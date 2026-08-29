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
    submission_check,
    summarize,
    summarize_by_subject,
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
            Subject(id="p2", name="乙", kind="person", site="川内支店"),
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
    """免状のように期限が無いものを、未確定として警告に混ぜない。"""
    ledger = make_ledger()
    ledger.holdings.append(
        Holding(id="h1", subject_id="p1", requirement_id="menjo", records=[])
    )

    rows = build_rows(ledger, TODAY)
    assert len(rows) == 1
    assert rows[0].status == "unknown"
    assert rows[0].blocks_assignment is False
    assert submission_check(ledger, target_date=TODAY) == []


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


def test_何も登録が無い人は問題なしになる():
    lg = make_ledger()
    lg.subjects = [Subject(id="p1", name="新入", kind="person")]
    lg.holdings = []
    summary = summarize_by_subject(lg, TODAY)[0]
    assert summary.worst == "ok"
    assert summary.rows == []
    assert summary.cause_due_on is None


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
