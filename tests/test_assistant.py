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
    """件数だけを見ていたため、まったく別の行を返しても通っていた。

    「30日以内に切れるものは？」には「切れ」が入っているので、
    期限切れの照会に先に捕まり、12日後の健診ではなく期限切れの
    資格者証を返していた。期限切れも1件、30日以内も1件だったので、
    件数の一致だけでは気づけなかった。

    どの行を返したかまで見る。
    """
    lg = make_ledger()
    a = answer(lg, "30日以内に切れるものは？", TODAY)

    assert a.kind == "query"
    assert len(a.rows) == 1
    row = a.rows[0]
    assert row.holding.id == "h2"                     # 健診であること
    assert row.requirement.name == "定期健康診断"
    assert row.status == "due_soon"                   # 期限切れではないこと
    assert row.due_on == date(2026, 9, 10)


def test_期限切れの照会と日数指定の照会が別物であること():
    """同じ台帳で、返る行が実際に違うことを確かめる。"""
    lg = make_ledger()
    overdue = answer(lg, "期限が切れているものは？", TODAY)
    within = answer(lg, "30日以内に切れるものは？", TODAY)

    assert [r.holding.id for r in overdue.rows] == ["h1"]
    assert [r.holding.id for r in within.rows] == ["h2"]


def test_今月は30日以内と同じ意味ではない():
    """8月30日に「今月」と聞かれたら 8月31日までを指す。

    以前は soon_days（30日）に読み替えていた。それでは9月末までになる。
    """
    lg = make_ledger()
    a = answer(lg, "今月切れるものは？", TODAY)

    assert a.kind == "query"
    assert "2026-08-31" in a.headline
    # 健診の期日は 9月10日なので、今月には入らない
    assert a.rows == []


def test_期限切れの照会も種別で絞る():
    """「期限切れの人」に道具が混ざっていた。

    種別の絞り込みを分岐ごとに書いていたため、直した分岐だけ効いていた。
    """
    lg = make_ledger()
    lg.subjects.append(Subject(id="a1", name="絶縁手袋", kind="asset", code="G-1"))
    lg.holdings.append(Holding(id="h9", subject_id="a1", requirement_id="cert",
                               fixed_due_on=date(2026, 1, 1)))   # 期限切れ

    both = answer(lg, "期限が切れているものは？", TODAY)
    assert {r.subject.kind for r in both.rows} == {"person", "asset"}

    people = answer(lg, "期限切れの人を教えて", TODAY)
    assert {r.subject.kind for r in people.rows} == {"person"}

    assets = answer(lg, "期限切れの道具を教えて", TODAY)
    assert {r.subject.kind for r in assets.rows} == {"asset"}


def test_提出日の件数が提出前チェックと同じ計算から出る():
    """AI だけ独自に足し算していた。前に画面14件・README12件になった構造。"""
    from core.review import preflight_check

    lg = make_ledger()
    lg.subjects.append(Subject(id="p9", name="新入 太郎", kind="person"))

    a = answer(lg, "2027年3月31日に提出したら何が引っかかる？", TODAY)
    expected = preflight_check(lg, target_date=date(2027, 3, 31))

    assert str(expected.blocked) in a.headline
    assert len(a.rows) == len(expected.issues)
    assert len(a.subjects) == len(expected.unrecorded)


def test_提出日の照会も種別で絞る():
    from core.review import preflight_check

    lg = make_ledger()
    lg.subjects.append(Subject(id="a1", name="絶縁手袋", kind="asset", code="G-1"))

    a = answer(lg, "2027年3月31日に社員の書類を提出したら？", TODAY)
    expected = preflight_check(
        lg, target_date=date(2027, 3, 31),
        subject_ids=[s.id for s in lg.subjects if s.kind == "person"],
    )
    assert str(expected.blocked) in a.headline
    assert all(s.kind == "person" for s in a.subjects)


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


# --- 言い換え役を差し込んだ場合 -------------------------------------------


def test_言い換えられれば答えられる():
    """言い換え役は言い換えるだけ。件数は台帳から出る。"""
    lg = make_ledger()

    def fake(_q: str) -> str:
        return "期限が切れているものを教えて"

    a = answer(lg, "うちでやばいのある？", TODAY, normalizer=fake)
    assert a.kind == "query"
    assert "言い換え" not in a.headline
    assert "として受け取りました" in a.lines[0]
    assert len(a.rows) == 1


def test_言い換えられなければ答えられないと言う():
    lg = make_ledger()

    def fake(_q: str) -> None:
        return None

    a = answer(lg, "今日の天気は？", TODAY, normalizer=fake)
    assert a.kind == "unknown"


