"""台帳の判定の検証。

とくに「基準日を変えると結論が変わる」ことを確認する。
この台帳の存在理由がそこにあるため。
"""

from __future__ import annotations

from datetime import date

import pytest

from core.models import Holding, Ledger, Record, Requirement, Subject
from core.review import assignment_check, build_rows, submission_check, summarize
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


# --- 同梱データ -----------------------------------------------------------


def test_同梱データが読めて整合している():
    """参照切れがあれば build_rows が例外を送出するため、通ること自体が検証になる。"""
    ledger = load_ledger(SEED_PATH)
    rows = build_rows(ledger, TODAY)

    assert len(rows) == len(ledger.holdings)
    counts = summarize(rows)
    assert sum(counts.values()) == len(rows)
    # デモとして意味を持つよう、超過・未確定・間近がそれぞれ存在すること。
    assert counts["overdue"] > 0
    assert counts["unknown"] > 0
    assert counts["due_soon"] > 0
