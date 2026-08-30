"""台帳の判定の検証。

とくに「基準日を変えると結論が変わる」ことを確認する。
この台帳の存在理由がそこにあるため。
"""

from __future__ import annotations

from datetime import date

import pytest

from core.models import Holding, Ledger, Record, Requirement, Subject
from core.review import (
    assignment_check,
    build_rows,
    preflight_check,
    submission_check,
    summarize,
    summarize_by_subject,
    unrecorded_subjects,
)
from core.store import SEED_PATH, load_ledger

TODAY = date(2026, 8, 29)


def make_ledger() -> Ledger:
    return Ledger(
        requirements=[
            Requirement(
                id="cert",
                name="監理技術者資格者証",
                category="qualification",
                obligation="legal",
                date_mode="fixed",
            ),
            Requirement(
                id="course",
                name="監理技術者講習",
                category="qualification",
                obligation="legal",
                date_mode="fixed",
            ),
            Requirement(
                id="kenshin",
                name="定期健康診断",
                category="qualification",
                obligation="legal",
                date_mode="cycle",
                cycle_months=12,
            ),
            Requirement(
                id="menjo",
                name="第一種電気工事士 免状",
                category="qualification",
                obligation="none",
                date_mode="none",
            ),
        ],
        subjects=[
            Subject(id="p1", name="甲", kind="person", site="本社"),
            Subject(id="p2", name="乙", kind="person", site="鹿屋支店"),
        ],
        holdings=[],
    )


# --- 基準日で結論が変わること ---------------------------------------------


def test_健診が提出予定日には切れている場合を検出する():
    """年度跨ぎの検出。作成日には有効でも、提出日には切れている。"""
    ledger = make_ledger()
    ledger.holdings.append(
        Holding(
            id="h1",
            subject_id="p1",
            requirement_id="kenshin",
            records=[Record(done_on=date(2025, 9, 10))],
        )
    )

    # 期日は 2026-09-10。
    today_issues = submission_check(ledger, target_date=TODAY)
    assert today_issues == []

    later_issues = submission_check(ledger, target_date=date(2026, 9, 30))
    assert len(later_issues) == 1
    assert later_issues[0].requirement.id == "kenshin"


def test_基準日を過去に戻せば超過は消える():
    ledger = make_ledger()
    ledger.holdings.append(
        Holding(
            id="h1",
            subject_id="p1",
            requirement_id="course",
            fixed_due_on=date(2026, 7, 31),
        )
    )

    assert len(submission_check(ledger, target_date=TODAY)) == 1
    assert submission_check(ledger, target_date=date(2026, 7, 1)) == []


# --- 配置可否 -------------------------------------------------------------


def test_資格者証が有効でも講習が切れていれば配置できない():
    """監理技術者は資格者証と講習の両方が要る。片方だけ見ると通してしまう。"""
    ledger = make_ledger()
    ledger.holdings += [
        Holding(id="h1", subject_id="p1", requirement_id="cert", fixed_due_on=date(2027, 3, 31)),
        Holding(id="h2", subject_id="p1", requirement_id="course", fixed_due_on=date(2026, 7, 31)),
    ]

    ok, reasons = assignment_check(
        ledger, subject_id="p1", required_requirement_ids=["cert", "course"], as_of=TODAY
    )
    assert ok is False
    assert len(reasons) == 1
    assert "監理技術者講習" in reasons[0]


def test_両方有効なら配置できる():
    ledger = make_ledger()
    ledger.holdings += [
        Holding(id="h1", subject_id="p1", requirement_id="cert", fixed_due_on=date(2027, 3, 31)),
        Holding(id="h2", subject_id="p1", requirement_id="course", fixed_due_on=date(2027, 12, 31)),
    ]

    ok, reasons = assignment_check(
        ledger, subject_id="p1", required_requirement_ids=["cert", "course"], as_of=TODAY
    )
    assert ok is True
    assert reasons == []