def test_言い換え役が嘘の答えを返しても件数は台帳から出る():
    """言い換え役が『5件です』のような文を返しても、それは答えにならない。"""
    lg = make_ledger()

    def fake(_q: str) -> str:
        return "期限が切れているものを教えて（5件です）"

    a = answer(lg, "なんか教えて", TODAY, normalizer=fake)
    # 台帳の実数は 1 件。言い換え役の言う 5 件にはならない。
    assert len(a.rows) == 1
    assert "5件" not in a.headline


# --- 日付の解釈 -----------------------------------------------------------


def test_年の無い日付は推測せず聞き返す():
    """「8月1日」が今年か来年かは書いた人にしか分からない。

    外すと提出日を1年ずらして判定することになる。
    """
    a = answer(make_ledger(), "8月1日に提出したら何が引っかかる？", TODAY)
    assert a.kind == "unknown"
    assert "年" in a.headline
    assert a.rows == []


def test_存在しない日付で落ちない():
    """2026年2月30日 のような入力で例外にしない。"""
    a = answer(make_ledger(), "2026年2月30日に提出したら？", TODAY)
    assert a.kind == "unknown"


def test_Nか月後は台帳と同じ月計算にする():
    """本体が add_months（月末クランプ）なのに、AI だけ30日だと
    同じ「1か月」が違う日を指す。"""
    from core.assistant import _parse_date
    from core.schedule import add_months

    got = _parse_date("6か月後に提出したら？", date(2026, 8, 31))
    assert got == add_months(date(2026, 8, 31), 6)
    assert got == date(2027, 2, 28)   # 月末はクランプされる


def test_N日後はそのまま日数で数える():
    from core.assistant import _parse_date

    assert _parse_date("30日後に出す", TODAY) == date(2026, 9, 28)


# --- 対象を取り違えない ---------------------------------------------------


def test_同姓同名は片方を選ばずに聞き返す():
    """画面は ID で扱うよう直したのに、AI だけ名前で当てていた。

    片方を選ぶと、期限切れの方を黙って外して
    「対応は必要ありません」と答えうる。
    """
    lg = make_ledger()
    lg.subjects.append(Subject(id="p9", name="迫田 和樹", kind="person",
                               code="E-0099", site="奄美支店"))
    a = answer(lg, "迫田 和樹さんは大丈夫？", TODAY)

    assert a.kind == "unknown"
    assert "2人" in a.headline
    assert a.rows == []
    # どちらか選べるように、手がかりを見せること
    assert any("E-0001" in line for line in a.lines)
    assert any("E-0099" in line for line in a.lines)


def test_社員番号を付ければ答えられる():
    lg = make_ledger()
    lg.subjects.append(Subject(id="p9", name="迫田 和樹", kind="person",
                               code="E-0099", site="奄美支店"))
    a = answer(lg, "E-0099 の迫田 和樹さんは大丈夫？", TODAY)
    assert a.kind == "query"


def test_人を聞かれて道具を混ぜない():
    """件数がそのまま食い違う。"""
    lg = make_ledger()
    lg.subjects.append(Subject(id="p3", name="新入 太郎", kind="person"))
    lg.subjects.append(Subject(id="a1", name="絶縁手袋 新品", kind="asset",
                               code="GLO-9"))

    a = answer(lg, "資格情報が無い人は？", TODAY)
    assert {s.kind for s in a.subjects} == {"person"}
    assert "新入 太郎" in [s.name for s in a.subjects]

    b = answer(lg, "点検情報が無い道具は？", TODAY)
    assert [s.name for s in b.subjects] == ["絶縁手袋 新品"]

    # どちらとも取れない聞き方なら、決めつけずに両方返す
    c = answer(lg, "登録されていないものは？", TODAY)
    assert {s.kind for s in c.subjects} == {"person", "asset"}


def test_固定期限に前回実施日を探させない():
    """免許証に「前回の実施日」は無い。あるのは証に書かれた有効期限。"""
    lg = Ledger(
        requirements=[
            Requirement(id="men", name="運転免許証", category="qualification",
                        obligation="legal", date_mode="fixed"),
            Requirement(id="k", name="定期健康診断", category="qualification",
                        obligation="legal", date_mode="cycle", cycle_months=12),
        ],
        subjects=[Subject(id="p1", name="甲", kind="person")],
        holdings=[Holding(id="h1", subject_id="p1", requirement_id="men"),
                  Holding(id="h2", subject_id="p1", requirement_id="k")],
    )
    a = answer(lg, "日付未入力のものは？", TODAY)

    assert "前回の日付" not in a.headline
    assert len(a.rows) == 2
    joined = "".join(a.lines)
    assert "前回の実施日が未入力：1件" in joined
    assert "有効期限が未入力：1件" in joined
