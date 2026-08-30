"""画面の見た目。

Streamlit の既定の見た目のままだと、状態が絵文字と文字だけで表され、
どれが危ないのか一目で分からない。色と面で差を付けるための CSS と、
状態を表す部品をここにまとめる。

決まりごとは変えない:
  * 色だけで状態を表さない。ピルの中には必ず言葉が入る。
  * 危険なものほど強い色。問題なしは目立たせない。

配色は 5 つの状態に対して、背景・枠・文字の 3 色を組にして持つ。
背景だけ変えて文字色を据え置くと、淡い色の上で読みにくくなるため。
"""

from __future__ import annotations

import streamlit as st

# 状態ごとの (表示名, 短い表示名, 背景, 枠, 文字)
STATE_STYLE: dict[str, tuple[str, str, str, str, str]] = {
    "overdue":  ("期限切れ", "期限切れ", "#FEF2F2", "#FCA5A5", "#B91C1C"),
    "unknown":  ("日付未入力・期限計算不可", "日付未入力", "#F1F5F9", "#CBD5E1", "#475569"),
    "due_soon": ("期限間近", "期限間近", "#FFFBEB", "#FCD34D", "#B45309"),
    "ok":       ("問題なし", "問題なし", "#F0FDF4", "#86EFAC", "#15803D"),
    "none":     ("有効（期限なし）", "有効（期限なし）", "#EFF6FF", "#93C5FD", "#1D4ED8"),
    # 赤・橙・緑は意味を持っているので使えず、灰は日付未入力が使っている。
    # 対象が人か道具かで言葉を変える。道具の画面で「資格情報なし」では意味が通らない。
    "unregistered": ("資格情報なし", "資格情報なし", "#F5F3FF", "#C4B5FD", "#6D28D9"),
    "unregistered_asset": ("点検情報なし", "点検情報なし", "#F5F3FF", "#C4B5FD", "#6D28D9"),
}