def test_台帳に登録が無い場合は配置できないとする():
    """記録が無いことを『問題なし』として扱わない。"""
    ledger = make_ledger()
    ledger.holdings.append(
        Holding(id="h1", subject_id="p1", requirement_id="cert", fixed_due_on=date(2027, 3, 31))
    )

    ok, reasons = assignment_check(
        ledger, subject_id="p1", required_requirement_ids=["cert", "course"], as_of=TODAY
    )
    assert ok is False
    assert "登録がありません" in reasons[0]


def test_期日が未確定な場合も配置できないとする():
    ledger = make_ledger()
    ledger.holdings.append(
        Holding(id="h1", subject_id="p1", requirement_id="kenshin", records=[])
    )

    ok, reasons = assignment_check(
        ledger, subject_id="p1", required_requirement_ids=["kenshin"], as_of=TODAY
    )
    assert ok is False
    assert "未確定" in reasons[0]


# --- 一覧 -----------------------------------------------------------------


def test_期限を持たない種別は書類を止めない():
    """免状のように期限が無いものを、未確定として警告に混ぜない。

    以前は core も unknown を返しており、画面側だけで補正していた。
    そのため summarize() では「本当に日付が無いもの」と混ざって数えられた。
    判定の側で別の状態にしてある。
    """
    ledger = make_ledger()
    ledger.holdings.append(
        Holding(id="h1", subject_id="p1", requirement_id="menjo", records=[])
    )

    rows = build_rows(ledger, TODAY)
    assert len(rows) == 1
    assert rows[0].status == "no_deadline"
    assert rows[0].blocks_assignment is False
    assert submission_check(ledger, target_date=TODAY) == []


def test_期限なしと日付未入力を別々に数える():
    """集計で混ざると、把握できていない件数が水増しされる。"""
    ledger = make_ledger()
    ledger.holdings += [
        Holding(id="h1", subject_id="p1", requirement_id="menjo", records=[]),
        Holding(id="h2", subject_id="p1", requirement_id="kenshin", records=[]),
    ]
    counts = summarize(build_rows(ledger, TODAY))
    assert counts["no_deadline"] == 1
    assert counts["unknown"] == 1


def test_期日未確定の行が先頭に並ぶ():
    ledger = make_ledger()
    ledger.holdings += [
        Holding(id="h1", subject_id="p1", requirement_id="cert", fixed_due_on=date(2026, 9, 1)),
        Holding(id="h2", subject_id="p2", requirement_id="kenshin", records=[]),
    ]

    rows = build_rows(ledger, TODAY)
    assert rows[0].holding.id == "h2"
    assert rows[0].due_on is None


def test_参照が壊れている行は黙って捨てず例外にする():
    ledger = make_ledger()
    ledger.holdings.append(
        Holding(id="h1", subject_id="p1", requirement_id="存在しない")
    )

    with pytest.raises(KeyError):
        build_rows(ledger, TODAY)


def test_対象を絞って提出前チェックができる():
    ledger = make_ledger()
    ledger.holdings += [
        Holding(id="h1", subject_id="p1", requirement_id="course", fixed_due_on=date(2026, 7, 31)),
        Holding(id="h2", subject_id="p2", requirement_id="course", fixed_due_on=date(2026, 7, 31)),
    ]

    issues = submission_check(ledger, target_date=TODAY, subject_ids=["p2"])
    assert len(issues) == 1
    assert issues[0].subject.id == "p2"


# --- 人ごとの集約と並び順 -------------------------------------------------


def make_people_ledger() -> Ledger:
    lg = make_ledger()
    lg.subjects = [
        Subject(id="p1", name="あ", kind="person", site="本社"),
        Subject(id="p2", name="い", kind="person", site="本社"),
        Subject(id="p3", name="う", kind="person", site="本社"),
        Subject(id="p4", name="え", kind="person", site="本社"),
        Subject(id="p5", name="お", kind="person", site="本社"),
    ]
    lg.holdings = [
        # 間近（2026-09-10 = 12 日後）
        Holding(id="h2", subject_id="p2", requirement_id="kenshin",
                records=[Record(done_on=date(2025, 9, 10))]),
        # 超過
        Holding(id="h1", subject_id="p1", requirement_id="cert",
                fixed_due_on=date(2026, 6, 30)),
        # 問題なし（十分先）
        Holding(id="h4", subject_id="p4", requirement_id="cert",
                fixed_due_on=date(2029, 1, 1)),
        # 未確定（周期はあるが前回実施日が無い）
        Holding(id="h3", subject_id="p3", requirement_id="kenshin", records=[]),
        # 期限を持たない免状のみ
        Holding(id="h5", subject_id="p5", requirement_id="menjo", records=[]),
    ]
    return lg


