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

from datetime import date

import streamlit as st

from core.models import CATEGORY_LABEL, OBLIGATION_LABEL, Ledger, Record
from core.review import (
    Row,
    SubjectSummary,
    build_rows,
    submission_check,
    summarize_by_subject,
)
from core.schedule import ScheduleError, validate_done_on
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

if st.session_state.pop("_request_show_all", False):
    st.session_state["scope"] = "全員"

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

    st.markdown("**登録**")
    st.button("社員を登録", width="stretch", key="btn-add-person", disabled=True)
    st.button("社員に資格・講習を追加", width="stretch", key="btn-add-qual", disabled=True)
    st.caption("※ 登録はまだ作っていません")

    st.divider()
    st.caption("**使い方で迷ったら**\n\n不明な点は事務所までお問い合わせください。")


lg = ledger()
today = date.today()


# --- 社員の資格・健診 -----------------------------------------------------


def draw_card(summary: SubjectSummary, slot) -> None:
    """カード 1 枚。状態を作り出している資格そのものを出す。"""
    # 見出しには「期限間近（30日以内）」と出すが、カードの中は幅が狭いので短い方を使う。
    with slot.container(border=True):
        st.markdown(f"##### {summary.subject.name}")
        st.caption(f"{summary.subject.site} ／ {summary.subject.role}")
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
            "資格・健診を確認",
            key=f"open-{summary.subject.id}",
            width="stretch",
        ):
            st.session_state["selected"] = summary.subject.id


def draw_grid(items: list[SubjectSummary]) -> None:
    for start in range(0, len(items), CARDS_PER_ROW):
        slots = st.columns(CARDS_PER_ROW)
        for summary, slot in zip(items[start : start + CARDS_PER_ROW], slots):
            draw_card(summary, slot)


def draw_selected_person() -> None:
    """選んだ人の中身。開閉ではなく、常にある領域の中身が入れ替わる。"""
    st.divider()
    st.markdown("#### 選んだ人")

    selected_id = st.session_state.get("selected")
    selected = lg.subject(selected_id) if selected_id else None

    with st.container(border=True):
        if selected is None:
            st.write("上のカードの「資格・健診を確認」を押すと、その人の資格がここに出ます。")
            return

        head, close = st.columns([4, 1])
        head.markdown(f"##### {selected.name}")
        head.caption(f"{selected.site} ／ {selected.role}")
        if close.button("閉じる", width="stretch", key="btn-close-detail"):
            st.session_state.pop("selected", None)
            st.rerun()

        rows = [r for r in build_rows(lg, today) if r.subject.id == selected.id]
        if not rows:
            st.info("この人には資格・講習・健診がまだ登録されていません。")

        for row in rows:
            with st.container(border=True):
                info, action = st.columns([3, 2])

                with info:
                    st.markdown(f"**{row.requirement.name}**")
                    st.caption(
                        f"{CATEGORY_LABEL[row.requirement.category]}／"
                        f"{OBLIGATION_LABEL[row.requirement.obligation]}"
                    )
                    st.write(STATUS_TEXT[row.status])

                    if not row.requirement.has_deadline:
                        st.caption("この資格に有効期限はありません（保有の記録）")
                    elif row.due_on is None:
                        st.caption("前回の日付が入っていないため、次回の期日を出せません")
                    else:
                        st.write(
                            f"期限：{jp_date(row.due_on)} {remaining_text(row.days_left)}"
                        )

                    last = row.holding.last_done_on
                    if row.requirement.date_mode == "cycle":
                        st.caption(
                            f"前回：{jp_date(last) if last else '未入力'}"
                            f"　周期：{row.requirement.cycle_months}か月"
                        )

                with action:
                    if row.requirement.date_mode != "cycle":
                        st.caption("実施日ではなく、証に記載の期限で管理する項目です")
                        continue

                    done_on = st.date_input(
                        "実施した日",
                        value=today,
                        key=f"done-{row.holding.id}",
                    )
                    if st.button(
                        "実施を記録する",
                        key=f"rec-{row.holding.id}",
                        width="stretch",
                    ):
                        try:
                            validate_done_on(done_on, today)
                        except ScheduleError as e:
                            st.error(str(e))
                        else:
                            row.holding.add_record(Record(done_on=done_on))
                            st.session_state["_flash"] = (
                                f"{selected.name} の {row.requirement.name} に "
                                f"{jp_date(done_on)} の実施を記録しました。"
                                "次回の期限を計算し直しました。"
                            )
                            st.rerun()


def page_people() -> None:
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
                st.session_state["_request_show_all"] = True
                st.rerun()

    draw_selected_person()


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


# --- 振り分け -------------------------------------------------------------

if nav == PAGE_PEOPLE:
    page_people()
elif nav == PAGE_SUBMISSION:
    page_submission()
else:
    st.title(nav)
    st.info(
        f"{NOT_BUILT}。左の「{PAGE_PEOPLE}」か「{PAGE_SUBMISSION}」を選んでください。"
    )

st.caption(
    "※ このデモの変更はブラウザのセッション内にのみ保持され、保存されません。"
    "社員名・施設名はすべて架空です。"
)
