"""期限台帳 — 安全書類が差し戻される原因を、出す前に潰すための台帳。

画面はドメイン層（core/）の薄い皮として作る。
期日の決め方と判定はすべて core 側にあり、この層には計算を置かない。
"""

from __future__ import annotations

import io
from datetime import date

import pandas as pd
import streamlit as st

from core.models import (
    CATEGORY_LABEL,
    DATE_MODE_LABEL,
    OBLIGATION_LABEL,
    Ledger,
    Record,
)
from core.review import Row, assignment_check, build_rows, submission_check, summarize
from core.schedule import STATUS_LABEL, ScheduleError, validate_done_on
from core.store import SEED_PATH, load_ledger

STATUS_MARK = {
    "overdue": "🔴 超過",
    "unknown": "⚪ 未確定",
    "due_soon": "🟠 間近",
    "upcoming": "🟡 予告",
    "ok": "🟢 余裕",
}

st.set_page_config(page_title="期限台帳", page_icon="📋", layout="wide")


# --- 状態 -----------------------------------------------------------------


def ledger() -> Ledger:
    if "ledger" not in st.session_state:
        st.session_state.ledger = load_ledger(SEED_PATH)
    return st.session_state.ledger


def rows_frame(rows: list[Row]) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "状態": STATUS_MARK[r.status],
                "対象": r.subject.name,
                "拠点": r.subject.site,
                "種別": r.requirement.name,
                "区分": OBLIGATION_LABEL[r.requirement.obligation],
                "期日": r.due_on.isoformat() if r.due_on else "—",
                # 未確定は欠損として保持する。0 や文字列で埋めると数値として扱えなくなる。
                "残日数": pd.NA if r.days_left is None else r.days_left,
                "前回実施": (
                    r.holding.last_done_on.isoformat() if r.holding.last_done_on else "—"
                ),
                "備考": r.holding.note,
            }
            for r in rows
        ]
    )
    if not frame.empty:
        frame["残日数"] = frame["残日数"].astype("Int64")
    return frame


# --- サイドバー -----------------------------------------------------------

with st.sidebar:
    st.header("表示条件")

    as_of = st.date_input(
        "基準日",
        value=date.today(),
        help="この日付の時点で判定します。今日以外の日を入れられることがこの台帳の要点です。",
    )

    lg = ledger()

    sites = ["すべて"] + lg.sites
    site = st.selectbox("拠点", sites)

    category = st.selectbox(
        "区分", ["すべて", "資格・講習・健診", "点検・校正"]
    )

    st.divider()
    st.caption("警告のしきい値")
    lg.soon_days = st.number_input("『間近』とする日数", 1, 365, lg.soon_days)
    lg.upcoming_days = st.number_input("『予告』とする日数", 1, 730, lg.upcoming_days)
    if lg.soon_days > lg.upcoming_days:
        st.error("『間近』は『予告』以下にしてください。")
        st.stop()

    st.divider()
    if st.button("同梱データに戻す", width="stretch"):
        st.session_state.ledger = load_ledger(SEED_PATH)
        st.rerun()


def filtered(rows: list[Row]) -> list[Row]:
    out = rows
    if site != "すべて":
        out = [r for r in out if r.subject.site == site]
    if category != "すべて":
        out = [r for r in out if CATEGORY_LABEL[r.requirement.category] == category]
    return out


# --- 見出し ---------------------------------------------------------------

st.title("期限台帳")
st.caption(
    "安全書類が差し戻される原因を、提出する前に洗い出すための台帳です。"
    "資格・講習・健診の期限と、点検・校正の期日を同じ仕組みで扱います。"
)

st.warning(
    "**これはデモです。** 社員名・施設名はすべて架空で、実在の個人や顧客とは関係ありません。"
    "変更はブラウザのセッション内にのみ保持され、保存されません。",
    icon="⚠️",
)