def test_悪い順に並ぶ():
    order = [s.subject.name for s in summarize_by_subject(make_people_ledger(), TODAY)]
    assert order == ["あ", "う", "い", "え", "お"]


def test_一番悪い人が先頭に来る():
    top = summarize_by_subject(make_people_ledger(), TODAY)[0]
    assert top.subject.name == "あ"
    assert top.worst == "overdue"


def test_期限を持たない免状しか無い人は問題なしとして扱う():
    """免状に有効期限が無いことを『未確定』に数えると、把握できていない行が埋もれる。"""
    summaries = {s.subject.name: s for s in summarize_by_subject(make_people_ledger(), TODAY)}
    menjo_only = summaries["お"]
    assert menjo_only.worst == "ok"
    assert menjo_only.needs_action is False


def test_手を付ける必要がある人だけが畳まれずに残る():
    summaries = summarize_by_subject(make_people_ledger(), TODAY)
    acting = [s.subject.name for s in summaries if s.needs_action]
    assert acting == ["あ", "う", "い"]


def test_同じ状態なら期日が近い順に並ぶ():
    lg = make_ledger()
    lg.subjects = [
        Subject(id="p1", name="遅い", kind="person"),
        Subject(id="p2", name="早い", kind="person"),
    ]
    lg.holdings = [
        Holding(id="h1", subject_id="p1", requirement_id="cert",
                fixed_due_on=date(2026, 6, 30)),
        Holding(id="h2", subject_id="p2", requirement_id="cert",
                fixed_due_on=date(2026, 1, 15)),
    ]
    order = [s.subject.name for s in summarize_by_subject(lg, TODAY)]
    assert order == ["早い", "遅い"]


def test_状態の原因になっている行の期日をカードに出す():
    lg = make_ledger()
    lg.subjects = [Subject(id="p1", name="甲", kind="person")]
    lg.holdings = [
        Holding(id="h1", subject_id="p1", requirement_id="cert",
                fixed_due_on=date(2027, 5, 1)),
        Holding(id="h2", subject_id="p1", requirement_id="course",
                fixed_due_on=date(2026, 12, 1)),
    ]
    summary = summarize_by_subject(lg, TODAY)[0]
    assert summary.cause_due_on == date(2026, 12, 1)
    assert summary.cause_days_left == (date(2026, 12, 1) - TODAY).days


def test_カードには状態を作り出している資格を出す():
    """状態と期日を別々の資格から拾うと、矛盾したカードになる。

    健診には期日があり、定期講習には前回受講日が無い、という人を作る。
    このとき状態は『未確定』なので、カードに出すのは定期講習でなければならない。
    健診の期日を並べると「日付が未入力」と言いながら期日が出ることになる。
    """
    lg = make_ledger()
    lg.subjects = [Subject(id="p1", name="東", kind="person")]
    lg.holdings = [
        Holding(id="h1", subject_id="p1", requirement_id="kenshin",
                records=[Record(done_on=date(2026, 6, 2))]),
        Holding(id="h2", subject_id="p1", requirement_id="course", fixed_due_on=None),
    ]
    summary = summarize_by_subject(lg, TODAY)[0]

    assert summary.worst == "unknown"
    assert summary.cause is not None
    assert summary.cause.requirement.id == "course"
    assert summary.cause_due_on is None


