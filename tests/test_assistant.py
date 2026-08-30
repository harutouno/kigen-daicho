"""AIサポートの検証。

一番大事なのは「分からないときに分からないと言う」ことなので、
答えられる場合と同じくらい、答えられない場合を確かめる。

もう一つ大事なのは、答えが台帳の判定と一致していること。
文章を組み立てて返すと、画面が3件と言っているのに4件と答える、
という食い違いが起きる。同じ関数から作っていることを確かめる。
"""

from __future__ import annotations

from datetime import date

from core.assistant import answer
from core.models import Holding, Ledger, Record, Requirement, Subject
from core.review import build_rows, submission_check

TODAY = date(2026, 8, 29)


def make_ledger() -> Ledger:
    return Ledger(
        requirements=[
            Requirement(id="cert", name="監理技術者資格者証",
                        category="qualification", obligation="legal",
                        date_mode="fixed"),
            Requirement(id="kenshin", name="定期健康診断",
                        category="qualification", obligation="legal",
                        date_mode="cycle", cycle_months=12),
        ],
        subjects=[
            Subject(id="p1", name="迫田 和樹", kind="person",
                    site="本社", role="施工管理", code="E-0001"),
            Subject(id="p2", name="東 亮", kind="person",
                    site="本社", role="電気工事", code="E-0002"),
        ],
        holdings=[
            # 切れている
            Holding(id="h1", subject_id="p1", requirement_id="cert",
                    fixed_due_on=date(2026, 6, 30)),
            # 間近（12日後）
            Holding(id="h2", subject_id="p1", requirement_id="kenshin",
                    records=[Record(done_on=date(2025, 9, 10))]),
        ],
    )


# --- 分からないときに、分からないと言う -----------------------------------


def test_答えられない聞き方には答えられないと言う():
    a = answer(make_ledger(), "今日の天気は？", TODAY)
    assert a.kind == "unknown"
    assert "答えられません" in a.headline
    assert a.rows == []


def test_空の入力には入力を促す():
    a = answer(make_ledger(), "   ", TODAY)
    assert a.kind == "unknown"


def test_分からないときに件数を作らない():
    """推測して数字を返さないこと。食い違いの原因になる。"""
    a = answer(make_ledger(), "よくわからないけどなんとかして", TODAY)
    assert a.kind == "unknown"
    assert a.rows == []
    assert a.subjects == []


# --- 答えが台帳の判定と一致している ---------------------------------------


def test_期限切れの件数が台帳と一致する():
    lg = make_ledger()
    a = answer(lg, "期限が切れているものを教えて", TODAY)

    expected = [r for r in build_rows(lg, TODAY) if r.status == "overdue"]
    assert a.kind == "query"
    assert len(a.rows) == len(expected)
    assert str(len(expected)) in a.headline


def test_提出日を指定した答えが提出前チェックと一致する():
    lg = make_ledger()
    a = answer(lg, "2027年3月31日に提出したら何が引っかかる？", TODAY)

    expected = submission_check(lg, target_date=date(2027, 3, 31))
    assert a.kind == "query"
    assert len(a.rows) == len(expected)


def test_日付を拾えなければ提出の質問として扱わない():
    """日付を推測して勝手に決めない。"""
    a = answer(make_ledger(), "提出したらどうなる？", TODAY)
    assert a.kind != "query" or not a.rows


def test_名前で対象を引ける():
    a = answer(make_ledger(), "迫田 和樹さんは大丈夫？", TODAY)
    assert a.kind == "query"
    assert "迫田 和樹" in a.headline


def test_記録が無い人はその旨を答える():
    lg = make_ledger()
    lg.subjects.append(Subject(id="p3", name="新入 太郎", kind="person"))
    a = answer(lg, "新入 太郎さんは大丈夫？", TODAY)
    assert "記録が1件もありません" in a.headline
    assert "何も分かっていない" in "".join(a.lines)


def test_日数を指定した照会ができる():
    lg = make_ledger()
    a = answer(lg, "30日以内に切れるものは？", TODAY)
    assert a.kind == "query"
    # 健診の期日は 2026-09-10（12日後）なので入る
    assert len(a.rows) == 1


# --- 案内は行き先を指す ---------------------------------------------------


def test_社員の登録は登録ボタンを指す():
    a = answer(make_ledger(), "社員を登録したい", TODAY)
    assert a.kind == "guide"
    assert a.highlight == "btn-add-subject"


def test_資格の追加は社員の登録と取り違えない():
    """「社員に資格を追加」を「社員を登録」と混同しない。"""
    a = answer(make_ledger(), "社員に資格を追加したい", TODAY)
    assert a.kind == "guide"
    assert "資格" in a.headline
    assert a.highlight != "btn-add-subject"


def test_案内は書き込みを行わない():
    """AI は指すだけ。台帳を変えない。"""
    lg = make_ledger()
    before = (len(lg.subjects), len(lg.holdings))
    for q in ("社員を登録したい", "実施を記録したい", "期限が切れているものを教えて"):
        answer(lg, q, TODAY)
    assert (len(lg.subjects), len(lg.holdings)) == before
