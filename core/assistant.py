"""AIサポートの中身。

外部サービスへは接続せず、打たれた言葉から「何を聞かれているか」を判定して、
台帳の判定関数を呼び、その結果をそのまま言葉にする。

設計上の約束:

1. **書き込まない。** 読むだけ、指すだけ。登録も記録も、人が画面のボタンを押す。
2. **答えは必ず台帳の関数から作る。** 文章を組み立てるのではなく、
   build_rows や submission_check の結果を言葉にする。独自に文章を作ると、
   画面が「3人」と言っているのに「4人」と答える、という食い違いが起きる。
   同じ関数から作れば食い違いようがない。
3. **分からないときは分からないと言う。** 一番近そうな答えを推測して返さない。
   台帳の判定と食い違う答えを出すのが最悪なので、該当しなければそう言い、
   答えられることの一覧を出す。

このモジュールは Streamlit を import しない。判定の正しさを画面抜きで検証できる。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from core.models import Ledger, Subject
from core.schedule import add_months
from core.review import (
    Row,
    build_rows,
    submission_check,
    summarize_by_subject,
    unrecorded_subjects,
)

__all__ = ["Answer", "answer", "CAPABILITIES"]


@dataclass(frozen=True)
class Answer:
    """AIサポートの返事。

    highlight は画面上で光らせる要素のキー。文章だけでは行き先が分からない
    利用者のために、押す先そのものを示す。指すだけで、押しはしない。
    """

    kind: str                      # "guide" | "query" | "unknown"
    headline: str
    lines: list[str] = field(default_factory=list)
    highlight: str | None = None
    rows: list[Row] = field(default_factory=list)
    subjects: list[Subject] = field(default_factory=list)


CAPABILITIES = [
    "期限が切れているものを教えて",
    "30日以内に切れるものは？",
    "資格情報が無い人は？",
    "2027年3月31日に提出したら何が引っかかる？",
    "迫田 和樹さんは大丈夫？",
    "社員を登録したい",
    "資格を追加したい",
    "実施を記録したい",
    "提出前チェックをしたい",
    "点検の周期を変えたい",
]


def _has(text: str, *words: str) -> bool:
    return any(w in text for w in words)


def _looks_like_year_less_date(text: str) -> bool:
    """「8月1日」のように、年の無い日付が書かれているか。"""
    if re.search(r"\d{4}\s*年", text):
        return False
    return bool(re.search(r"\d{1,2}\s*月\s*\d{1,2}\s*日", text))


def _parse_date(text: str, today: date) -> date | None:
    """文中の日付を拾う。拾えなければ None。

    年の無い日付から年を補わない。「8月1日」が今年なのか来年なのかは
    書いた人にしか分からず、外すと提出日を1年ずらして判定することになる。

    「Nか月後」は add_months で数える。台帳の周期計算が月単位で月末を
    クランプしているのに、ここだけ30日で数えると、同じ「1か月」が
    画面と AI で違う日を指すことになる。
    """
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            # 2026年2月30日 のような日付。落とさず「拾えなかった」として扱う。
            return None

    m = re.search(r"(\d{1,3})\s*日後", text)
    if m:
        return today + timedelta(days=int(m.group(1)))

    m = re.search(r"(\d{1,2})\s*(?:か月|ヶ月|カ月)後", text)
    if m:
        return add_months(today, int(m.group(1)))

    return None


def _asked_kind(text: str) -> str | None:
    """社員のことを聞いているのか、道具のことを聞いているのか。

    どちらとも取れない場合は None を返し、両方を対象にする。
    決めつけて片方だけ返すと、聞かれていないものを見落とす。
    """
    if _has(text, "道具", "機器", "設備", "点検情報", "備品"):
        return "asset"
    if _has(text, "人", "社員", "従業員", "資格情報", "職員"):
        return "person"
    return None


def _find_subjects(ledger: Ledger, text: str) -> list[Subject]:
    """名前や管理番号に当てはまる対象をすべて返す。

    1 人に絞り込まない。同姓同名がいるときに片方を選ぶと、
    期限切れの方を黙って外して「問題ありません」と答えうる。
    画面側は ID で扱うよう直したのに、ここだけ名前で当てていた。

    長い手がかりで当たったものを優先する。「東 亮」と「東」が両方いるとき、
    「東 亮さんは？」で両方返すと毎回聞き返すことになる。
    """
    hits: dict[str, tuple[int, Subject]] = {}
    for s in ledger.subjects:
        for key in (s.code, s.name, s.name.replace(" ", "")):
            if key and key in text:
                length = len(key)
                if s.id not in hits or length > hits[s.id][0]:
                    hits[s.id] = (length, s)
    if not hits:
        return []
    best = max(length for length, _ in hits.values())
    return [s for length, s in hits.values() if length == best]


def answer(
    ledger: Ledger,
    question: str,
    today: date,
    *,
    normalizer=None,
) -> Answer:
    """打たれた文から、答えを組み立てる。

    normalizer を渡すと、こちらで判定できなかったときに一度だけ言い換えを頼む。
    言い換えても分からなければ、そのまま「答えられません」を返す。
    言い換え役に答えそのものを作らせないのは、台帳の判定と食い違わせないため。
    """
    text = question.strip()
    if not text:
        return Answer(
            kind="unknown",
            headline="聞きたいことを入力してください。",
            lines=["下の例のように書くと答えられます。"],
        )

    # --- 案内系：行き先を示す -------------------------------------------
    # 実際に押す先を光らせる。文章だけだと、どこにあるのか分からない。

    if _has(text, "社員", "人", "従業員") and _has(text, "登録", "追加", "入れ"):
        if _has(text, "資格", "講習", "健診"):
            return Answer(
                kind="guide",
                headline="資格・講習・健診は、その社員の画面から追加します。",
                lines=[
                    "「社員の資格・健診」で対象の人のカードを押す",
                    "「＋ 資格・講習・健診を追加する」を押す",
                    "種類を選び、前回の実施日が分かれば入れて「追加する」",
                ],
                highlight="nav",
            )
        return Answer(
            kind="guide",
            headline="左の「社員を登録」から登録できます。",
            lines=[
                "氏名とふりがなは必須です",
                "社員番号は空欄でも登録できます（自動で採番します）",
                "登録するとその人の画面へ移るので、続けて資格を追加してください",
            ],
            highlight="btn-add-subject",
        )

    if _has(text, "道具", "機器", "工具", "測定器") and _has(text, "登録", "追加"):
        return Answer(
            kind="guide",
            headline="左のメニューで「道具・機器の点検」を選ぶと、登録ボタンが出ます。",
            lines=[
                "メニューを「道具・機器の点検」に切り替える",
                "左の「道具・機器を登録」を押す",
                "管理番号は空欄でも登録できます（仮の番号を割り当てます）",
            ],
            highlight="nav",
        )

    if _has(text, "資格", "講習", "健診", "点検", "校正") and _has(text, "追加"):
        return Answer(
            kind="guide",
            headline="対象を開いてから追加します。",
            lines=[
                "一覧で対象のカードを押して詳細を開く",
                "「＋ 資格・講習・健診を追加する」（道具なら「＋ 点検・校正を追加する」）を押す",
                "種類を選んで「追加する」",
            ],
            highlight="nav",
        )

    if _has(text, "記録", "受講した", "受けた", "実施した", "終わった", "済んだ"):
        return Answer(
            kind="guide",
            headline="実施の記録は、その項目の行から入れます。",
            lines=[
                "対象のカードを押して詳細を開く",
                "表の右にある「記録」を押す",
                "実施した日を入れて「記録する」。次回の期限が計算し直されます",
            ],
            highlight="nav",
        )

    if _has(text, "提出", "安全書類", "グリーンファイル") and _has(
        text, "チェック", "確認", "したい", "やり方", "方法"
    ) and _parse_date(text, today) is None:
        return Answer(
            kind="guide",
            headline="左のメニューの「安全書類の提出前チェック」です。",
            lines=[
                "提出する日を入れます（作る日ではありません）",
                "その日に切れるもの、今日すでに切れているものが分かれて出ます",
                "記録が1件も無い人も、判断できないものとして出ます",
            ],
            highlight="nav",
        )

    if _has(text, "周期", "しきい値", "何日前", "設定", "変えたい", "変更"):
        return Answer(
            kind="guide",
            headline="左のメニューの「種類の設定」で変えられます。",
            lines=[
                "周期（何か月ごとか）を種類ごとに変えられます",
                "「期限間近」とする日数も変えられます",
                "変えた値はすぐに一覧と提出前チェックに反映されます",
            ],
            highlight="nav",
        )

    # --- 照会系：台帳に聞く ---------------------------------------------
    # 答えはすべて台帳の関数から作る。文章を組み立てない。

    if _looks_like_year_less_date(text) and _has(text, "提出", "工期", "出す", "出したら"):
        return Answer(
            kind="unknown",
            headline="年が分からないので、判定できません。",
            lines=[
                "「2027年3月31日」のように、年を付けて入力してください。",
                "年を推測すると、提出日を1年ずらして判定してしまうためです。",
            ],
        )

    target_date = _parse_date(text, today)
    if target_date is not None and _has(text, "提出", "工期", "出す", "出したら"):
        issues = submission_check(ledger, target_date=target_date)
        blank = unrecorded_subjects(ledger)
        if not issues and not blank:
            return Answer(
                kind="query",
                headline=f"{target_date} に提出しても、引っかかるものはありません。",
            )
        return Answer(
            kind="query",
            headline=(
                f"{target_date} に提出すると、{len(issues) + len(blank)}件が"
                "引っかかります。"
            ),
            lines=["「安全書類の提出前チェック」で同じ結果を画面でも確認できます。"],
            rows=issues,
            subjects=blank,
        )

    found = _find_subjects(ledger, text)
    if len(found) > 1:
        # どちらのことか決めない。片方を選ぶと、期限切れの方を外して
        # 「問題ありません」と答えることになる。
        return Answer(
            kind="unknown",
            headline=f"「{found[0].name}」は {len(found)}人います。どちらか分かりません。",
            lines=[
                "社員番号か所属を付けて、もう一度聞いてください。",
                *[f"・{s.name}（{s.code or '番号なし'}／{s.site or '所属なし'}）"
                  for s in found],
            ],
        )
    if found:
        subject = found[0]
        summaries = {s.subject.id: s for s in summarize_by_subject(ledger, today)}
        s = summaries[subject.id]
        if s.worst == "unregistered":
            return Answer(
                kind="query",
                headline=f"{subject.name} には記録が1件もありません。",
                lines=[
                    "問題が無いのではなく、何も分かっていない状態です。",
                    "書類に載せてよいかどうかは判断できません。",
                ],
                subjects=[subject],
            )
        acting = [r for r in s.rows if r.blocks_assignment or r.status == "due_soon"]
        if not acting:
            return Answer(
                kind="query",
                headline=f"{subject.name} に、いま対応が必要なものはありません。",
            )
        return Answer(
            kind="query",
            headline=f"{subject.name} は {len(acting)}件、対応が必要です。",
            rows=acting,
        )

    if _has(text, "資格情報", "点検情報", "登録されていない", "何も無い", "未登録"):
        # 「人は？」と聞かれて道具を混ぜない。件数がそのまま食い違う。
        kind = _asked_kind(text)
        blank = unrecorded_subjects(ledger, kind=kind)
        what = {"person": "社員", "asset": "道具・機器", None: "対象"}[kind]
        return Answer(
            kind="query",
            headline=f"記録が1件も無い{what}は {len(blank)}件です。"
            if blank
            else f"記録が1件も無い{what}はありません。",
            subjects=blank,
        )

    if _has(text, "切れ", "超過", "期限切れ", "過ぎ"):
        rows = [r for r in build_rows(ledger, today) if r.status == "overdue"]
        return Answer(
            kind="query",
            headline=f"期限が切れているものが {len(rows)}件あります。"
            if rows
            else "期限が切れているものはありません。",
            rows=rows,
        )

    days = None
    m = re.search(r"(\d{1,3})\s*日", text)
    if m and _has(text, "以内", "うち", "近い", "間近", "先"):
        days = int(m.group(1))
    elif _has(text, "間近", "近い", "もうすぐ", "今月"):
        days = ledger.soon_days

    if days is not None:
        limit = today + timedelta(days=days)
        rows = [
            r
            for r in build_rows(ledger, today)
            if r.due_on is not None and today <= r.due_on <= limit
        ]
        return Answer(
            kind="query",
            headline=f"{days}日以内に期限が来るものが {len(rows)}件あります。"
            if rows
            else f"{days}日以内に期限が来るものはありません。",
            rows=rows,
        )

    if _has(text, "日付未入力", "未確定", "分からない", "計算できない"):
        rows = [r for r in build_rows(ledger, today) if r.blocks_assignment
                and r.due_on is None]
        # 何を入れれば確定するかは、期限の決まり方で違う。
        # 周期型は前回実施日、固定型は証に書かれた有効期限。
        # まとめて「前回の日付」と言うと、免許証に前回実施日を探させる。
        cycle = [r for r in rows if r.requirement.date_mode == "cycle"]
        fixed = [r for r in rows if r.requirement.date_mode == "fixed"]
        lines = []
        if cycle:
            lines.append(f"前回の実施日が未入力：{len(cycle)}件")
        if fixed:
            lines.append(f"証に書かれた有効期限が未入力：{len(fixed)}件")
        return Answer(
            kind="query",
            headline=f"期限を確定するための情報が入っていないものが {len(rows)}件あります。"
            if rows
            else "期限を確定するための情報が入っていないものはありません。",
            lines=lines,
            rows=rows,
        )

    # --- 分からないとき --------------------------------------------------
    # 一番近そうな答えを推測して返さない。台帳の判定と食い違う答えを出すのが最悪。

    if normalizer is not None:
        canonical = normalizer(text)
        if canonical:
            # 言い換えた文でもう一度だけ判定する。無限に回さないよう、
            # ここでは normalizer を渡さない。
            again = answer(ledger, canonical, today)
            if again.kind != "unknown":
                return Answer(
                    kind=again.kind,
                    headline=again.headline,
                    lines=[f"（「{canonical}」として受け取りました）"] + again.lines,
                    highlight=again.highlight,
                    rows=again.rows,
                    subjects=again.subjects,
                )

    return Answer(
        kind="unknown",
        headline="すみません、その聞き方には答えられません。",
        lines=[
            "近いものを推測して答えると、画面の表示と食い違う恐れがあるため、",
            "答えられないときはそう申し上げています。",
            "次のような聞き方であれば答えられます。",
        ],
    )