def test_ほかに対応が必要な件数を数える():
    lg = make_ledger()
    lg.subjects = [Subject(id="p1", name="甲", kind="person")]
    lg.holdings = [
        # 超過が 2 件、間近が 1 件、問題なしが 1 件。
        Holding(id="h1", subject_id="p1", requirement_id="cert",
                fixed_due_on=date(2026, 6, 30)),
        Holding(id="h2", subject_id="p1", requirement_id="course",
                fixed_due_on=date(2026, 7, 31)),
        Holding(id="h3", subject_id="p1", requirement_id="kenshin",
                records=[Record(done_on=date(2025, 9, 10))]),
        Holding(id="h4", subject_id="p1", requirement_id="menjo", records=[]),
    ]
    summary = summarize_by_subject(lg, TODAY)[0]

    # カードに出す 1 件を除いた残り。期限の無い免状は数えない。
    assert summary.cause is not None
    assert summary.cause.requirement.id == "cert"
    assert summary.other_action_count == 2


def test_問題のない人はほかの件数が0になる():
    lg = make_ledger()
    lg.subjects = [Subject(id="p1", name="甲", kind="person")]
    lg.holdings = [
        Holding(id="h1", subject_id="p1", requirement_id="cert",
                fixed_due_on=date(2029, 1, 1)),
    ]
    summary = summarize_by_subject(lg, TODAY)[0]
    assert summary.other_action_count == 0


def test_何も登録が無い人は問題なしにしない():
    """登録しただけの人を「問題なし」に見せない。

    このテストは以前「問題なしになる」ことを確かめていた。だが登録直後の人は
    問題が無いのではなく、何も分かっていないだけである。緑の側に畳まれると、
    登録して放置された人が一覧から消える。間違った仕様をテストで守っていたので、
    仕様ごと直した。
    """
    lg = make_ledger()
    lg.subjects = [Subject(id="p1", name="新入", kind="person")]
    lg.holdings = []
    summary = summarize_by_subject(lg, TODAY)[0]
    assert summary.worst == "unregistered"
    assert summary.needs_action is True
    assert summary.rows == []
    assert summary.cause_due_on is None


def test_資格情報なしは日付未入力の次に並ぶ():
    """どちらも『分かっていない』状態だが、日付未入力は書類を止める。

    資格情報なしは登録作業がまだ済んでいないという意味なので、その次に置く。
    どちらも期限間近より上。期限間近はまだ何も止めないため。
    """
    lg = make_ledger()
    lg.subjects = [
        Subject(id="p1", name="超過", kind="person"),
        Subject(id="p2", name="未入力", kind="person"),
        Subject(id="p3", name="未登録", kind="person"),
        Subject(id="p4", name="間近", kind="person"),
    ]
    lg.holdings = [
        Holding(id="h1", subject_id="p1", requirement_id="cert",
                fixed_due_on=date(2026, 6, 30)),
        Holding(id="h2", subject_id="p2", requirement_id="kenshin", records=[]),
        # p3 は holdings を持たない
        Holding(id="h4", subject_id="p4", requirement_id="kenshin",
                records=[Record(done_on=date(2025, 9, 10))]),
    ]
    order = [s.subject.name for s in summarize_by_subject(lg, TODAY)]
    assert order == ["超過", "未入力", "未登録", "間近"]


def test_期限のない資格だけ持つ人は資格情報なしにしない():
    """免状だけでも登録されていれば、何も分かっていない状態ではない。"""
    lg = make_ledger()
    lg.subjects = [Subject(id="p1", name="甲", kind="person")]
    lg.holdings = [
        Holding(id="h1", subject_id="p1", requirement_id="menjo", records=[])
    ]
    summary = summarize_by_subject(lg, TODAY)[0]
    assert summary.worst == "ok"
    assert summary.needs_action is False


def test_人と道具を分けて集約できる():
    lg = make_people_ledger()
    lg.subjects.append(Subject(id="a1", name="絶縁手袋", kind="asset", site="本社"))
    people = summarize_by_subject(lg, TODAY, kind="person")
    assets = summarize_by_subject(lg, TODAY, kind="asset")
    assert [s.subject.kind for s in people] == ["person"] * 5
    assert [s.subject.name for s in assets] == ["絶縁手袋"]


# --- 同梱データ -----------------------------------------------------------


