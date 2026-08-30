"""期限台帳。

画面はドメイン層（core/）の薄い皮として作る。期日の決め方と判定はすべて core 側にあり、
この層には計算を置かない。

画面の決まりごと:
  * 折りたたまない。押す先はすべて最初から見えている状態にする。
  * 状態を色だけで表さない。色の隣に必ず言葉を置く。
  * 危険なものほど上・左に来る。並びは 期限切れ → 日付未入力 → 資格情報なし → 期限間近。
  * 期日を計算できないものを「問題なし」に見せない。
  * まだ作っていない操作は、押せる状態で置かない。無効にして理由を書く。
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

import streamlit as st

from core.models import (
    LedgerDataError,
    CATEGORY_LABEL,
    DATE_MODE_LABEL,
    OBLIGATION_LABEL,
    Holding,
    Ledger,
    Record,
    Requirement,
    Subject,
)
from core.review import (
    STATUS_ORDER,
    UNREGISTERED,
    Row,
    SubjectSummary,
    build_rows,
    submission_check,
    summarize_by_subject,
    unrecorded_subjects,
)
from core.assistant import CAPABILITIES
from core.assistant import answer as assistant_answer
from core.schedule import ScheduleError, add_months, validate_done_on
from core.store import SEED_PATH, load_ledger

import ui

st.set_page_config(page_title="期限台帳", page_icon="📋", layout="wide")
ui.inject()

CARDS_PER_ROW = 3

PAGE_PEOPLE = "社員の資格・健診"
PAGE_ASSETS = "道具・機器の点検"
PAGE_SUBMISSION = "安全書類の提出前チェック"
PAGE_TYPES = "種類の設定"
PAGE_AI = "AIサポート"

SECTIONS: list[tuple[str, str, str]] = [
    ("overdue", "期限切れ", "期限を過ぎています"),
    ("unknown", "日付未入力・期限計算不可", "前回日が未入力で計算できません"),
    ("unregistered", "資格情報なし", "まだ何も登録されていません"),
    # 日数は設定で変えられるので、表示するときに埋める。
    ("due_soon", "期限間近（{soon}日以内）", "{soon}日以内に期限が来ます"),
]

# 「予告」は 3 色に畳んだので、問題なしとして扱う。
PILL_STATE: dict[str, str] = {
    "overdue": "overdue",
    "unknown": "unknown",
    "unregistered": "unregistered",
    "due_soon": "due_soon",
    "upcoming": "ok",
    "ok": "ok",
    # 期限が存在しないものは、判定側で no_deadline になる。
    "no_deadline": "none",
}

NOT_BUILT = "この機能はまだ作っていません"


def fill_days(text: str) -> str:
    """見出しや説明の中の日数を、いま設定されている値で埋める。

    コードに 30 を書き込むと、設定で 90 日に変えても表示だけ 30 のままになり、
    画面が嘘をつく。
    """
    return text.replace("{soon}", str(lg.soon_days))


def pill_key(state: str, is_asset: bool = False) -> str:
    """ピルやタイルに使う配色のキー。

    「資格情報なし」は人の言い方で、道具の画面では「点検情報なし」と出したい。
    状態そのものは同じなので、表示のときだけ切り替える。
    """
    key = PILL_STATE[state]
    if key == "unregistered" and is_asset:
        return "unregistered_asset"
    return key


# --- 共通の小物 -----------------------------------------------------------


def jp_date(d: date) -> str:
    """2026年7月31日 の形。利用者が読み慣れた並びにする。"""
    return f"{d.year}年{d.month}月{d.day}日"


def remaining_text(days: int | None) -> str:
    if days is None:
        return ""
    if days < 0:
        return f"（{-days}日 超過）"
    return f"（あと{days}日）"


def ledger() -> Ledger:
    if "ledger" not in st.session_state:
        st.session_state.ledger = load_ledger(SEED_PATH)
    return st.session_state.ledger


def reason_text(row: Row) -> str:
    """なぜ書類が通らないのかを、そのまま読める文にする。"""
    if row.due_on is None:
        if row.requirement.date_mode == "fixed":
            return "証に記載された有効期限が入っていないため、期限が分かりません"
        return "前回の日付が入っていないため、期限が分かりません"
    return f"{jp_date(row.due_on)} に期限が切れます"


# --- 前の操作の後始末 -----------------------------------------------------
# ウィジェットが作られたあとに session_state を書き換えられないため、
# ボタンによる要求は、次の実行の先頭でここに反映する。

_show_all_key = st.session_state.pop("_request_show_all", None)
if _show_all_key:
    st.session_state[_show_all_key] = "全員"

# 記録直後に画面を作り直すため、その場で出したメッセージは消えてしまう。
# 次の実行に持ち越して表示する。何も言われないと、記録できたのか分からない。
_flash = st.session_state.pop("_flash", None)

# AIサポートの例を押したときも同じ。入力欄はキーを持っているので value では
# 入らない。次の実行の先頭で、入力欄の値そのものを書き換える。
# メニューもウィジェットなので、作られたあとには書き換えられない。
_request_nav = st.session_state.pop("_request_nav", None)
if _request_nav is not None:
    st.session_state["nav"] = _request_nav

_ai_example = st.session_state.pop("_ai_example", None)
if _ai_example is not None:
    st.session_state["ai-question"] = _ai_example

# 検索して見つからなかった言葉を、登録画面の名前欄へ引き継ぐ。
# 引き継がないと、いま打ったばかりの名前をもう一度打つことになる。
_prefill_name = st.session_state.pop("_prefill_name", None)
if _prefill_name is not None:
    key = "reg-a-name" if st.session_state.get("registering") == "asset" else "reg-name"
    st.session_state[key] = _prefill_name


# --- 左サイドバー ---------------------------------------------------------

with st.sidebar:
    st.markdown("### 期限台帳")
    st.caption("資格・講習・健診・点検の期限を管理します")

    st.markdown("**メニュー**")
    nav = st.radio(
        "メニュー",
        [PAGE_PEOPLE, PAGE_ASSETS, PAGE_SUBMISSION, PAGE_TYPES, PAGE_AI],
        label_visibility="collapsed",
        key="nav",
    )

    # 登録の入口は、いま見ている画面に合わせて言葉を変える。
    # 道具の画面で「社員を登録」と出ていると、押す先を間違える。
    if nav == PAGE_ASSETS:
        add_labels = ("道具・機器を登録", "点検の記録を追加")
    else:
        add_labels = ("社員を登録", "社員に資格・講習を追加")

    st.markdown("**登録**")
    if st.button(add_labels[0], width="stretch", key="btn-add-subject"):
        # 登録画面は「社員の資格・健診」「道具・機器の点検」の下にある。
        # メニューを移さずに登録状態だけ立てると、押しても何も起きず、
        # あとで一覧へ来たときに突然登録画面が開く。メニューも一緒に移す。
        kind = "asset" if nav == PAGE_ASSETS else "person"
        st.session_state["registering"] = kind
        st.session_state["_request_nav"] = PAGE_ASSETS if kind == "asset" else PAGE_PEOPLE
        st.session_state.pop("selected", None)
        st.session_state.pop("selected_holding", None)
        st.rerun()
    st.caption(
        "既存の道具への点検の追加は、その道具を開いてできます。"
        if nav == PAGE_ASSETS
        else "既存の社員への資格の追加は、その人を開いてできます。"
    )

    st.divider()
    st.caption("**使い方で迷ったら**\n\n不明な点は事務所までお問い合わせください。")


lg = ledger()
today = date.today()


# --- 社員の資格・健診 -----------------------------------------------------


def draw_card(summary: SubjectSummary, slot) -> None:
    """カード 1 枚。状態を作り出している資格・点検そのものを出す。"""
    subject = summary.subject
    is_asset = subject.kind == "asset"

    # 見出しには「期限間近（30日以内）」と出すが、カードの中は幅が狭いので短い方を使う。
    # キーを付けると st-key-... の class が振られ、状態ごとの色帯を当てられる。
    key = pill_key(summary.worst, is_asset)
    with slot.container(border=True, key=f"card-{PILL_STATE[summary.worst]}-{subject.id}"):
        st.markdown(ui.bar(key), unsafe_allow_html=True)
        st.markdown(f"##### {subject.name}")
        if is_asset:
            # 道具は名前では特定できない。「絶縁手袋」は何組もあるため、
            # 現物にたどり着くには管理番号が要る。型番は校正や修理を頼むときに要る。
            st.caption(f"管理番号：{subject.code}　／　{subject.site}")
            if subject.model:
                st.caption(f"型番：{subject.model}")
        else:
            st.caption(f"{subject.site} ／ {subject.role}")
        st.markdown(ui.pill(key, short=True), unsafe_allow_html=True)

        cause = summary.cause
        if summary.worst == UNREGISTERED:
            # 「資格情報なし」なのに「対応が必要な項目はありません」と出ていた。
            # 記録が1件も無いことは、問題が無いことではない。
            # 一覧では要対応として並べているのに、カードの中で打ち消していた。
            st.write(
                "資格・講習・健診が1件も登録されていません。"
                if not is_asset
                else "点検の項目が1件も登録されていません。"
            )
        elif cause is None:
            st.write("対応が必要な項目はありません")
        else:
            st.markdown(f"**{cause.requirement.name}**")
            if cause.due_on is None:
                st.write(missing_input_text(cause))
            else:
                st.write(
                    f"期限：{jp_date(cause.due_on)} {remaining_text(cause.days_left)}"
                )

        if summary.worst != UNREGISTERED:
            st.caption(f"ほかに対応が必要な項目：{summary.other_action_count}件")
        if st.button(
            "点検の記録を見る" if is_asset else "資格・健診を確認",
            key=f"open-{subject.id}",
            width="stretch",
        ):
            st.session_state["selected"] = subject.id
            st.session_state.pop("recording", None)
            st.session_state.pop("selected_holding", None)
            st.rerun()


def draw_grid(items: list[SubjectSummary]) -> None:
    for start in range(0, len(items), CARDS_PER_ROW):
        slots = st.columns(CARDS_PER_ROW)
        for summary, slot in zip(items[start : start + CARDS_PER_ROW], slots):
            draw_card(summary, slot)


def state_label(row: Row) -> str:
    """行の状態を、色と言葉の両方で表す。

    有効期限が存在しない資格は「未確定」ではない。分かっていないのではなく、
    そもそも期限が無い。混ぜると、本当に日付が入っていない行が埋もれる。
    """
    return ui.pill(PILL_STATE[row.status], short=True)


def missing_input_text(row: Row) -> str:
    """期日が出せないときに、何を入れればよいかを書く。

    due_on が None になる理由は 3 つあり、必要な入力が違う。
    ひとまとめに「前回の日付を入力してください」と出すと、
    証面の期限で管理する項目に対して見当違いの案内になる。
    この判断はここ 1 箇所に集約し、各画面はこれを呼ぶ。
    """
    mode = row.requirement.date_mode
    if mode == "none":
        return "有効期限の定めなし"
    if mode == "fixed":
        return "証に記載された有効期限を入力してください"
    return "前回の実施日を入力してください"


def deadline_text(row: Row) -> str:
    """期限、または期限が出せない理由。"""
    if not row.requirement.has_deadline:
        return "有効期限の定めなし"
    if row.due_on is not None:
        return f"期限：{jp_date(row.due_on)}"
    if row.requirement.date_mode == "fixed":
        return "有効期限：未入力"
    last = row.holding.last_done_on
    if last is None:
        return "前回実施日：未入力"
    return f"前回実施日：{jp_date(last)}"


def situation_text(row: Row) -> str:
    """残り日数、または次にやること。"""
    if not row.requirement.has_deadline:
        return "期限の定めなし"
    if row.due_on is None:
        return missing_input_text(row)
    days = row.days_left or 0
    return f"{-days}日超過" if days < 0 else f"あと{days}日"


def sort_for_detail(rows: list[Row]) -> list[Row]:
    """詳細の表の並び。危ないものほど上。期限を持たないものは最後。

    build_rows は期日の無い行を先頭に置く（放置されやすいため）が、
    有効期限が存在しない資格まで先頭に来ると、本当に危ない行が下へ押し下げられる。
    """
    def key(row: Row) -> tuple[int, int, date]:
        if not row.requirement.has_deadline:
            return (1, 0, date.max)
        return (0, STATUS_ORDER[row.status], row.due_on or date.min)

    return sorted(rows, key=key)


def count_states(rows: list[Row]) -> dict[str, int]:
    """1 人（1 台）の中での状態別件数。"""
    counts = {"overdue": 0, "unknown": 0, "due_soon": 0, "ok": 0, "no_deadline": 0}
    for row in rows:
        # 判定側が no_deadline を返すので、画面で has_deadline を見直す必要はない。
        if row.status in counts:
            counts[row.status] += 1
        else:
            counts["ok"] += 1
    return counts


# (count_states のキー, ui.STATE_STYLE のキー, 説明)
DETAIL_TILES = [
    ("overdue", "overdue", "すぐに対応が必要です"),
    ("unknown", "unknown", "日付の入力が必要です"),
    ("due_soon", "due_soon", "期限が近づいています"),
    ("ok", "ok", "期限内です"),
    ("no_deadline", "none", "有効期限の定めなし"),
]


def draw_delete_block(subject, is_asset: bool) -> None:
    """対象を台帳から消す。

    取り返しがつかない操作なので、押した直後には消さない。
    何が一緒に消えるのかを数えて見せ、もう一度押させる。
    記録も一緒に消えることを、数字で示してから確認する。
    """
    what = "道具・機器" if is_asset else "社員"
    holdings = [h for h in lg.holdings if h.subject_id == subject.id]
    records = sum(len(h.records) for h in holdings)

    st.divider()

    if st.session_state.get("deleting") != subject.id:
        left, _ = st.columns([1, 3])
        if left.button(
            f"この{what}を台帳から消す",
            key="btn-delete-open",
            width="stretch",
        ):
            st.session_state["deleting"] = subject.id
            st.rerun()
        st.caption(
            "退職や廃棄で台帳から外す場合に使います。押すとすぐには消えず、"
            "何が一緒に消えるかを確認してから実行します。"
        )
        return

    with st.container(border=True):
        st.error(
            f"「{subject.name}」を台帳から消します。取り消せません。",
            icon="⚠️",
        )
        st.write("一緒に消えるもの")
        st.write(f"　・登録されている項目　{len(holdings)}件")
        st.write(f"　・実施・点検の記録　　{records}件")
        if records:
            st.caption(
                "記録は追記のみで残してきたものです。消すと履歴もたどれなくなります。"
                "退職者の記録を残しておきたい場合は、消さずに置いておくこともできます。"
            )

        do, cancel = st.columns([1, 2])
        if do.button("消す", type="primary", width="stretch", key="btn-delete-do"):
            lg.holdings = [h for h in lg.holdings if h.subject_id != subject.id]
            lg.subjects = [s for s in lg.subjects if s.id != subject.id]
            for key in ("deleting", "selected", "selected_holding", "editing"):
                st.session_state.pop(key, None)
            st.session_state["_flash"] = (
                f"「{subject.name}」を台帳から消しました。"
                f"（項目 {len(holdings)}件、記録 {records}件も一緒に消えました）"
            )
            st.rerun()

        if cancel.button("やめる", width="stretch", key="btn-delete-cancel"):
            st.session_state.pop("deleting", None)
            st.rerun()


def draw_detail(kind: str) -> None:
    """選んだ 1 件の詳細。一覧とは別の画面として出す。"""
    is_asset = kind == "asset"
    selected = lg.subject(st.session_state["selected"])

    back, edit = st.columns([3, 1])
    if back.button(
        "← 一覧に戻る（道具・機器一覧へ）" if is_asset else "← 一覧に戻る（社員一覧へ）",
        key="btn-back",
    ):
        st.session_state.pop("selected", None)
        st.session_state.pop("selected_holding", None)
        st.rerun()
    if edit.button(
        "この道具の情報を編集する" if is_asset else "この人の情報を編集する",
        key="btn-edit-subject",
        width="stretch",
    ):
        st.session_state["editing"] = selected.id
        st.rerun()

    if _flash:
        st.success(_flash, icon="✅")

    rows = sort_for_detail(
        [r for r in build_rows(lg, today) if r.subject.id == selected.id]
    )
    counts = count_states(rows)

    head, status = st.columns([1, 2])

    with head.container(border=True):
        title = selected.name
        if selected.kana:
            title += f"　:gray[（{selected.kana}）]"
        st.markdown(f"### {title}")
        if is_asset:
            st.write(f"管理番号　{selected.code}")
            if selected.model:
                st.write(f"型番　　　{selected.model}")
            st.write(f"保管場所　{selected.site}")
        else:
            st.write(f"社員番号　{selected.code}")
            st.write(f"所属　　　{selected.site} ／ {selected.role}")

    with status:
        st.markdown("**この人の状況**" if not is_asset else "**この道具の状況**")
        tiles = st.columns(len(DETAIL_TILES))
        for (key, style, note), slot in zip(DETAIL_TILES, tiles):
            slot.markdown(
                ui.tile(style, counts[key], note, "件"), unsafe_allow_html=True
            )

    # 記録が 1 件も無い対象は、一覧では「資格情報なし」として紫のカードになる。
    # 詳細で 0 が並ぶだけだと、問題が無いように見えてしまうので明示する。
    if not rows:
        st.markdown(ui.pill(pill_key("unregistered", is_asset)),
                    unsafe_allow_html=True)
        st.warning(
            "まだ何も登録されていません。問題が無いのではなく、"
            "資格・講習・健診を持っているかどうかが分かっていない状態です。"
            "下の「＋ 資格・講習・健診を追加する」から登録してください。"
            if kind == "person"
            else "まだ何も登録されていません。問題が無いのではなく、"
                 "点検や校正の状況が分かっていない状態です。",
            icon="⚠️",
        )

    # 最優先の 1 件。一覧のカードに出しているものと同じ行を、詳細でも先頭に出す。
    urgent = [r for r in rows if r.blocks_assignment or r.status == "due_soon"]
    if urgent:
        top = urgent[0]
        st.markdown("##### 最も優先して対応が必要なもの")
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([2, 2, 1.5, 1.5])
            c1.markdown(state_label(top), unsafe_allow_html=True)
            c1.markdown(f"**{top.requirement.name}**")
            c2.write(deadline_text(top))
            c3.write(situation_text(top))
            c4.write(
                f"次回予定日：{jp_date(top.holding.planned_on)}"
                if top.holding.planned_on
                else "次回予定日：未入力"
            )

    title, add = st.columns([3, 1])
    title.markdown(
        "##### 資格・講習・健診の一覧" if not is_asset else "##### 点検・校正の一覧"
    )
    if add.button(
        "＋ 点検・校正を追加する" if is_asset else "＋ 資格・講習・健診を追加する",
        key="btn-open-add",
        width="stretch",
    ):
        st.session_state["adding"] = selected.id
        st.rerun()

    st.caption(
        "登録されているすべての記録です。次回予定日の入力と、実施の記録ができます。"
    )

    draw_add_form(selected, rows, is_asset)

    widths = [1.1, 2.4, 1.6, 1.7, 1.3, 1.4, 0.9]
    header = st.columns(widths)
    for slot, label in zip(
        header,
        ["種別", "名称", "状態", "期限／前回実施日", "残り日数・状況", "次回予定日", "記録"],
    ):
        slot.caption(f"**{label}**")

    for row in rows:
        cols = st.columns(widths)
        cols[0].caption(OBLIGATION_LABEL[row.requirement.obligation])
        # 名称を押すと、その記録の詳細（履歴・根拠・添付）へ移る。
        if cols[1].button(
            row.requirement.name,
            key=f"to-holding-{row.holding.id}",
            type="tertiary",
            width="stretch",
        ):
            st.session_state["selected_holding"] = row.holding.id
            st.session_state.pop("recording", None)
            st.session_state.pop("adding", None)
            st.rerun()
        cols[2].markdown(state_label(row), unsafe_allow_html=True)
        cols[3].write(deadline_text(row))
        cols[4].write(situation_text(row))

        if not row.requirement.has_deadline:
            # 期限が無いものに次回予定日は無い。入力欄も出さない。
            cols[5].write("—")
            cols[6].write("—")
            continue

        planned = cols[5].date_input(
            "次回予定日",
            value=row.holding.planned_on,
            key=f"plan-{row.holding.id}",
            label_visibility="collapsed",
        )
        if planned != row.holding.planned_on:
            row.holding.planned_on = planned
            st.rerun()

        if row.requirement.date_mode == "cycle":
            if cols[6].button("記録", key=f"open-rec-{row.holding.id}", width="stretch"):
                st.session_state["recording"] = row.holding.id
                st.rerun()
        else:
            cols[6].write("—")

    draw_record_form(rows)

    st.divider()
    st.caption(
        "・「期限切れ」または「日付未入力・期限計算不可」の記録がある場合、"
        "安全書類を提出する前に対応してください。"
    )
    st.caption(
        "・「有効（期限なし）」は有効期限の定めがない資格・免状です（更新義務はありません）。"
    )
    st.caption(
        "・次回予定日は受講や点検の予約が取れている日です。期限の判定には使いません。"
        "予定を実績として扱うと、超過が隠れてしまうためです。"
    )

    draw_delete_block(selected, is_asset)


# 修了証などの画像を預かる機能。公開デモでは閉じておく。
#
# 修了証には氏名・生年月日・証番号が写る。社員の画面からは、期限の判定に使わない
# 個人情報（生年月日・連絡先など）を意図的に外している。そこへ画像として同じものを
# 入れ直さないよう、既定では受け付けない。実運用で開けるなら、アクセス制御と
# 保存期間の設計が別に要る。
ATTACHMENTS_ENABLED = False

# 証の写真から日付を読み取る機能。公開デモでは閉じておく。
#
# 読み取れなかった項目は空欄のままにする。推測で埋めると、人が埋まっている欄を
# そのまま通してしまい、目視確認が形だけになる。
# 読み取った値もそのままは保存せず、必ず人が確定させる。
PHOTO_READING_ENABLED = False


def build_certificate_reader():
    """読み取り役を選ぶ。既定はモックで、外部へはつながらない。

    本物（ClaudeCertificateReader）は未検証。API キーが無いため、実際に
    呼び出して動くことを確認していない。
    """
    from core.certificate_reader import (
        ClaudeCertificateReader,
        MockCertificateReader,
    )

    if AI_API_ENABLED:
        return ClaudeCertificateReader()
    return MockCertificateReader()


def next_actions(row: Row) -> list[str]:
    """状態から、次にやることを組み立てる。

    状態を出すだけでは何をすればよいか分からない。判定と同じ材料から作るので、
    画面の説明と判定が食い違うことがない。
    """
    if not row.requirement.has_deadline:
        return ["この資格に有効期限はありません。保有の記録として登録されています。"]

    if row.due_on is None:
        if row.requirement.date_mode == "fixed":
            return [
                "証の現物を確認する（本人の手元、または事務所の控え）",
                "下の「新しい有効期限を登録する」で期限を入力する",
                "状態が確定します",
            ]
        return [
            "前回の実施日を調べる（本人の手元の証、または事務所の記録）",
            "下の「実施を記録する」で日付を入力する",
            "期限が計算され、状態が確定します",
        ]

    if row.status == "overdue":
        if row.requirement.date_mode == "cycle":
            return [
                "再受講・再点検の申込みを行う",
                "実施後、修了証や記録を受け取る",
                "下の「実施を記録する」で日付を入力する",
                "次の予約が決まっていれば「次回予定日」を入力する",
            ]
        return [
            "更新の手続きを行う",
            "新しい証に記載された有効期限を登録する",
            "次の予定が決まっていれば「次回予定日」を入力する",
        ]

    if row.status == "due_soon":
        return [
            "受講・点検の予約を取る",
            "「次回予定日」に予約した日を入力する",
            "実施したら「実施を記録する」で日付を入力する",
        ]

    return ["いま対応は必要ありません。"]


def draw_holding_detail(kind: str) -> None:
    """1 件の資格・点検の詳細。人の詳細から名称を押して来る。"""
    is_asset = kind == "asset"
    holding_id = st.session_state["selected_holding"]

    row = next(
        (r for r in build_rows(lg, today) if r.holding.id == holding_id),
        None,
    )
    if row is None:
        st.session_state.pop("selected_holding", None)
        st.rerun()
        return

    subject, req, holding = row.subject, row.requirement, row.holding

    if st.button(f"← {subject.name} さんの詳細に戻る" if not is_asset
                 else f"← {subject.name} の詳細に戻る", key="btn-back-holding"):
        st.session_state.pop("selected_holding", None)
        st.rerun()

    if _flash:
        st.success(_flash, icon="✅")

    st.markdown(f"## {req.name}")
    st.markdown(state_label(row), unsafe_allow_html=True)
    st.caption(f"{subject.name}（{subject.code}）　{subject.site} ／ {subject.role}")

    left, right = st.columns([3, 2])

    # --- 左：この記録の中身 -------------------------------------------------
    with left.container(border=True):
        st.markdown("**この記録の内容**")
        items: list[tuple[str, str]] = [
            ("種別", CATEGORY_LABEL[req.category]),
            ("区分", OBLIGATION_LABEL[req.obligation]),
            ("期日の決まり方", DATE_MODE_LABEL[req.date_mode]),
        ]
        if req.cycle_months:
            items.append(("周期", f"{req.cycle_months}か月"))
        # 証面の期限で管理する記録に「前回実施日」は無い。空欄を出すと、
        # 入れ忘れているように見えてしまう。
        if req.date_mode == "cycle":
            items.append(
                ("前回実施日",
                 jp_date(holding.last_done_on) if holding.last_done_on else "未入力")
            )
        items.append(("有効期限", jp_date(row.due_on) if row.due_on else
                      ("定めなし" if not req.has_deadline else "計算できません")))
        items.append(("残り日数・状況", situation_text(row)))
        items.append(("次回予定日", jp_date(holding.planned_on) if holding.planned_on else "未入力"))
        items.append(("備考", holding.note or "—"))

        for label, value in items:
            a, b = st.columns([1, 2])
            a.caption(label)
            b.write(value)

    # --- 右：状態と次にやること ---------------------------------------------
    with right:
        with st.container(border=True):
            st.markdown("**現在の状態**")
            st.markdown(state_label(row), unsafe_allow_html=True)
            st.write(situation_text(row))
            if row.blocks_assignment:
                st.error(
                    f"この記録が原因で、{subject.name} さんを安全書類に記載できません。"
                    if not is_asset
                    else f"この記録が原因で、{subject.name} を使う作業の書類を出せません。",
                    icon="⚠️",
                )

        with st.container(border=True):
            st.markdown("**この記録の次にやること**")
            actions = next_actions(row)
            if len(actions) == 1:
                st.write(actions[0])
            else:
                # 1 行ずつ st.write すると箇条書きが毎回作り直され、
                # すべて「1.」から始まってしまう。ひとつの文字列にまとめて渡す。
                lines = [f"{i}. {a}" for i, a in enumerate(actions, start=1)]
                st.markdown("\n".join(lines))

    # --- 履歴 ---------------------------------------------------------------
    is_fixed = req.date_mode == "fixed"
    # 訂正されたものは残すが、回数には数えない。数えると
    # 「3回受けた」と見えるのに有効な記録は2回、という表示になる。
    live = holding.effective_records()
    st.markdown(
        ui.section("有効期限の登録の履歴" if is_fixed else "受講・実施の履歴",
                   len(live), "回"),
        unsafe_allow_html=True)
    if not holding.records:
        st.info(
            "まだ記録がありません。下の「新しい有効期限を登録する」から入力してください。"
            if is_fixed
            else "まだ記録がありません。下の「実施を記録する」から入力してください。"
        )
    else:
        st.caption(
            "記録は追記のみで、書き換えません。過去の回もそのまま残ります。"
            "日付を間違えた場合は、その回を開いて訂正してください。"
        )
        widths = [0.7, 1.5, 1.5, 1.1, 1.8, 0.9, 1.0]
        header = st.columns(widths)
        for slot, label in zip(
            header,
            ["回数",
             "受け取った日" if is_fixed else "実施日",
             "有効期限" if is_fixed else "この回による期限",
             "実施者", "備考", "添付", ""],
        ):
            slot.caption(f"**{label}**")

        # 記録は追記のみ。古い順に並べ、何回目かを示す。
        ordered = sorted(holding.records, key=lambda r: r.done_on)
        superseded = holding.superseded_ids
        for i, rec in enumerate(ordered, start=1):
            cols = st.columns(widths)
            done = rec.id in superseded
            cols[0].write(f"~~{i}回目~~" if done else f"{i}回目")
            cols[1].write(jp_date(rec.done_on) + ("（訂正済み）" if done else ""))
            if rec.is_expiry_update:
                cols[2].write(jp_date(rec.expiry_on))
            elif req.date_mode == "cycle" and req.cycle_months:
                cols[2].write(jp_date(add_months(rec.done_on, req.cycle_months)))
            else:
                cols[2].write("—")
            cols[3].write(rec.done_by or "—")
            cols[4].write(rec.memo or "—")
            cols[5].write(f"{len(rec.attachments)}件" if rec.attachments else "—")
            # 記録そのものの id で指す。日付で作ると、同じ日に訂正した回と
            # 元の回が同じ鍵になり、別の回が開く。
            if cols[6].button("この回を見る", key=f"see-{rec.id}", width="stretch"):
                st.session_state["seen_record"] = rec.id
                st.rerun()

        draw_record_detail(holding, req, ordered)

    # --- 入力 ---------------------------------------------------------------
    plan, record = st.columns(2)

    with plan.container(border=True):
        st.markdown("**次回予定日**")
        st.caption(
            "受講や点検の予約が決まっている場合に入力します。"
            "期限の判定には使いません。"
        )
        if not req.has_deadline:
            st.write("この記録に期限はないため、予定日はありません。")
        else:
            planned = st.date_input(
                "次回予定日",
                value=holding.planned_on,
                key=f"detail-plan-{holding.id}",
                label_visibility="collapsed",
            )
            if planned != holding.planned_on:
                holding.planned_on = planned
                st.rerun()

    with record.container(border=True):
        if req.date_mode == "fixed":
            # 更新して新しい証を受け取ったとき、ここから期限を入れ替える。
            # これが無いと、期限切れの資格を更新しても台帳が切れたままになる。
            st.markdown("**新しい有効期限を登録する**")
            st.caption(
                "更新して新しい証を受け取ったら、そこに書かれている期限を入れてください。"
            )
            current = holding.expiry_on_at(today)
            c1, c2 = st.columns(2)
            new_due = c1.date_input(
                "新しい有効期限",
                value=current,
                key=f"detail-newdue-{holding.id}",
            )
            # いつ受け取ったかを持たないと、更新した瞬間に過去の判定まで
            # 新しい期限で塗り替わり、「その時点では切れていた」という事実が
            # 台帳から消える。更新は上書きではなく、積む。
            got_on = c2.date_input(
                "受け取った日",
                value=today,
                key=f"detail-goton-{holding.id}",
            )
            st.caption(
                "「受け取った日」より前の判定には、いまの期限が使われ続けます。"
                "過去に提出した書類の判定を、あとから変えないためです。"
            )
            if st.button(
                "この期限を登録する",
                type="primary",
                width="stretch",
                key="btn-detail-renew",
            ):
                if new_due == current:
                    st.info("いまの期限と同じです。変更はありません。")
                else:
                    try:
                        validate_done_on(got_on, today)
                    except ScheduleError as e:
                        st.error(str(e), icon="⚠️")
                    else:
                        holding.add_record(Record(
                            done_on=got_on,
                            expiry_on=new_due,
                            memo="新しい有効期限を登録",
                        ))
                        holding.planned_on = None
                        st.session_state["_flash"] = (
                            f"{req.name} の有効期限を "
                            f"{jp_date(current) if current else '未入力'} から "
                            f"{jp_date(new_due)} に更新しました"
                            f"（{jp_date(got_on)} 受け取り）。"
                        )
                        st.rerun()
        elif req.date_mode == "none":
            st.markdown("**実施を記録する**")
            st.caption("この記録に期限はありません。保有の記録として登録されています。")
        else:
            st.markdown("**実施を記録する**")
            c1, c2 = st.columns(2)
            done_on = c1.date_input("実施した日", value=today, key=f"detail-done-{holding.id}")
            done_by = c2.text_input("実施者", value="", key=f"detail-by-{holding.id}")
            if st.button("記録する", type="primary", width="stretch", key="btn-detail-record"):
                try:
                    validate_done_on(done_on, today)
                except ScheduleError as e:
                    st.error(str(e))
                else:
                    holding.add_record(Record(done_on=done_on, done_by=done_by))
                    holding.planned_on = None
                    st.session_state["_flash"] = (
                        f"{req.name} に {jp_date(done_on)} の実施を記録しました。"
                        "次回の期限を計算し直しました。"
                    )
                    st.rerun()

    # --- 根拠と説明 ---------------------------------------------------------
    with st.container(border=True):
        st.markdown("**この記録の根拠**")
        st.write(req.source or "—")
        if req.note:
            st.markdown("**補足**")
            st.write(req.note)


def draw_record_detail(holding, req, ordered: list) -> None:
    """選んだ回の中身。開閉ではなく、常にここにある領域が入れ替わる。

    添付は回ごとに持つ。2021年の修了証と2026年の修了証が同じ場所に混ざると、
    どの回の控えなのか分からなくなるため。
    """
    seen = st.session_state.get("seen_record")
    target = None
    for i, rec in enumerate(ordered, start=1):
        if rec.id == seen:
            target, number = rec, i
            break

    with st.container(border=True):
        if target is None:
            st.caption("表の「この回を見る」を押すと、その回の内容と控えがここに出ます。")
            return

        head, close = st.columns([4, 1])
        head.markdown(f"**{number}回目　{jp_date(target.done_on)}**")
        if close.button("閉じる", key="btn-close-record", width="stretch"):
            st.session_state.pop("seen_record", None)
            st.rerun()

        corrected = target.id in holding.superseded_ids
        if corrected:
            replacement = next(
                (r for r in holding.records if r.supersedes == target.id), None
            )
            st.warning(
                "この記録は訂正されています。判定には使われていません。"
                + (f"　訂正後：{jp_date(replacement.done_on)}" if replacement else ""),
                icon="✏️",
            )
        elif target.supersedes:
            st.caption("※ この記録は、別の記録の訂正として登録されたものです。")

        info = st.columns(3)
        info[0].caption("実施者")
        info[0].write(target.done_by or "—")
        info[1].caption("有効期限" if target.is_expiry_update else "この回による期限")
        if target.is_expiry_update:
            info[1].write(jp_date(target.expiry_on))
        elif req.date_mode == "cycle" and req.cycle_months:
            info[1].write(jp_date(add_months(target.done_on, req.cycle_months)))
        else:
            info[1].write("—")
        info[2].caption("備考")
        info[2].write(target.memo or "—")

        st.markdown("**この回の控え**")
        if not target.attachments:
            st.caption("この回に登録された控えはありません。")
        else:
            widths = [3, 1.2, 1.6, 1.2]
            header = st.columns(widths)
            for slot, label in zip(header, ["ファイル名", "大きさ", "登録日", "登録者"]):
                slot.caption(f"**{label}**")
            for att in target.attachments:
                cols = st.columns(widths)
                cols[0].write(att.filename)
                cols[1].write(f"{att.size // 1024} KB" if att.size else "—")
                cols[2].write(jp_date(att.uploaded_on) if att.uploaded_on else "—")
                cols[3].write(att.uploaded_by or "—")
            st.caption(
                "※ このデモでは控えの一覧だけを持ち、ファイルの中身は保存していません。"
            )

        if not corrected:
            # 日付を間違えて登録したとき、正しい日付をただ追記しても直らない。
            # 最も新しい日付が採用されるので、誤って実際より後の日付を入れて
            # いると、そちらが残って期限が実際より先へ延びる。
            # 元の記録は消さず、置き換えとして積む。
            with st.expander("この記録の日付を間違えた場合"):
                st.caption(
                    "正しい日付でもう一度記録しても直りません。"
                    "新しい方の日付が使われてしまうためです。ここから訂正してください。"
                    "元の記録は消えず、「訂正済み」として残ります。"
                )
                fixed_on = st.date_input(
                    "正しい" + ("受け取った日" if target.is_expiry_update else "実施した日"),
                    value=target.done_on,
                    key=f"fix-date-{holding.id}-{number}",
                )
                fixed_expiry = None
                if target.is_expiry_update:
                    fixed_expiry = st.date_input(
                        "正しい有効期限",
                        value=target.expiry_on,
                        key=f"fix-expiry-{holding.id}-{number}",
                    )
                reason = st.text_input(
                    "訂正の理由", value="", placeholder="例）修了証を見て確認しました",
                    key=f"fix-memo-{holding.id}-{number}",
                )
                if st.button("この内容に訂正する", key=f"btn-fix-{holding.id}-{number}"):
                    try:
                        validate_done_on(fixed_on, today)
                        holding.correct_record(target.id, Record(
                            done_on=fixed_on,
                            done_by=target.done_by,
                            memo=reason or "訂正",
                            expiry_on=fixed_expiry,
                            attachments=list(target.attachments),
                        ))
                    except (ScheduleError, LedgerDataError) as e:
                        st.error(str(e), icon="⚠️")
                    else:
                        st.session_state.pop("seen_record", None)
                        st.session_state["_flash"] = (
                            f"{jp_date(target.done_on)} の記録を "
                            f"{jp_date(fixed_on)} に訂正しました。"
                        )
                        st.rerun()

        st.file_uploader(
            "この回の控えを追加する",
            type=["png", "jpg", "jpeg", "pdf"],
            key=f"upload-{holding.id}-{number}",
            disabled=not ATTACHMENTS_ENABLED,
        )
        if not ATTACHMENTS_ENABLED:
            st.caption(
                "※ 追加は公開デモでは無効にしています。"
                "修了証には氏名・生年月日・証番号が写るため、"
                "誰でも触れる状態で本物を預かることを避けています。"
                "実運用で有効にする場合は、アクセス制御と保存期間の設計が別に必要です。"
            )


def draw_add_form(subject, rows: list[Row], is_asset: bool) -> None:
    """この対象に、まだ持っていない種類を追加する。"""
    if st.session_state.get("adding") != subject.id:
        return

    category = "inspection" if is_asset else "qualification"
    already = {r.requirement.id for r in rows}
    choices = [
        r for r in lg.requirements
        if r.category == category and r.id not in already
    ]

    with st.container(border=True):
        if not choices:
            st.info("追加できる種類がありません。すべて登録済みです。")
            if st.button("閉じる", key="btn-close-add"):
                st.session_state.pop("adding", None)
                st.rerun()
            return

        st.markdown(f"**{subject.name}** に追加する")

        by_name = {r.name: r for r in choices}
        picked_name = st.selectbox(
            "追加する種類", list(by_name.keys()), key="add-requirement"
        )
        req = by_name[picked_name]

        st.caption(
            f"{OBLIGATION_LABEL[req.obligation]}／{DATE_MODE_LABEL[req.date_mode]}"
            + (f"　周期：{req.cycle_months}か月" if req.cycle_months else "")
        )
        if req.source:
            st.caption(f"根拠：{req.source}")

        fixed_due: date | None = None
        last_done: date | None = None

        if req.date_mode == "fixed":
            st.write("証に記載されている有効期限を入力してください。")

            # 写真から読み取って、欄に入れておく。確定させるのは人。
            read = st.file_uploader(
                "証の写真から読み取る（任意）",
                type=["png", "jpg", "jpeg"],
                key="add-photo",
                disabled=not PHOTO_READING_ENABLED,
            )
            if PHOTO_READING_ENABLED and read is not None:
                got = build_certificate_reader().read(read.getvalue(), read.name)
                if got.expiry_on is not None:
                    st.session_state.setdefault("add-fixed-due", got.expiry_on)
                    st.info(
                        f"写真から {jp_date(got.expiry_on)} と読み取りました。"
                        "現物と見比べてから「追加する」を押してください。",
                        icon="📷",
                    )
                else:
                    st.warning(
                        "写真からは日付を読み取れませんでした。"
                        "空欄のままにしてあります。手で入力してください。",
                        icon="📷",
                    )
                if got.note:
                    st.caption(got.note)
            elif not PHOTO_READING_ENABLED:
                st.caption(
                    "※ 写真からの読み取りは公開デモでは無効にしています。"
                    "証には氏名・生年月日・証番号が写るため、誰でも触れる状態で"
                    "本物を預かることを避けています。"
                )

            fixed_due = st.date_input(
                "有効期限", value=None, key="add-fixed-due"
            )
        elif req.date_mode == "cycle":
            st.write(
                "前回の実施日が分かれば入力してください。"
                "分からない場合は空のままで構いません（「日付未入力」として扱います）。"
            )
            last_done = st.date_input(
                "前回の実施日", value=None, key="add-last-done"
            )
        else:
            st.write("この種類に有効期限はありません。保有の記録として登録します。")

        do, cancel = st.columns([1, 1])
        if do.button("追加する", type="primary", width="stretch", key="btn-do-add"):
            if req.date_mode == "cycle" and last_done is not None:
                try:
                    validate_done_on(last_done, today)
                except ScheduleError as e:
                    st.error(str(e))
                    return

            records = (
                [Record(done_on=last_done, done_by="", memo="登録時に入力")]
                if last_done is not None
                else []
            )
            lg.holdings.append(
                Holding(
                    id=f"h-{uuid.uuid4().hex[:12]}",
                    subject_id=subject.id,
                    requirement_id=req.id,
                    fixed_due_on=fixed_due,
                    records=records,
                )
            )
            st.session_state.pop("adding", None)
            st.session_state["_flash"] = f"{subject.name} に {req.name} を追加しました。"
            st.rerun()

        if cancel.button("やめる", width="stretch", key="btn-cancel-add"):
            st.session_state.pop("adding", None)
            st.rerun()


def draw_record_form(rows: list[Row]) -> None:
    """実施の記録。開閉ではなく、常にここにある領域の中身が入れ替わる。"""
    target_id = st.session_state.get("recording")
    target = next((r for r in rows if r.holding.id == target_id), None)

    with st.container(border=True):
        if target is None:
            st.caption("表の「記録」を押すと、ここで実施日を入力できます。")
            return

        st.markdown(f"**{target.requirement.name}** の実施を記録する")
        c1, c2, c3, c4 = st.columns([1.2, 1.2, 1, 1])
        done_on = c1.date_input("実施した日", value=today, key=f"done-{target.holding.id}")
        done_by = c2.text_input("実施者", value="", key=f"by-{target.holding.id}")

        if c3.button("記録する", type="primary", width="stretch", key="btn-do-record"):
            try:
                validate_done_on(done_on, today)
            except ScheduleError as e:
                st.error(str(e))
            else:
                target.holding.add_record(Record(done_on=done_on, done_by=done_by))
                # 実施したので、その予約はもう使わない。
                target.holding.planned_on = None
                st.session_state.pop("recording", None)
                st.session_state["_flash"] = (
                    f"{target.requirement.name} に {jp_date(done_on)} の実施を"
                    "記録しました。次回の期限を計算し直しました。"
                )
                st.rerun()

        if c4.button("やめる", width="stretch", key="btn-cancel-record"):
            st.session_state.pop("recording", None)
            st.rerun()


def page_people() -> None:
    selected_id = st.session_state.get("selected")
    selected = lg.subject(selected_id) if selected_id else None
    if selected is not None and selected.kind == "person":
        if st.session_state.get("selected_holding"):
            draw_holding_detail("person")
        else:
            draw_detail("person")
        return

    summaries = summarize_by_subject(lg, today, kind="person")

    by_status: dict[str, list[SubjectSummary]] = {key: [] for key, _, _ in SECTIONS}
    clear: list[SubjectSummary] = []
    for s in summaries:
        if s.worst in by_status:
            by_status[s.worst].append(s)
        else:
            clear.append(s)

    if _flash:
        st.success(_flash, icon="✅")

    st.title(PAGE_PEOPLE)
    st.caption(
        "期限切れ → 日付未入力・期限計算不可 → 期限間近 の順に表示します。"
        "上から順に確認してください。"
    )

    tiles = st.columns(len(SECTIONS) + 1)
    for (key, label, note), slot in zip(SECTIONS, tiles):
        slot.markdown(ui.tile(key, len(by_status[key]), fill_days(note), "人"),
                      unsafe_allow_html=True)
    tiles[-1].markdown(
        ui.tile("ok", len(clear), "期限切れ・期限間近はありません", "人"),
        unsafe_allow_html=True,
    )

    left, right = st.columns([1, 1])

    with left:
        st.markdown("**氏名で検索**　:gray[(表示範囲に関係なく全員から検索します)]")
        keyword = st.text_input(
            "氏名で検索",
            placeholder="例：東 亮",
            label_visibility="collapsed",
            key="search",
        ).strip()

    with right:
        st.markdown("**表示範囲を選んでください**")
        st.radio(
            "表示範囲",
            ["対応が必要な人だけ", "全員"],
            horizontal=True,
            label_visibility="collapsed",
            key="scope",
            captions=[
                "期限切れ・期限間近・日付未入力の人を表示",
                "問題なしの人も含めて全員を表示",
            ],
        )

    # 検索は絞り込みを飛び越える。「対応が必要な人だけ」を表示していても、
    # 名前を打てば全員から探す。打ったのに出てこない状態を作らないため。
    if keyword:
        hits = [
            s for s in summaries
            # 登録画面で「ふりがなは検索に使います」と案内しているので、
            # 道具と同じく search_text で引く。
            if keyword in s.subject.search_text
        ]
        st.markdown(ui.section(f"「{keyword}」の検索結果", len(hits), "人"), unsafe_allow_html=True)
        if hits:
            st.caption("表示範囲の設定に関係なく、全員から探しています。")
            draw_grid(hits)
        else:
            st.warning(f"「{keyword}」に一致する人は登録されていません。")
            # 探して見つからなかった直後が、登録したい瞬間である。
            # ここで左のボタンまで目を戻させると、探した言葉を打ち直すことになる。
            if st.button(f"「{keyword}」を社員として登録する",
                         key="btn-register-from-search-person"):
                st.session_state["registering"] = "person"
                st.session_state["_prefill_name"] = keyword
                st.session_state.pop("selected", None)
                st.rerun()
    else:
        shown_clear = st.session_state.get("scope") == "全員"

        any_shown = False
        for key, label, _ in SECTIONS:
            items = by_status[key]
            if not items:
                continue
            any_shown = True
            st.markdown(ui.section(fill_days(label), len(items), "人"),
                        unsafe_allow_html=True)
            draw_grid(items)

        if shown_clear and clear:
            any_shown = True
            st.markdown(ui.section("問題なし", len(clear), "人"), unsafe_allow_html=True)
            draw_grid(clear)

        if not any_shown:
            st.success("対応が必要な人はいません。")

        if not shown_clear and clear:
            note, action = st.columns([3, 1])
            note.info(f"問題なしの人が{len(clear)}人います。「全員」を選ぶと表示できます。")
            if action.button("全員を表示する", width="stretch", key="btn-show-all"):
                st.session_state["_request_show_all"] = "scope"
                st.rerun()



# --- 道具・機器の点検 -----------------------------------------------------


def page_assets() -> None:
    selected_id = st.session_state.get("selected")
    selected = lg.subject(selected_id) if selected_id else None
    if selected is not None and selected.kind == "asset":
        if st.session_state.get("selected_holding"):
            draw_holding_detail("asset")
        else:
            draw_detail("asset")
        return

    summaries = summarize_by_subject(lg, today, kind="asset")

    by_status: dict[str, list[SubjectSummary]] = {key: [] for key, _, _ in SECTIONS}
    clear: list[SubjectSummary] = []
    for s in summaries:
        if s.worst in by_status:
            by_status[s.worst].append(s)
        else:
            clear.append(s)

    if _flash:
        st.success(_flash, icon="✅")

    st.title(PAGE_ASSETS)
    st.caption(
        "期限切れ → 日付未入力・期限計算不可 → 期限間近 の順に表示します。"
        "上から順に確認してください。"
    )

    tiles = st.columns(len(SECTIONS) + 1)
    for (key, label, note), slot in zip(SECTIONS, tiles):
        slot.markdown(
            ui.tile(pill_key(key, True), len(by_status[key]), fill_days(note), "件"),
            unsafe_allow_html=True,
        )
    tiles[-1].markdown(
        ui.tile("ok", len(clear), "期限切れ・期限間近はありません", "件"),
        unsafe_allow_html=True,
    )

    left, right = st.columns([1, 1])

    with left:
        st.markdown("**名称・管理番号・型番・保管場所で検索**")
        st.caption("表示範囲に関係なく全件から探します。「鹿屋」で保管場所も引けます。")
        keyword = st.text_input(
            "検索",
            placeholder="例：絶縁手袋　GLO-002　HIOKI　鹿屋",
            label_visibility="collapsed",
            key="search-asset",
        ).strip()

    with right:
        st.markdown("**表示範囲を選んでください**")
        st.radio(
            "表示範囲",
            ["対応が必要なものだけ", "すべて"],
            horizontal=True,
            label_visibility="collapsed",
            key="scope-asset",
            captions=[
                "期限切れ・期限間近・日付未入力のものを表示",
                "問題なしのものも含めてすべて表示",
            ],
        )

    if keyword:
        hits = [s for s in summaries if keyword in s.subject.search_text]
        st.markdown(ui.section(f"「{keyword}」の検索結果", len(hits), "件"), unsafe_allow_html=True)
        if hits:
            st.caption("表示範囲の設定に関係なく、全件から探しています。")
            draw_grid(hits)
        else:
            st.warning(f"「{keyword}」に一致する道具・機器は登録されていません。")
            if st.button(f"「{keyword}」を道具・機器として登録する",
                         key="btn-register-from-search-asset"):
                st.session_state["registering"] = "asset"
                st.session_state["_prefill_name"] = keyword
                st.session_state.pop("selected", None)
                st.rerun()
    else:
        shown_clear = st.session_state.get("scope-asset") == "すべて"

        any_shown = False
        for key, label, _ in SECTIONS:
            items = by_status[key]
            if not items:
                continue
            any_shown = True
            # 「資格情報なし」は人の言い方。道具の画面では別の言葉にする。
            shown = (
                ui.STATE_STYLE[pill_key(key, True)][0]
                if key == "unregistered"
                else fill_days(label)
            )
            st.markdown(ui.section(shown, len(items), "件"), unsafe_allow_html=True)
            draw_grid(items)

        if shown_clear and clear:
            any_shown = True
            st.markdown(ui.section("問題なし", len(clear), "件"), unsafe_allow_html=True)
            draw_grid(clear)

        if not any_shown:
            st.success("対応が必要な道具・機器はありません。")

        if not shown_clear and clear:
            note, action = st.columns([3, 1])
            note.info(
                f"問題なしの道具・機器が{len(clear)}件あります。"
                "「すべて」を選ぶと表示できます。"
            )
            if action.button("すべて表示する", width="stretch", key="btn-show-all-asset"):
                st.session_state["_request_show_all"] = "scope-asset"
                st.rerun()


# --- 安全書類の提出前チェック ---------------------------------------------


def draw_issue_group(rows: list[Row]) -> None:
    """引っかかる項目を人ごとにまとめて出す。作業員名簿が人単位で作られるため。"""
    by_person: dict[str, list[Row]] = {}
    for row in rows:
        # 名前をキーにすると、同姓同名や同名の道具が同じ箱に混ざる。
        by_person.setdefault(row.subject.id, []).append(row)

    for items in by_person.values():
        head = items[0].subject
        label = f"{head.name}（{head.code}）" if head.code else head.name
        with st.container(border=True):
            st.markdown(f"**{label}**　:gray[{head.site} ／ {head.role}]")
            for row in items:
                st.write(f"　・{row.requirement.name}：{reason_text(row)}")


def page_submission() -> None:
    st.title(PAGE_SUBMISSION)
    st.markdown(
        "**作った日ではなく、出す日で見ます。**\n\n"
        "健康診断や資格証は、書類を作った時点では有効でも、提出日や工期の終わりには"
        "切れていることがあります。元請から差し戻される原因の多くがこれです。"
        "提出する日を入れて、その日に引っかかるものを先に洗い出してください。"
    )

    setting, target = st.columns([1, 2])

    with setting:
        st.markdown("**いつ提出しますか**")
        st.caption("工期の終わりで見る場合は、その日を入れてください")
        target_date = st.date_input(
            "提出予定日",
            value=today,
            label_visibility="collapsed",
            key="submit-date",
        )

    people = [s for s in lg.subjects if s.kind == "person"]
    assets = [s for s in lg.subjects if s.kind == "asset"]
    # 同姓同名がいると名前だけでは選べない。社員番号を添えて必ず一意にする。
    person_by_name = {
        (f"{s.name}（{s.code}）" if s.code else f"{s.name}（{s.id}）"): s.id
        for s in people
    }
    asset_by_name = {f"{s.name}（{s.code}）": s.id for s in assets}

    with target:
        st.markdown("**現場に出す人**")
        st.caption("選ばなければ、社員全員を見ます")
        picked = st.multiselect(
            "現場に出す人",
            list(person_by_name.keys()),
            label_visibility="collapsed",
            key="submit-people",
        )

        st.markdown("**現場に持ち込む道具・機器**")
        st.caption("選ばなければ、道具・機器すべてを見ます")
        picked_assets = st.multiselect(
            "現場に持ち込む道具・機器",
            list(asset_by_name.keys()),
            label_visibility="collapsed",
            key="submit-assets",
        )

    # どちらも選ばれていなければ全件を見る。片方だけ選ばれた場合も、
    # もう片方は全件のまま。選べる対象と、結果に出てくる対象を一致させる。
    if picked or picked_assets:
        subject_ids = (
            [person_by_name[n] for n in picked]
            + [asset_by_name[n] for n in picked_assets]
        )
        if not picked:
            subject_ids += [s.id for s in people]
        if not picked_assets:
            subject_ids += [s.id for s in assets]
    else:
        subject_ids = None

    if target_date < today:
        st.warning("過去の日付が入っています。これから提出する日を入れてください。")
        return

    issues = submission_check(lg, target_date=target_date, subject_ids=subject_ids)
    # 行の検査だけでは、記録が 1 件も無い人を見逃す。行が作られないため。
    blank = unrecorded_subjects(lg, subject_ids=subject_ids)

    who = f"選んだ{len(picked)}人" if picked else f"社員全員（{len(people)}人）"
    what = (
        f"選んだ{len(picked_assets)}件"
        if picked_assets
        else f"道具・機器すべて（{len(assets)}件）"
    )
    st.caption(f"対象：{who}　／　{what}")

    if not issues and not blank:
        st.success(
            f"{jp_date(target_date)} 時点で、登録されている情報の中に"
            "書類を止める項目はありません。",
            icon="✅",
        )
        st.caption(
            "※ 台帳に登録されていない資格については判定できません。"
            "職種ごとに必要な資格が揃っているかの確認は、この画面では行いません。"
        )
        return

    # 今日すでに切れているものと、その日までに切れるものを分ける。
    # 後者はこの画面でしか気づけない。社員の一覧では今日の状態しか見えないため。
    today_ids = {
        r.holding.id
        for r in submission_check(lg, target_date=today, subject_ids=subject_ids)
    }
    already = [r for r in issues if r.holding.id in today_ids]
    upcoming = [r for r in issues if r.holding.id not in today_ids]

    st.error(
        f"{jp_date(target_date)} に提出すると、"
        f"{len(issues) + len(blank)}件が引っかかります。",
        icon="⚠️",
    )

    if upcoming:
        st.markdown(ui.section("その日までに切れるもの", len(upcoming), "件"),
                    unsafe_allow_html=True)
        st.caption(
            "今日の時点ではまだ有効です。社員の一覧を見ているだけでは気づけません。"
        )
        draw_issue_group(upcoming)

    if already:
        st.markdown(ui.section("今日すでに引っかかっているもの", len(already), "件"),
                    unsafe_allow_html=True)
        st.caption("提出する日に関係なく、いま対応が必要です。")
        draw_issue_group(already)

    if blank:
        st.markdown(ui.section("情報が無く、判断できないもの", len(blank), "件"),
                    unsafe_allow_html=True)
        st.caption(
            "記録が1件も登録されていません。問題が無いのではなく、"
            "書類に載せてよいかどうかを判断できない状態です。"
        )
        for subject in blank:
            is_a = subject.kind == "asset"
            with st.container(border=True):
                head, pill_col = st.columns([3, 1])
                label = f"{subject.name}（{subject.code}）" if is_a else subject.name
                head.markdown(f"**{label}**　:gray[{subject.site} ／ {subject.role}]")
                pill_col.markdown(
                    ui.pill(pill_key("unregistered", is_a), short=True),
                    unsafe_allow_html=True,
                )
                st.write(
                    "　・点検・校正を登録してから、書類を作成してください。"
                    if is_a
                    else "　・資格・講習・健診を登録してから、書類を作成してください。"
                )


# --- 種類の設定 -----------------------------------------------------------


def draw_requirement(req: Requirement) -> None:
    """種類 1 件。周期を持つものは、ここで周期を変えられる。"""
    with st.container(border=True):
        head, cycle = st.columns([3, 1])

        with head:
            st.markdown(f"**{req.name}**")
            st.caption(
                f"{OBLIGATION_LABEL[req.obligation]}　／　"
                f"{DATE_MODE_LABEL[req.date_mode]}"
            )
            if req.source:
                st.write(f"根拠：{req.source}")
            if req.note:
                st.caption(req.note)

        with cycle:
            if req.date_mode != "cycle":
                st.caption("周期では決まりません")
                return

            months = st.number_input(
                "周期（か月）",
                min_value=1,
                max_value=120,
                value=req.cycle_months or 12,
                key=f"cycle-{req.id}",
            )
            if months != req.cycle_months:
                # 凍結した値なので差し替える。変更はその場で全画面に効く。
                index = lg.requirements.index(req)
                lg.requirements[index] = replace(req, cycle_months=int(months))
                st.rerun()


def page_types() -> None:
    st.title(PAGE_TYPES)
    st.markdown(
        "**周期も警告のタイミングも、プログラムには書き込んでいません。**\n\n"
        "点検や講習の周期は、設備の条件や契約、社内の規程によって変わります。"
        "外から決め打ちできるものではないので、ここで変えられるようにしてあります。"
        "ここで変えた値が、そのまま判定に使われます。"
    )

    st.markdown("##### 警告のタイミング")
    with st.container(border=True):
        left, right = st.columns([1, 3])
        with left:
            soon = st.number_input(
                "何日前から「期限間近」とするか",
                min_value=1,
                max_value=365,
                value=lg.soon_days,
                key="setting-soon",
            )
        with right:
            st.caption(
                f"いまは期限の{lg.soon_days}日前から「期限間近」として扱っています。"
                "この日数を変えると、社員の一覧と提出前チェックの結果がすぐに変わります。"
            )
        if soon != lg.soon_days:
            lg.set_soon_days(int(soon))
            st.rerun()

    with_deadline = [r for r in lg.requirements if r.has_deadline]
    without_deadline = [r for r in lg.requirements if not r.has_deadline]

    for category, label in (
        ("qualification", "資格・講習・健診"),
        ("inspection", "点検・校正"),
    ):
        items = [r for r in with_deadline if r.category == category]
        if not items:
            continue
        st.markdown(f"##### {label}　{len(items)}種類")
        for req in items:
            draw_requirement(req)

    if without_deadline:
        st.markdown(f"##### 有効期限がない種類　{len(without_deadline)}種類")
        st.caption(
            "電気主任技術者の免状などには有効期限がありません。"
            "誰が持っているかを把握するために登録しますが、期限としては扱わず、"
            "警告にも混ぜません。**期限のないものを期限として並べると、"
            "台帳そのものが信用されなくなるためです。**"
        )
        for req in without_deadline:
            draw_requirement(req)

    st.info(
        "種類そのものの追加・削除は、まだ作っていません。"
        "いまできるのは、周期と警告のタイミングの変更です。",
        icon="ℹ️",
    )

    # --- 拠点と職種 -------------------------------------------------------
    #
    # 登録済みの社員から数え上げるだけにすると、その拠点の最後の1人を
    # 消した時点で拠点そのものが選択肢から消え、次の人を登録できなくなる。
    # 事業所は人がいなくても存在するので、ここで持つ。
    st.divider()
    st.markdown("#### 拠点・職種")
    if _flash:
        st.success(_flash, icon="✅")
    st.caption(
        "登録画面で選べる一覧です。ここに無い拠点や職種は選べません。"
        "人が1人もいない拠点も、消さずに残ります。"
    )

    for master_label, unit, key, current, add in (
        ("拠点", "拠点", "site", lg.sites, lg.add_site),
        ("職種（社員）", "職種", "role-person", lg.roles("person"),
         lambda name: lg.add_role(name, "person")),
        ("種別（道具・機器）", "種別", "role-asset", lg.roles("asset"),
         lambda name: lg.add_role(name, "asset")),
    ):
        with st.container(border=True):
            st.markdown(f"**{master_label}**　{len(current)}件")
            st.caption("／".join(current) if current else "まだありません")

            col_input, col_button = st.columns([3, 1], vertical_alignment="bottom")
            with col_input:
                name = st.text_input(
                    f"追加する{unit}", key=f"master-input-{key}",
                    placeholder=f"例）{current[0] if current else ''}",
                    label_visibility="collapsed",
                )
            with col_button:
                added = st.button("追加", key=f"master-add-{key}", width="stretch")

            if added:
                try:
                    add(name)
                except LedgerDataError as e:
                    # 失敗したときは作り直さない。作り直すと、
                    # 打った内容ごと消えたうえで理由も出ない。
                    st.error(str(e), icon="⚠️")
                else:
                    st.session_state["_flash"] = f"{unit}「{name.strip()}」を追加しました。"
                    st.rerun()


# --- 社員の登録 -----------------------------------------------------------


def next_employee_code(prefix: str = "E-") -> str:
    """空欄で登録されたときに割り当てる社員番号。

    番号がまだ決まっていない新入社員を登録できるようにするため、
    社員番号は必須にしない。空欄なら既存の最大値の次を割り当てる。
    """
    used = 0
    for s in lg.subjects:
        if s.kind != "person" or not s.code.startswith(prefix):
            continue
        tail = s.code[len(prefix):]
        if tail.isdigit():
            used = max(used, int(tail))
    return f"{prefix}{used + 1:04d}"


def code_is_taken(code: str, exclude_id: str | None = None) -> Subject | None:
    """同じ社員番号を持つ人がいれば返す。

    重複したまま登録すると、一覧でどちらの話をしているのか分からなくなる。
    手で押す確認ボタンは置かず、登録のときに必ず通す。
    """
    for s in lg.subjects:
        if s.id == exclude_id:
            continue
        if s.kind == "person" and s.code and s.code == code:
            return s
    return None


REGISTER_STEPS = [
    ("unregistered", "登録した直後は「資格情報なし」",
     "資格が1件も無い社員は、一覧で「資格情報なし（要対応）」として表示されます。"
     "問題が無いのではなく、まだ何も分かっていないためです。"),
    ("unknown", "資格・講習・健診を登録する",
     "登録が終わると、その社員の画面へ移ります。続けて資格や健診を登録してください。"),
    ("ok", "安全書類の提出前チェックに入る",
     "情報が揃うと、提出日を指定したチェックの対象になります。"),
    ("due_soon", "期限が見えるようになる",
     "期限切れと期限間近が一覧に出るので、更新や受講を前もって手配できます。"),
]


def page_register_person() -> None:
    st.title("社員を登録する")
    st.caption("新しく管理する社員の情報を入力してください。※ は必須項目です。")

    if st.button("← 一覧に戻る", key="btn-register-back"):
        st.session_state.pop("registering", None)
        st.rerun()

    sites = lg.sites or ["本社"]
    roles = lg.roles("person")

    with st.container(border=True):
        name = st.text_input(
            "氏名　※", placeholder="例）迫田 和樹", key="reg-name"
        )
        st.caption("姓と名の間にスペースを入れてください。")

        kana = st.text_input(
            "ふりがな　※", placeholder="例）さこだ かずき", key="reg-kana"
        )
        st.caption("ひらがなで入力してください。検索に使います。")

        code = st.text_input(
            "社員番号（任意）", placeholder="例）E-0001", key="reg-code"
        )
        st.caption(
            f"空欄の場合は自動で採番します（次の番号は {next_employee_code()}）。"
            "同じ番号がすでに使われている場合は、登録のときにお知らせします。"
        )

        st.markdown("**所属（拠点）　※**")
        site = st.radio(
            "所属", sites, horizontal=True, label_visibility="collapsed", key="reg-site"
        )

        st.markdown("**職種　※**")
        role = st.radio(
            "職種", roles, horizontal=True, label_visibility="collapsed", key="reg-role"
        )

        note = st.text_area(
            "備考（任意）",
            placeholder="必要に応じて入力してください",
            max_chars=200,
            key="reg-note",
        )

        left, right = st.columns([1, 2])
        if left.button("入力をやり直す", width="stretch", key="btn-register-clear"):
            for k in ("reg-name", "reg-kana", "reg-code", "reg-note"):
                st.session_state.pop(k, None)
            st.rerun()

        if right.button(
            "登録して、資格の登録へ進む",
            type="primary",
            width="stretch",
            key="btn-register-do",
        ):
            problems: list[str] = []
            if not name.strip():
                problems.append("氏名を入力してください。")
            if not kana.strip():
                problems.append("ふりがなを入力してください。")

            wanted = code.strip() or next_employee_code()
            taken = code_is_taken(wanted)
            if taken is not None:
                problems.append(
                    f"社員番号 {wanted} は {taken.name} さんが使っています。"
                    "別の番号を入力するか、空欄にして自動採番にしてください。"
                )

            if problems:
                for p in problems:
                    st.error(p, icon="⚠️")
            else:
                # 件数から作ると、削除したあとに既存IDと衝突する。
                # 内部IDは社員番号とは別物なので、衝突しない値を使う。
                new_id = f"p-{uuid.uuid4().hex[:12]}"
                lg.subjects.append(
                    Subject(
                        id=new_id,
                        name=name.strip(),
                        kind="person",
                        site=site,
                        role=role,
                        code=wanted,
                        kana=kana.strip(),
                        note=note.strip(),
                    )
                )
                for k in ("reg-name", "reg-kana", "reg-code", "reg-note"):
                    st.session_state.pop(k, None)
                st.session_state.pop("registering", None)
                st.session_state["selected"] = new_id
                st.session_state["_flash"] = (
                    f"{name.strip()} さん（{wanted}）を登録しました。"
                    "資格が1件も無いため、いまは「資格情報なし」の状態です。"
                    "続けて資格・講習・健診を登録してください。"
                )
                st.rerun()

    st.markdown("##### 登録したあとの流れ")
    steps = st.columns(len(REGISTER_STEPS))
    for (state, title, body), slot in zip(REGISTER_STEPS, steps):
        with slot.container(border=True):
            st.markdown(ui.bar(state), unsafe_allow_html=True)
            st.markdown(f"**{title}**")
            st.caption(body)


# --- 道具・機器の登録 -------------------------------------------------------


def next_asset_code(prefix: str = "M-") -> str:
    """空欄で登録されたときに割り当てる管理番号。

    道具の管理番号は会社が現物に貼るものなので、本来は手で入れる。
    ただし買ったばかりで番号がまだ貼られていない場合に登録できないと困るため、
    空欄なら仮の番号を割り当てる。
    """
    used = 0
    for s in lg.subjects:
        if s.kind != "asset" or not s.code.startswith(prefix):
            continue
        tail = s.code[len(prefix):]
        if tail.isdigit():
            used = max(used, int(tail))
    return f"{prefix}{used + 1:04d}"


def asset_code_is_taken(code: str, exclude_id: str | None = None) -> Subject | None:
    """同じ管理番号の道具がいれば返す。

    道具は名前では特定できないため、管理番号が重複すると現物にたどり着けなくなる。
    人の社員番号より影響が大きい。
    """
    for s in lg.subjects:
        if s.id == exclude_id:
            continue
        if s.kind == "asset" and s.code and s.code == code:
            return s
    return None


REGISTER_ASSET_STEPS = [
    ("unregistered_asset", "登録した直後は「点検情報なし」",
     "点検が1件も無い道具は、一覧で「点検情報なし（要対応）」として表示されます。"
     "問題が無いのではなく、まだ何も分かっていないためです。"),
    ("unknown", "点検・校正を登録する",
     "登録が終わると、その道具の画面へ移ります。続けて点検や校正を登録してください。"),
    ("ok", "前回実施日から次回期日が決まる",
     "周期と前回実施日が揃うと、次回の期日が自動で計算されます。"),
    ("due_soon", "期限が見えるようになる",
     "絶縁用保護具の6か月検査のように周期の短いものほど、抜けを防げます。"),
]


def page_register_asset() -> None:
    st.title("道具・機器を登録する")
    st.caption("新しく管理する道具・機器の情報を入力してください。※ は必須項目です。")

    if st.button("← 一覧に戻る", key="btn-register-asset-back"):
        st.session_state.pop("registering", None)
        st.rerun()

    sites = lg.sites or ["本社"]
    kinds = lg.roles("asset")

    with st.container(border=True):
        name = st.text_input(
            "名称　※", placeholder="例）絶縁手袋 A組", key="reg-a-name"
        )
        st.caption("何であるかが分かる名前を入れてください。")

        code = st.text_input(
            "管理番号（任意）", placeholder="例）GLO-001", key="reg-a-code"
        )
        st.caption(
            f"現物に貼っている番号を入れてください。"
            f"空欄の場合は仮の番号を割り当てます（次は {next_asset_code()}）。"
            "同じ番号がすでに使われている場合は、登録のときにお知らせします。"
        )

        model = st.text_input(
            "型番（任意）", placeholder="例）YOTSUGI YS-101-23-01", key="reg-a-model"
        )
        st.caption("校正や修理を依頼するときに使います。分かれば入れてください。")

        st.markdown("**保管場所　※**")
        site = st.radio(
            "保管場所", sites, horizontal=True,
            label_visibility="collapsed", key="reg-a-site",
        )

        st.markdown("**種類　※**")
        role = st.radio(
            "種類", kinds, horizontal=True,
            label_visibility="collapsed", key="reg-a-role",
        )

        note = st.text_area(
            "備考（任意）",
            placeholder="必要に応じて入力してください",
            max_chars=200,
            key="reg-a-note",
        )

        left, right = st.columns([1, 2])
        if left.button("入力をやり直す", width="stretch", key="btn-register-a-clear"):
            for k in ("reg-a-name", "reg-a-code", "reg-a-model", "reg-a-note"):
                st.session_state.pop(k, None)
            st.rerun()

        if right.button(
            "登録して、点検の登録へ進む",
            type="primary",
            width="stretch",
            key="btn-register-a-do",
        ):
            problems: list[str] = []
            if not name.strip():
                problems.append("名称を入力してください。")

            wanted = code.strip() or next_asset_code()
            taken = asset_code_is_taken(wanted)
            if taken is not None:
                problems.append(
                    f"管理番号 {wanted} は「{taken.name}」が使っています。"
                    "別の番号を入力するか、空欄にして自動採番にしてください。"
                )

            if problems:
                for p in problems:
                    st.error(p, icon="⚠️")
            else:
                new_id = f"a-{uuid.uuid4().hex[:12]}"
                lg.subjects.append(
                    Subject(
                        id=new_id,
                        name=name.strip(),
                        kind="asset",
                        site=site,
                        role=role,
                        code=wanted,
                        model=model.strip(),
                        note=note.strip(),
                    )
                )
                for k in ("reg-a-name", "reg-a-code", "reg-a-model", "reg-a-note"):
                    st.session_state.pop(k, None)
                st.session_state.pop("registering", None)
                st.session_state["selected"] = new_id
                st.session_state["_flash"] = (
                    f"「{name.strip()}」（{wanted}）を登録しました。"
                    "点検が1件も無いため、いまは「点検情報なし」の状態です。"
                    "続けて点検・校正を登録してください。"
                )
                st.rerun()

    st.markdown("##### 登録したあとの流れ")
    steps = st.columns(len(REGISTER_ASSET_STEPS))
    for (state, title, body), slot in zip(REGISTER_ASSET_STEPS, steps):
        with slot.container(border=True):
            st.markdown(ui.bar(state), unsafe_allow_html=True)
            st.markdown(f"**{title}**")
            st.caption(body)


# --- 登録内容の編集 ---------------------------------------------------------


def page_edit_subject() -> None:
    """登録した内容を直す。登録画面とほぼ同じ形にして、迷わせない。

    登録できて直せないと、名前の打ち間違いや異動があったときに手が出せなくなる。
    記録（実施・点検）はここでは触らない。あれは追記のみで書き換えないものなので、
    直す対象は対象そのものの属性だけにしている。
    """
    subject = lg.subject(st.session_state["editing"])
    if subject is None:
        st.session_state.pop("editing", None)
        st.rerun()
        return

    is_asset = subject.kind == "asset"

    st.title("道具・機器の情報を直す" if is_asset else "社員の情報を直す")
    st.caption("登録済みの内容を書き換えます。※ は必須項目です。")

    if st.button("← やめて戻る", key="btn-edit-back"):
        st.session_state.pop("editing", None)
        st.rerun()

    sites = lg.sites or ["本社"]
    kinds = lg.roles(subject.kind)
    if subject.role and subject.role not in kinds:
        kinds.append(subject.role)

    with st.container(border=True):
        if is_asset:
            name = st.text_input("名称　※", value=subject.name, key="ed-name")
            code = st.text_input("管理番号", value=subject.code, key="ed-code")
            st.caption("現物に貼っている番号です。空欄にはできますが、推奨しません。")
            model = st.text_input("型番", value=subject.model, key="ed-model")
            kana = ""
        else:
            name = st.text_input("氏名　※", value=subject.name, key="ed-name")
            st.caption("姓と名の間にスペースを入れてください。")
            kana = st.text_input("ふりがな　※", value=subject.kana, key="ed-kana")
            code = st.text_input("社員番号", value=subject.code, key="ed-code")
            model = ""

        st.caption("同じ番号がすでに使われている場合は、保存のときにお知らせします。")

        st.markdown("**保管場所　※**" if is_asset else "**所属（拠点）　※**")
        site = st.radio(
            "所属",
            sites,
            index=sites.index(subject.site) if subject.site in sites else 0,
            horizontal=True,
            label_visibility="collapsed",
            key="ed-site",
        )

        st.markdown("**種類　※**" if is_asset else "**職種　※**")
        role = st.radio(
            "職種",
            kinds,
            index=kinds.index(subject.role) if subject.role in kinds else 0,
            horizontal=True,
            label_visibility="collapsed",
            key="ed-role",
        )

        note = st.text_area(
            "備考（任意）", value=subject.note, max_chars=200, key="ed-note"
        )

        left, right = st.columns([1, 2])
        if left.button("やめる", width="stretch", key="btn-edit-cancel"):
            st.session_state.pop("editing", None)
            st.rerun()

        if right.button(
            "保存する", type="primary", width="stretch", key="btn-edit-save"
        ):
            problems: list[str] = []
            if not name.strip():
                problems.append("名称を入力してください。" if is_asset
                                else "氏名を入力してください。")
            if not is_asset and not kana.strip():
                problems.append("ふりがなを入力してください。")

            wanted = code.strip()
            if wanted:
                # 自分自身は重複とみなさない。番号を変えずに保存できるようにする。
                taken = (
                    asset_code_is_taken(wanted, exclude_id=subject.id)
                    if is_asset
                    else code_is_taken(wanted, exclude_id=subject.id)
                )
                if taken is not None:
                    label = "管理番号" if is_asset else "社員番号"
                    problems.append(
                        f"{label} {wanted} は「{taken.name}」が使っています。"
                        "別の番号を入力してください。"
                    )

            if problems:
                for p in problems:
                    st.error(p, icon="⚠️")
            else:
                index = lg.subjects.index(subject)
                lg.subjects[index] = replace(
                    subject,
                    name=name.strip(),
                    kana=kana.strip(),
                    code=wanted,
                    model=model.strip(),
                    site=site,
                    role=role,
                    note=note.strip(),
                )
                for k in ("ed-name", "ed-kana", "ed-code", "ed-model", "ed-note"):
                    st.session_state.pop(k, None)
                st.session_state.pop("editing", None)
                st.session_state["_flash"] = f"「{name.strip()}」の情報を保存しました。"
                st.rerun()

    st.caption(
        "※ 資格・講習・健診や点検の記録は、ここでは変わりません。"
        "記録は追記のみで書き換えない決まりのため、"
        "直したい場合はその記録の画面から新しく追加してください。"
    )


# --- AIサポート -------------------------------------------------------------

# 本物の AI へ接続するかどうか。公開デモでは閉じておく。
#
# いまの判定は、打たれた言葉から意図を読み取る仕組みで動いており、外部へは
# 一切つながらない。ここを True にすると core/intent_llm.py 経由で
# 言い換えだけを外部の AI に頼む。答えは相変わらず台帳の関数から作る。
#
# **この経路は未検証。** API キーが無いため、実際に呼び出して動くことを
# 確認していない。書いてあるだけで、動作を保証しない。
AI_API_ENABLED = False


def build_normalizer():
    """言い換え役。無効なとき、SDK が無いときは None（従来どおり動く）。"""
    if not AI_API_ENABLED:
        return None
    from core import intent_llm

    if not intent_llm.is_available():
        return None

    def call(question: str) -> str | None:
        return intent_llm.normalize(question, today_text=jp_date(today))

    return call


def page_ai() -> None:
    st.title(PAGE_AI)
    st.caption(
        "台帳について質問できます。操作の場所も案内します。"
        "答えはすべて台帳の記録から作っているので、画面の表示と食い違いません。"
    )

    question = st.text_input(
        "聞きたいこと",
        placeholder="例）期限が切れているものを教えて",
        label_visibility="collapsed",
        key="ai-question",
    )

    st.markdown("**こう聞けます**")
    cols = st.columns(2)
    for i, example in enumerate(CAPABILITIES):
        if cols[i % 2].button(example, key=f"ai-ex-{i}", width="stretch"):
            st.session_state["_ai_example"] = example
            st.rerun()

    if not question.strip():
        st.info(
            "上の例を押すか、聞きたいことを入力してください。",
            icon="💬",
        )
        return

    normalizer = build_normalizer()
    try:
        result = assistant_answer(lg, question, today, normalizer=normalizer)
    except Exception as e:  # noqa: BLE001
        # 外部への問い合わせが失敗しても、こちら側の判定は使える。
        # 黙って握り潰さず、失敗したことを見せたうえで従来どおり答える。
        st.warning(f"外部への問い合わせに失敗しました：{e}", icon="⚠️")
        result = assistant_answer(lg, question, today)

    st.divider()

    if result.kind == "unknown":
        st.warning(result.headline, icon="🤔")
        for line in result.lines:
            st.write(line)
        st.caption(
            "※ 近いものを推測して答えることはしません。"
            "台帳の表示と食い違う答えを出すのが、一番困るためです。"
        )
        return

    if result.kind == "guide":
        st.success(result.headline, icon="👉")
        for i, line in enumerate(result.lines, start=1):
            st.write(f"{i}. {line}")
        if result.highlight:
            # 押す先そのものを光らせる。
            st.markdown(ui.highlight(result.highlight), unsafe_allow_html=True)
            st.caption(
                "※ 左の画面で、押す場所をオレンジ色で示しています。"
                "こちらでは押しません。ご自身で押してください。"
            )
        return

    # 照会の答え
    st.info(result.headline, icon="📋")
    for line in result.lines:
        st.write(line)

    if result.rows:
        st.markdown(ui.section("該当するもの", len(result.rows), "件"),
                    unsafe_allow_html=True)
        widths = [1.6, 2.2, 1.3, 1.8]
        header = st.columns(widths)
        for slot, label in zip(header, ["対象", "種類", "状態", "期限・状況"]):
            slot.caption(f"**{label}**")
        for row in result.rows[:30]:
            cols = st.columns(widths)
            cols[0].write(row.subject.name)
            cols[1].write(row.requirement.name)
            cols[2].markdown(state_label(row), unsafe_allow_html=True)
            cols[3].write(reason_text(row))
        if len(result.rows) > 30:
            st.caption(f"※ 先頭30件だけ出しています（全{len(result.rows)}件）。")

    if result.subjects:
        st.markdown(ui.section("記録が1件も無いもの", len(result.subjects), "件"),
                    unsafe_allow_html=True)
        for s in result.subjects:
            is_a = s.kind == "asset"
            label = f"{s.name}（{s.code}）" if is_a else s.name
            st.write(f"　・{label}　:gray[{s.site} ／ {s.role}]")

    st.caption(
        "※ この答えは台帳の判定関数から作っています。"
        "各画面で同じ条件を指定すれば、同じ結果になります。"
    )

    if not AI_API_ENABLED:
        st.caption(
            "※ 外部の AI サービスには接続していません。"
            "打たれた言葉から意図を読み取る仕組みで動いています。"
            "接続する場合も、外部に頼むのは言い換えだけで、"
            "答えの数字と名前は台帳から出します。"
        )


# --- 振り分け -------------------------------------------------------------

if st.session_state.get("editing"):
    page_edit_subject()
elif nav == PAGE_PEOPLE and st.session_state.get("registering") == "person":
    page_register_person()
elif nav == PAGE_PEOPLE:
    page_people()
elif nav == PAGE_ASSETS and st.session_state.get("registering") == "asset":
    page_register_asset()
elif nav == PAGE_ASSETS:
    page_assets()
elif nav == PAGE_SUBMISSION:
    page_submission()
elif nav == PAGE_TYPES:
    page_types()
elif nav == PAGE_AI:
    page_ai()
else:
    st.title(nav)
    st.info(
        f"{NOT_BUILT}。左の「{PAGE_PEOPLE}」か「{PAGE_SUBMISSION}」を選んでください。"
    )

st.caption(
    "※ このデモの変更はブラウザのセッション内にのみ保持され、保存されません。"
    "社員名・施設名はすべて架空です。"
)