all_rows = build_rows(lg, as_of)
view_rows = filtered(all_rows)
counts = summarize(view_rows)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("🔴 超過", counts["overdue"])
c2.metric("⚪ 未確定", counts["unknown"])
c3.metric("🟠 間近", counts["due_soon"])
c4.metric("🟡 予告", counts["upcoming"])
c5.metric("🟢 余裕", counts["ok"])

st.divider()

tab_list, tab_submit, tab_assign, tab_master = st.tabs(
    ["一覧", "提出前チェック", "配置チェック", "種別マスタ"]
)


# --- 一覧 -----------------------------------------------------------------

with tab_list:
    st.subheader(f"{as_of} 時点の一覧")
    st.caption(
        "期日が確定していない行を先頭に出します。放置されやすいのは「切れているもの」より"
        "「そもそも分かっていないもの」だからです。"
    )

    if not view_rows:
        st.info("該当する行がありません。")
    else:
        st.dataframe(rows_frame(view_rows), width="stretch", hide_index=True)

        buf = io.StringIO()
        rows_frame(view_rows).to_csv(buf, index=False)
        st.download_button(
            "CSV で書き出す",
            buf.getvalue().encode("utf-8-sig"),
            file_name=f"kigen_{as_of}.csv",
            mime="text/csv",
        )

    st.divider()
    st.subheader("実施を記録する")
    st.caption(
        "記録を追加すると、次回期日が自動で立ち直します。前回実施日は別に保持せず、"
        "記録の中の最新日から導出しています。"
    )

    cyclic = [r for r in view_rows if r.requirement.date_mode == "cycle"]
    if not cyclic:
        st.info("周期で管理する行がありません。")
    else:
        labels = {
            f"{r.subject.name}／{r.requirement.name}": r.holding.id for r in cyclic
        }
        col_a, col_b, col_c = st.columns([3, 2, 2])
        picked = col_a.selectbox("対象", list(labels.keys()))
        done_on = col_b.date_input("実施日", value=as_of, key="done_on")
        done_by = col_c.text_input("実施者", value="")

        if st.button("記録する", type="primary"):
            try:
                validate_done_on(done_on, as_of)
            except ScheduleError as e:
                st.error(str(e))
            else:
                holding_id = labels[picked]
                target = next(h for h in lg.holdings if h.id == holding_id)
                target.add_record(Record(done_on=done_on, done_by=done_by))
                st.success(f"{picked} に {done_on} の実施を記録しました。")
                st.rerun()


# --- 提出前チェック -------------------------------------------------------

with tab_submit:
    st.subheader("提出前チェック")
    st.markdown(
        "安全書類の差し戻し原因として、**資格証の期限切れ**と**健康診断日の年度跨ぎ**が"
        "挙げられています。どちらも書類を作った日ではなく、**提出する日や工期の終わりの時点**で"
        "切れているために起きます。ここでは提出予定日を入れて、その日に通らないものを洗い出します。"
    )

    col_a, col_b = st.columns([1, 2])
    target_date = col_a.date_input("提出予定日／工期末", value=as_of, key="target")
    names = {s.name: s.id for s in lg.subjects if s.kind == "person"}
    picked_names = col_b.multiselect(
        "対象者（未選択なら全件）", list(names.keys()), default=[]
    )
    subject_ids = [names[n] for n in picked_names] if picked_names else None

    issues = submission_check(lg, target_date=target_date, subject_ids=subject_ids)

    if not issues:
        st.success(f"{target_date} 時点で、書類を止める行はありません。")
    else:
        st.error(f"{target_date} 時点で {len(issues)} 件が書類を止めます。")
        st.dataframe(rows_frame(issues), width="stretch", hide_index=True)

    st.caption(
        "「期日が未確定」の行も通しません。分からないものを大丈夫として扱うと、"
        "台帳を持つ意味がなくなるためです。"
    )


# --- 配置チェック ---------------------------------------------------------