def seed_generated_on() -> date:
    """同梱データがどの日を基準に作られたかを読む。

    生成は実行した日を基準にするため、ここで固定日を書くと
    データを作り直した日にテストが落ちる。
    """
    import json

    raw = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    return date.fromisoformat(raw["generated_on"])


def test_同梱データが読めて整合している():
    """参照切れがあれば build_rows が例外を送出するため、通ること自体が検証になる。"""
    ledger = load_ledger(SEED_PATH)
    rows = build_rows(ledger, seed_generated_on())

    assert len(rows) == len(ledger.holdings)
    counts = summarize(rows)
    assert sum(counts.values()) == len(rows)
    # デモとして意味を持つよう、超過・未確定・間近がそれぞれ存在すること。
    assert counts["overdue"] > 0
    assert counts["unknown"] > 0
    assert counts["due_soon"] > 0


# --- しきい値の不変条件 ---------------------------------------------------


def test_間近の日数を上げると予告の日数も一緒に上がる():
    """呼び出す側に揃える責任を持たせると、いつか揃え忘れて落ちる。"""
    lg = make_ledger()
    assert lg.soon_days == 30 and lg.upcoming_days == 60

    lg.set_soon_days(90)
    assert lg.soon_days == 90
    assert lg.upcoming_days == 90

    # 例外にならずに判定できること。
    build_rows(lg, TODAY)


def test_間近の日数を下げても予告は下がらない():
    lg = make_ledger()
    lg.set_soon_days(7)
    assert lg.soon_days == 7
    assert lg.upcoming_days == 60


def test_間近の日数に0以下は拒否する():
    lg = make_ledger()
    with pytest.raises(ValueError):
        lg.set_soon_days(0)


# --- 記録が 1 件も無い対象 -------------------------------------------------


def test_記録が無い人は提出前チェックを素通りしてしまう():
    """submission_check は行を見るため、行が無い対象は検出できない。

    これは欠陥ではなく仕様の範囲の確認。行の検査だけでは足りないことを
    はっきりさせるために書いている。補うのが unrecorded_subjects。
    """
    lg = make_ledger()
    lg.subjects = [Subject(id="p1", name="新入", kind="person")]
    lg.holdings = []

    assert submission_check(lg, target_date=TODAY, subject_ids=["p1"]) == []


def test_記録が無い対象を別に取り出せる():
    lg = make_ledger()
    lg.subjects = [
        Subject(id="p1", name="新入", kind="person"),
        Subject(id="p2", name="既存", kind="person"),
        Subject(id="a1", name="新品の道具", kind="asset"),
    ]
    lg.holdings = [
        Holding(id="h1", subject_id="p2", requirement_id="cert",
                fixed_due_on=date(2027, 3, 31)),
    ]

    names = [s.name for s in unrecorded_subjects(lg)]
    assert names == ["新入", "新品の道具"]


def test_記録が無い対象を種類で絞れる():
    lg = make_ledger()
    lg.subjects = [
        Subject(id="p1", name="新入", kind="person"),
        Subject(id="a1", name="新品の道具", kind="asset"),
    ]
    lg.holdings = []

    assert [s.name for s in unrecorded_subjects(lg, kind="person")] == ["新入"]
    assert [s.name for s in unrecorded_subjects(lg, kind="asset")] == ["新品の道具"]


def test_記録が無い対象を選んだ人だけに絞れる():
    lg = make_ledger()
    lg.subjects = [
        Subject(id="p1", name="新入A", kind="person"),
        Subject(id="p2", name="新入B", kind="person"),
    ]
    lg.holdings = []

    assert [s.name for s in unrecorded_subjects(lg, subject_ids=["p2"])] == ["新入B"]


# --- 基準日より後の実施記録を混ぜない -------------------------------------