CSS = """
<style>
/* ---- 全体 ---------------------------------------------------------- */
.stApp { background: #F8FAFC; }
.block-container { padding-top: 2.2rem; max-width: 1500px; }

h1, h2, h3, h4, h5 { color: #0F172A; letter-spacing: .01em; }
h1 { font-weight: 700; }

/* ---- サイドバー ------------------------------------------------------ */
section[data-testid="stSidebar"] {
  background: #FFFFFF;
  border-right: 1px solid #E2E8F0;
}
section[data-testid="stSidebar"] .stRadio label {
  padding: 6px 10px;
  border-radius: 8px;
  width: 100%;
}
section[data-testid="stSidebar"] .stRadio label:hover { background: #F1F5F9; }

/* ---- カード ---------------------------------------------------------- */
/* Streamlit は st.container(border=True, key=...) の枠を、
   key の class が付いた要素そのものに描く。祖先をたどる必要はない。 */
[class*="st-key-card-"] {
  background: #FFFFFF !important;
  border: 1px solid #E2E8F0 !important;
  border-radius: 12px !important;
  box-shadow: 0 1px 2px rgba(15, 23, 42, .05);
  transition: box-shadow .12s ease, border-color .12s ease;
  height: 100%;
}
[class*="st-key-card-"]:hover {
  border-color: #94A3B8 !important;
  box-shadow: 0 6px 16px rgba(15, 23, 42, .10);
}

/* 状態ごとの色帯。
   枠の border-left に色を当てようとしたが、Streamlit 側の指定に負けて色だけ
   反映されなかった（太さと角丸は効くのに色は効かない）。カスケードを競うのを
   やめ、自分の要素として描く。色だけに意味を持たせないため、帯は補助であり、
   カードの中には必ず状態のピル（言葉入り）がある。 */
.kd-bar {
  height: 4px;
  border-radius: 2px;
  margin: 0 0 2px 0;
}

/* ---- 状態のピル ------------------------------------------------------ */
.kd-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12.5px;
  font-weight: 700;
  line-height: 1.6;
  white-space: nowrap;
  border: 1px solid transparent;
}
.kd-pill .kd-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: currentColor; flex: none;
}

/* ---- 集計タイル ------------------------------------------------------ */
.kd-tile {
  border-radius: 12px;
  padding: 14px 16px;
  border: 1px solid;
  height: 100%;
}
.kd-tile .kd-tile-label { font-size: 12.5px; font-weight: 700; }
.kd-tile .kd-tile-count {
  font-size: 30px; font-weight: 800; line-height: 1.25;
  font-variant-numeric: tabular-nums;
}
.kd-tile .kd-tile-count small { font-size: 14px; font-weight: 700; margin-left: 2px; }
.kd-tile .kd-tile-note { font-size: 11.5px; color: #64748B; }

/* ---- セクション見出し ------------------------------------------------ */
.kd-section {
  display: flex; align-items: center; gap: 10px;
  margin: 26px 0 10px;
}
.kd-section .kd-section-title { font-size: 15px; font-weight: 700; color: #0F172A; }
.kd-section .kd-section-count {
  font-size: 12px; font-weight: 700; color: #475569;
  background: #F1F5F9; border-radius: 999px; padding: 2px 9px;
}
.kd-section .kd-section-rule { flex: 1; height: 1px; background: #E2E8F0; }

/* ---- 表 -------------------------------------------------------------- */
.kd-th {
  font-size: 11.5px; font-weight: 700; color: #64748B;
  letter-spacing: .04em;
}
[class*="st-key-thead"] { border-bottom: 2px solid #CBD5E1; padding-bottom: 4px; }
[class*="st-key-trow-"] {
  border-bottom: 1px solid #EEF2F6;
  padding: 2px 0;
}
[class*="st-key-trow-"]:hover { background: #F8FAFC; }

/* 表の中の「名称」は押せる。リンクらしく見せる */
[class*="st-key-to-holding-"] button {
  justify-content: flex-start !important;
  padding-left: 0 !important;
  color: #1D4ED8 !important;
  font-weight: 600 !important;
}
[class*="st-key-to-holding-"] button:hover { text-decoration: underline; }

/* ---- ボタン ---------------------------------------------------------- */
.stButton button { border-radius: 8px; font-weight: 600; }
.stButton button[kind="primary"] { box-shadow: 0 1px 2px rgba(29,78,216,.25); }

/* ---- 数字の桁を揃える ------------------------------------------------ */
.kd-num { font-variant-numeric: tabular-nums; }
</style>
"""


def inject() -> None:
    """CSS を 1 度だけ差し込む。"""
    st.markdown(CSS, unsafe_allow_html=True)


def pill(state: str, *, short: bool = False) -> str:
    """状態を表すピルの HTML。色だけでなく必ず言葉を含む。"""
    label, short_label, bg, border, fg = STATE_STYLE[state]
    text = short_label if short else label
    return (
        f'<span class="kd-pill" style="background:{bg};border-color:{border};color:{fg}">'
        f'<span class="kd-dot"></span>{text}</span>'
    )


def bar(state: str) -> str:
    """カード上端の色帯。状態の塊を一覧で見分けやすくするための補助。"""
    _, _, _, border, fg = STATE_STYLE[state]
    color = fg if state != "ok" else border
    return f'<div class="kd-bar" style="background:{color}"></div>'


def tile(state: str, count: int, note: str, unit: str = "人") -> str:
    """集計タイルの HTML。"""
    label, _, bg, border, fg = STATE_STYLE[state]
    return (
        f'<div class="kd-tile" style="background:{bg};border-color:{border}">'
        f'<div class="kd-tile-label" style="color:{fg}">{label}</div>'
        f'<div class="kd-tile-count" style="color:{fg}">{count}<small>{unit}</small></div>'
        f'<div class="kd-tile-note">{note}</div>'
        f"</div>"
    )


def section(title: str, count: int, unit: str = "人") -> str:
    """セクション見出しの HTML。件数を添え、右へ罫線を伸ばす。"""
    return (
        '<div class="kd-section">'
        f'<span class="kd-section-title">{title}</span>'
        f'<span class="kd-section-count">{count}{unit}</span>'
        '<span class="kd-section-rule"></span>'
        "</div>"
    )