with tab_assign:
    st.subheader("配置チェック")
    st.markdown(
        "**監理技術者は、資格者証と講習の両方が有効でなければ配置できません。**\n\n"
        "この二つは別々に期限が進み、しかも講習の有効期間は「受講日の翌年1月1日から"
        "5年後の12月31日まで」という数え方です。片方だけを見ていると通してしまいます。"
    )

    col_a, col_b = st.columns([1, 2])
    person_names = {s.name: s.id for s in lg.subjects if s.kind == "person"}
    who = col_a.selectbox("配置する人", list(person_names.keys()))

    req_names = {
        r.name: r.id
        for r in lg.requirements
        if r.category == "qualification" and r.has_deadline
    }
    default_reqs = [n for n in ("監理技術者資格者証", "監理技術者講習") if n in req_names]
    needed = col_b.multiselect(
        "この現場に必要な条件", list(req_names.keys()), default=default_reqs
    )

    check_on = st.date_input("配置する日", value=as_of, key="assign_on")

    if not needed:
        st.info("必要な条件を選んでください。")
    else:
        ok, reasons = assignment_check(
            lg,
            subject_id=person_names[who],
            required_requirement_ids=[req_names[n] for n in needed],
            as_of=check_on,
        )
        if ok:
            st.success(f"{check_on} 時点で、{who} さんを配置できます。")
        else:
            st.error(f"{check_on} 時点で、{who} さんは配置できません。")
            for reason in reasons:
                st.write(f"- {reason}")


# --- 種別マスタ -----------------------------------------------------------

with tab_master:
    st.subheader("種別マスタ")
    st.markdown(
        "**周期も警告日数もコードに書き込んでいません。**\n\n"
        "実際の周期は設備の条件や社内規程で変わり、外部の人間が決め打ちできるものではないためです。"
        "ここで登録・変更したものが、そのまま判定に使われます。"
    )

    master = pd.DataFrame(
        [
            {
                "種別": r.name,
                "区分": CATEGORY_LABEL[r.category],
                "義務": OBLIGATION_LABEL[r.obligation],
                "期日の決まり方": DATE_MODE_LABEL[r.date_mode],
                "周期(月)": pd.NA if r.cycle_months is None else r.cycle_months,
                "根拠": r.source,
                "備考": r.note,
            }
            for r in lg.requirements
        ]
    )
    master["周期(月)"] = master["周期(月)"].astype("Int64")
    st.dataframe(master, width="stretch", hide_index=True)

    st.divider()
    st.markdown(
        "#### 期限を持たない種別について\n"
        "電気主任技術者の免状、第二種電気工事士の免状、認定電気工事従事者、"
        "技能講習の修了証、特別教育などには有効期限がありません。"
        "これらは「誰が持っているか」を把握するために登録しますが、期限としては扱いません。"
        "**期限のないものを期限として並べると、台帳の信頼が落ちるためです。**"
    )


# --- 脚注 -----------------------------------------------------------------

st.divider()
with st.expander("このデモで作っていないもの"):
    st.markdown(
        "意図的に外しています。隠すと嘘になるので明記します。\n\n"
        "- ログイン、複数ユーザー、権限の分離\n"
        "- サーバーへの保存（変更はセッション内のみ。再読み込みで消えます）\n"
        "- CSV の取り込み（書き出しのみ実装）\n"
        "- 通知・メール送信\n"
        "- 実在のデータ\n\n"
        "期限の扱い方を示すことが目的であり、そこに関係しない部分は入れていません。"
    )

with st.expander("周期・根拠の出典"):
    st.markdown(
        "初期データの周期は、次の公開情報を参考にした**例示**です。"
        "実際の周期は設備・契約・社内規程により異なるため、種別マスタで変更できます。\n\n"
        "- 監理技術者資格者証／監理技術者講習: 建設業法および同施行規則"
        "（令和3年1月1日改正で講習の有効期間の数え方が変更）\n"
        "- 第一種電気工事士 定期講習: 電気工事士法\n"
        "- 危険物取扱者 保安講習: 消防法\n"
        "- 定期健康診断: 労働安全衛生法\n"
        "- 自家用電気工作物の月次・年次点検: 電気事業法\n"
        "- 測定機器の校正、内部監査: ISO 9001:2015"
    )