def test_基準日より後の実施記録を過去の判定に使わない():
    """この台帳の芯にあたる不変条件。

    「任意の基準日で判定できる」と言いながら、基準日より後の実施記録を
    根拠にしていたら、過去の状態を再現できていないことになる。
    """
    lg = make_ledger()
    lg.subjects = [Subject(id="p1", name="甲", kind="person")]
    lg.holdings = [
        Holding(id="h1", subject_id="p1", requirement_id="kenshin",
                records=[Record(done_on=date(2025, 1, 1)),
                         Record(done_on=date(2026, 1, 1))]),
    ]

    # 2025-06-01 時点では、2026-01-01 の実施はまだ起きていない。
    row = build_rows(lg, date(2025, 6, 1))[0]
    assert row.due_on == date(2026, 1, 1)

    # 2026-06-01 時点では両方起きているので、新しい方が使われる。
    row = build_rows(lg, date(2026, 6, 1))[0]
    assert row.due_on == date(2027, 1, 1)


def test_基準日を戻すと周期型の状態も戻る():
    """固定期限型だけでなく、周期型でも過去の状態を再現できること。"""
    lg = make_ledger()
    lg.subjects = [Subject(id="p1", name="甲", kind="person")]
    lg.holdings = [
        Holding(id="h1", subject_id="p1", requirement_id="kenshin",
                records=[Record(done_on=date(2024, 1, 1)),
                         Record(done_on=date(2026, 8, 1))]),
    ]

    # 2025-06-01 時点：前回は 2024-01-01、期限 2025-01-01 → 超過している
    assert build_rows(lg, date(2025, 6, 1))[0].status == "overdue"
    # 2026-08-30 時点：前回は 2026-08-01、期限 2027-08-01 → 問題なし
    assert build_rows(lg, date(2026, 8, 30))[0].status == "ok"


def test_記録全体の最新日は表示のために残してある():
    """判定には使わないが、画面には「前回いつやったか」を出す必要がある。"""
    h = Holding(id="h1", subject_id="p1", requirement_id="kenshin",
                records=[Record(done_on=date(2025, 1, 1)),
                         Record(done_on=date(2026, 1, 1))])
    assert h.last_done_on == date(2026, 1, 1)
    assert h.last_done_on_at(date(2025, 6, 1)) == date(2025, 1, 1)


# --- 件数を数える場所を1つにする -------------------------------------------
#
# 以前は画面が「行の検査 + 記録が無い対象」を足して数え、README の統計は
# 行の検査だけを数えていた。同じ「引っかかる件数」という言葉で、
# 画面は 14 件、README は 12 件を指していた。しかもその 12 件を、
# 私が書いたテストが正しい値として固定していた。
# テストが通っていることは、正しいことの根拠にならない例だった。


def test_止まる理由を両方数える():
    lg = make_ledger()
    # 記録が1件も無い人を足す。行が作られないので、行の検査では見えない。
    lg.subjects.append(Subject(id="p9", name="新入 太郎", kind="person"))

    result = preflight_check(lg, target_date=date(2027, 3, 31))
    rows_only = submission_check(lg, target_date=date(2027, 3, 31))
    blank_only = unrecorded_subjects(lg)

    # 行の検査だけでは、記録が無い対象を数え落とす
    assert result.blocked == len(rows_only) + len(blank_only)
    assert result.blocked > len(rows_only)
    assert "p9" in [s.id for s in result.unrecorded]
    assert not result.is_clear


def test_止まるものが無ければそう言える():
    lg = Ledger(
        requirements=[Requirement(id="k", name="健診", category="qualification",
                                  obligation="legal", date_mode="cycle",
                                  cycle_months=12)],
        subjects=[Subject(id="p1", name="甲", kind="person")],
        holdings=[Holding(id="h1", subject_id="p1", requirement_id="k",
                          records=[Record(done_on=date(2026, 8, 1))])],
    )
    result = preflight_check(lg, target_date=date(2026, 9, 1))
    assert result.is_clear
    assert result.blocked == 0


def test_対象を絞っても両方に効く():
    lg = make_ledger()
    lg.subjects.append(Subject(id="p9", name="新入 太郎", kind="person"))

    result = preflight_check(lg, target_date=date(2027, 3, 31), subject_ids=["p9"])
    assert result.issues == []
    assert [s.id for s in result.unrecorded] == ["p9"]
    assert result.blocked == 1
