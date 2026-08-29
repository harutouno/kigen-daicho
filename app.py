"""期限台帳。

画面はドメイン層（core/）の薄い皮として作る。期日の決め方と判定はすべて core 側にあり、
この層には計算を置かない。

画面の決まりごと:
  * 折りたたまない。押す先はすべて最初から見えている状態にする。
  * 状態を色だけで表さない。色の隣に必ず言葉を置く。
  * 危険なものほど上・左に来る。並びは 期限切れ → 日付未入力 → 期限間近。
  * 期日を計算できないものを「問題なし」に見せない。
  * まだ作っていない操作は、押せる状態で置かない。無効にして理由を書く。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import streamlit as st

from core.models import (
    CATEGORY_LABEL,
    DATE_MODE_LABEL,
    OBLIGATION_LABEL,
    Holding,
    Ledger,
    Record,
    Requirement,
)
from core.review import (
    STATUS_ORDER,
    Row,
    SubjectSummary,
    build_rows,
    submission_check,
    summarize_by_subject,
)
from core.schedule import ScheduleError, add_months, validate_done_on
from core.store import SEED_PATH, load_ledger

st.set_page_config(page_title="期限台帳", page_icon="📋", layout="wide")

CARDS_PER_ROW = 3

PAGE_PEOPLE = "社員の資格・健診"
PAGE_ASSETS = "道具・機器の点検"
PAGE_SUBMISSION = "安全書類の提出前チェック"
PAGE_TYPES = "種類の設定"

SECTIONS: list[tuple[str, str, str, str]] = [
    ("overdue", "🔴", "期限切れ", "期限を過ぎています"),
    ("unknown", "⚪", "日付未入力・期限計算不可", "前回日が未入力で計算できません"),
    ("due_soon", "🟠", "期限間近（30日以内）", "30日以内に期限が来ます"),
]
SECTION_BY_STATUS = {key: (mark, label, note) for key, mark, label, note in SECTIONS}

STATUS_TEXT: dict[str, str] = {
    "overdue": "🔴 期限切れ",
    "unknown": "⚪ 日付未入力",
    "due_soon": "🟠 期限間近",
    "upcoming": "🟢 問題なし",
    "ok": "🟢 問題なし",
}

NOT_BUILT = "この機能はまだ作っていません"


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


# --- 左サイドバー ---------------------------------------------------------

with st.sidebar:
    st.markdown("### 期限台帳")
    st.caption("資格・講習・健診・点検の期限を管理します")

    st.markdown("**メニュー**")
    nav = st.radio(
        "メニュー",
        [PAGE_PEOPLE, PAGE_ASSETS, PAGE_SUBMISSION, PAGE_TYPES],
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
    st.button(add_labels[0], width="stretch", key="btn-add-subject", disabled=True)
    st.caption(
        "※ 新規の登録はまだ作っていません。"
        + ("既存の道具への点検の追加は、その道具を開いてできます。"
           if nav == PAGE_ASSETS
           else "既存の社員への資格の追加は、その人を開いてできます。")
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
    with slot.container(border=True):
        st.markdown(f"##### {subject.name}")
        if is_asset:
            # 道具は名前では特定できない。「絶縁手袋」は何組もあるため、
            # 現物にたどり着くには管理番号が要る。型番は校正や修理を頼むときに要る。
            st.caption(f"管理番号：{subject.code}　／　{subject.site}")
            if subject.model:
                st.caption(f"型番：{subject.model}")
        else:
            st.caption(f"{subject.site} ／ {subject.role}")
        st.markdown(f"**{STATUS_TEXT[summary.worst]}**")

        cause = summary.cause
        if cause is None:
            st.write("対応が必要な項目はありません")
        else:
            st.markdown(f"**{cause.requirement.name}**")
            if cause.due_on is None:
                st.write("前回の日付を入力してください")
            else:
                st.write(
                    f"期限：{jp_date(cause.due_on)} {remaining_text(cause.days_left)}"
                )

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
    if not row.requirement.has_deadline:
        return "🔵 有効（期限なし）"
    return STATUS_TEXT[row.status]


def deadline_text(row: Row) -> str:
    """期限、または期限が出せない理由。"""
    if not row.requirement.has_deadline:
        return "有効期限の定めなし"
    if row.due_on is not None:
        return f"期限：{jp_date(row.due_on)}"
    last = row.holding.last_done_on
    if last is None:
        return "前回実施日：未入力"
    return f"前回実施日：{jp_date(last)}"


def situation_text(row: Row) -> str:
    """残り日数、または次にやること。"""
    if not row.requirement.has_deadline:
        return "期限の定めなし"
    if row.due_on is None:
        return "前回の日付を入力してください"
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
        if not row.requirement.has_deadline:
            counts["no_deadline"] += 1
        elif row.status in ("overdue", "unknown", "due_soon"):
            counts[row.status] += 1
        else:
            counts["ok"] += 1
    return counts


DETAIL_TILES = [
    ("overdue", "🔴 期限切れ", "すぐに対応が必要です"),
    ("unknown", "⚪ 日付未入力・期限計算不可", "日付の入力が必要です"),
    ("due_soon", "🟠 期限間近（30日以内）", "期限が近づいています"),
    ("ok", "🟢 問題なし", "期限内です"),
    ("no_deadline", "🔵 有効（期限なし）", "有効期限の定めなし"),
]


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
    edit.button(
        "この道具の情報を編集する" if is_asset else "この人の情報を編集する",
        key="btn-edit-subject",
        width="stretch",
        disabled=True,
    )

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
        for (key, label, note), slot in zip(DETAIL_TILES, tiles):
            with slot.container(border=True):
                st.markdown(f"{label}")
                st.markdown(f"### {counts[key]}件")
                st.caption(note)

    # 最優先の 1 件。一覧のカードに出しているものと同じ行を、詳細でも先頭に出す。
    urgent = [r for r in rows if r.blocks_assignment or r.status == "due_soon"]
    if urgent:
        top = urgent[0]
        st.markdown("##### 最も優先して対応が必要なもの")
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([2, 2, 1.5, 1.5])
            c1.markdown(f"{state_label(top)}\n\n**{top.requirement.name}**")
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
        cols[2].write(state_label(row))
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


# 修了証などの画像を預かる機能。公開デモでは閉じておく。
#
# 修了証には氏名・生年月日・証番号が写る。社員の画面からは、期限の判定に使わない
# 個人情報（生年月日・連絡先など）を意図的に外している。そこへ画像として同じものを
# 入れ直さないよう、既定では受け付けない。実運用で開けるなら、アクセス制御と
# 保存期間の設計が別に要る。
ATTACHMENTS_ENABLED = False


def next_actions(row: Row) -> list[str]:
    """状態から、次にやることを組み立てる。

    状態を出すだけでは何をすればよいか分からない。判定と同じ材料から作るので、
    画面の説明と判定が食い違うことがない。
    """
    if not row.requirement.has_deadline:
        return ["この資格に有効期限はありません。保有の記録として登録されています。"]

    if row.due_on is None:
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

    st.markdown(f"## {req.name}　{state_label(row)}")
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
            st.markdown(f"### {state_label(row)}")
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
                st.markdown(
                    "
".join(f"{i}. {a}" for i, a in enumerate(actions, start=1))
                )

    # --- 履歴 ---------------------------------------------------------------
    st.markdown("##### 受講・実施の履歴")
    if not holding.records:
        st.info("まだ記録がありません。下の「実施を記録する」から入力してください。")
    else:
        widths = [0.8, 1.6, 1.6, 1.4, 2]
        header = st.columns(widths)
        for slot, label in zip(header, ["回数", "実施日", "この記録による期限", "実施者", "備考"]):
            slot.caption(f"**{label}**")

        # 記録は追記のみ。古い順に並べ、何回目かを示す。
        for i, rec in enumerate(sorted(holding.records, key=lambda r: r.done_on), start=1):
            cols = st.columns(widths)
            cols[0].write(f"{i}回目")
            cols[1].write(jp_date(rec.done_on))
            if req.date_mode == "cycle" and req.cycle_months:
                cols[2].write(jp_date(add_months(rec.done_on, req.cycle_months)))
            else:
                cols[2].write("—")
            cols[3].write(rec.done_by or "—")
            cols[4].write(rec.memo or "—")

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
        st.markdown("**実施を記録する**")
        if req.date_mode != "cycle":
            st.caption(
                "この記録は実施日ではなく、証に記載された期限で管理します。"
                if req.has_deadline
                else "この記録に期限はありません。"
            )
        else:
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

    # --- 添付ファイル（フラグで閉じている） ---------------------------------
    with st.container(border=True):
        st.markdown("**修了証・証明書の画像**")
        if ATTACHMENTS_ENABLED:
            st.file_uploader(
                "画像を追加する", type=["png", "jpg", "jpeg", "pdf"],
                key=f"upload-{holding.id}",
            )
        else:
            st.file_uploader(
                "画像を追加する", type=["png", "jpg", "jpeg", "pdf"],
                key=f"upload-{holding.id}", disabled=True,
            )
            st.caption(
                "※ この機能は公開デモでは無効にしています。"
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
                    id=f"h-new-{len(lg.holdings) + 1:04d}",
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

    by_status: dict[str, list[SubjectSummary]] = {key: [] for key, _, _, _ in SECTIONS}
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

    tiles = st.columns(4)
    for (key, mark, label, note), slot in zip(SECTIONS, tiles):
        with slot.container(border=True):
            st.markdown(f"{mark} **{label}**")
            st.markdown(f"# {len(by_status[key])}人")
            st.caption(note)
    with tiles[3].container(border=True):
        st.markdown("🟢 **問題なし**")
        st.markdown(f"# {len(clear)}人")
        st.caption("期限切れ・期限間近はありません")

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
            if keyword in s.subject.name
            or keyword in s.subject.name.replace(" ", "")
            or keyword in s.subject.site
        ]
        st.markdown(f"##### 🔍 「{keyword}」の検索結果　{len(hits)}人")
        if hits:
            st.caption("表示範囲の設定に関係なく、全員から探しています。")
            draw_grid(hits)
        else:
            st.warning(f"「{keyword}」に一致する人は登録されていません。")
            st.caption(f"※ 新しく登録する機能は{NOT_BUILT}。")
    else:
        shown_clear = st.session_state.get("scope") == "全員"

        any_shown = False
        for key, mark, label, _ in SECTIONS:
            items = by_status[key]
            if not items:
                continue
            any_shown = True
            st.markdown(f"##### {mark} {label}　{len(items)}人")
            draw_grid(items)

        if shown_clear and clear:
            any_shown = True
            st.markdown(f"##### 🟢 問題なし　{len(clear)}人")
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

    by_status: dict[str, list[SubjectSummary]] = {key: [] for key, _, _, _ in SECTIONS}
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

    tiles = st.columns(4)
    for (key, mark, label, note), slot in zip(SECTIONS, tiles):
        with slot.container(border=True):
            st.markdown(f"{mark} **{label}**")
            st.markdown(f"# {len(by_status[key])}件")
            st.caption(note)
    with tiles[3].container(border=True):
        st.markdown("🟢 **問題なし**")
        st.markdown(f"# {len(clear)}件")
        st.caption("期限切れ・期限間近はありません")

    left, right = st.columns([1, 1])

    with left:
        st.markdown("**名称・管理番号・型番・保管場所で検索**")
        st.caption("表示範囲に関係なく全件から探します。「川内」で保管場所も引けます。")
        keyword = st.text_input(
            "検索",
            placeholder="例：絶縁手袋　GLO-002　HIOKI　川内",
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
        st.markdown(f"##### 🔍 「{keyword}」の検索結果　{len(hits)}件")
        if hits:
            st.caption("表示範囲の設定に関係なく、全件から探しています。")
            draw_grid(hits)
        else:
            st.warning(f"「{keyword}」に一致する道具・機器は登録されていません。")
            st.caption(f"※ 新しく登録する機能は{NOT_BUILT}。")
    else:
        shown_clear = st.session_state.get("scope-asset") == "すべて"

        any_shown = False
        for key, mark, label, _ in SECTIONS:
            items = by_status[key]
            if not items:
                continue
            any_shown = True
            st.markdown(f"##### {mark} {label}　{len(items)}件")
            draw_grid(items)

        if shown_clear and clear:
            any_shown = True
            st.markdown(f"##### 🟢 問題なし　{len(clear)}件")
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
        by_person.setdefault(row.subject.name, []).append(row)

    for name, items in by_person.items():
        head = items[0].subject
        with st.container(border=True):
            st.markdown(f"**{name}**　:gray[{head.site} ／ {head.role}]")
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
    name_to_id = {s.name: s.id for s in people}

    with target:
        st.markdown("**現場に出す人**")
        st.caption("選ばなければ、全員を見ます")
        picked = st.multiselect(
            "現場に出す人",
            list(name_to_id.keys()),
            label_visibility="collapsed",
            key="submit-people",
        )

    subject_ids = [name_to_id[n] for n in picked] if picked else None

    if target_date < today:
        st.warning("過去の日付が入っています。これから提出する日を入れてください。")
        return

    issues = submission_check(lg, target_date=target_date, subject_ids=subject_ids)

    scope_text = f"選んだ{len(picked)}人" if picked else f"全員（{len(people)}人）"
    st.caption(f"対象：{scope_text}")

    if not issues:
        st.success(
            f"{jp_date(target_date)} に提出しても、引っかかる項目はありません。",
            icon="✅",
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
        f"{jp_date(target_date)} に提出すると、{len(issues)}件が引っかかります。",
        icon="⚠️",
    )

    if upcoming:
        st.markdown(f"##### 🟠 その日までに切れるもの　{len(upcoming)}件")
        st.caption(
            "今日の時点ではまだ有効です。社員の一覧を見ているだけでは気づけません。"
        )
        draw_issue_group(upcoming)

    if already:
        st.markdown(f"##### 🔴 今日すでに引っかかっているもの　{len(already)}件")
        st.caption("提出する日に関係なく、いま対応が必要です。")
        draw_issue_group(already)


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


# --- 振り分け -------------------------------------------------------------

if nav == PAGE_PEOPLE:
    page_people()
elif nav == PAGE_ASSETS:
    page_assets()
elif nav == PAGE_SUBMISSION:
    page_submission()
elif nav == PAGE_TYPES:
    page_types()
else:
    st.title(nav)
    st.info(
        f"{NOT_BUILT}。左の「{PAGE_PEOPLE}」か「{PAGE_SUBMISSION}」を選んでください。"
    )

st.caption(
    "※ このデモの変更はブラウザのセッション内にのみ保持され、保存されません。"
    "社員名・施設名はすべて架空です。"
)
