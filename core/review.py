"""台帳の判定。

この台帳の要点は、判定を「今日」に固定しないことにある。

安全書類が差し戻される原因として、資格証の期限切れと健康診断日の年度跨ぎが
挙げられている。どちらも「提出する時点」や「工期の終わり」で切れているために
起きるものであり、作成した日に有効かどうかを見ても防げない。

そこですべての判定関数は基準日 as_of を引数で受け取り、既定値を持たない。
呼び出し側は、いつ時点の話をしているのかを必ず明示することになる。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from core.models import Holding, Ledger, Requirement, Subject
from core.schedule import (
    OVERDUE,
    UNKNOWN,
    Status,
    Thresholds,
    days_left,
    status_of,
)

__all__ = ["Row", "build_rows", "submission_check", "assignment_check", "summarize"]


@dataclass(frozen=True)
class Row:
    """一覧に出す 1 行。"""

    subject: Subject
    requirement: Requirement
    holding: Holding
    due_on: date | None
    status: Status
    days_left: int | None

    @property
    def blocks_assignment(self) -> bool:
        """この行が原因で書類が通らない、または現場に出せない状態か。

        期限切れだけでなく、期日が確定していない場合も通さない。
        「分からない」を「大丈夫」として扱わないため。
        """
        return self.status in (OVERDUE, UNKNOWN) and self.requirement.has_deadline


def build_rows(ledger: Ledger, as_of: date) -> list[Row]:
    """台帳を基準日 as_of で評価し、期日の早い順に並べて返す。"""
    thresholds = Thresholds(
        soon_days=ledger.soon_days, upcoming_days=ledger.upcoming_days
    )

    rows: list[Row] = []
    for holding in ledger.holdings:
        requirement = ledger.requirement(holding.requirement_id)
        subject = ledger.subject(holding.subject_id)
        if requirement is None or subject is None:
            # 参照先を失った行を黙って捨てない。呼び出し側が気づけるよう例外にする。
            raise KeyError(
                f"台帳の参照が壊れています: holding={holding.id} "
                f"subject={holding.subject_id} requirement={holding.requirement_id}"
            )

        due = holding.due_on(requirement)
        rows.append(
            Row(
                subject=subject,
                requirement=requirement,
                holding=holding,
                due_on=due,
                status=status_of(due, as_of, thresholds),
                days_left=days_left(due, as_of),
            )
        )

    # 期日未確定を末尾ではなく先頭側に置く。放置されやすいのはこちらのため。
    def sort_key(row: Row) -> tuple[int, date]:
        if row.due_on is None:
            return (0, date.min)
        return (1, row.due_on)

    return sorted(rows, key=sort_key)


def submission_check(
    ledger: Ledger,
    *,
    target_date: date,
    subject_ids: list[str] | None = None,
) -> list[Row]:
    """指定した日に書類を出すとして、その日に通らない行を返す。

    target_date には提出予定日や工期末を入れる。作成日ではない。
    """
    rows = build_rows(ledger, target_date)
    if subject_ids is not None:
        allowed = set(subject_ids)
        rows = [r for r in rows if r.subject.id in allowed]
    return [r for r in rows if r.blocks_assignment]


def assignment_check(
    ledger: Ledger,
    *,
    subject_id: str,
    required_requirement_ids: list[str],
    as_of: date,
) -> tuple[bool, list[str]]:
    """基準日に、その人を配置できるかを判定する。

    監理技術者のように、資格者証と講習の両方が有効でなければ配置できない
    ものがあるため、複数の条件をすべて満たすかで判定する。

    戻り値は (配置可否, 理由の一覧)。可の場合、理由は空。
    """
    rows = {r.requirement.id: r for r in build_rows(ledger, as_of) if r.subject.id == subject_id}

    reasons: list[str] = []
    for requirement_id in required_requirement_ids:
        requirement = ledger.requirement(requirement_id)
        name = requirement.name if requirement else requirement_id

        row = rows.get(requirement_id)
        if row is None:
            reasons.append(f"{name}：台帳に登録がありません")
            continue
        if row.status == UNKNOWN:
            reasons.append(f"{name}：期日が未確定です")
            continue
        if row.status == OVERDUE:
            reasons.append(f"{name}：{row.due_on} に超過しています")

    return (not reasons, reasons)


def summarize(rows: list[Row]) -> dict[Status, int]:
    """状態ごとの件数。"""
    counts: dict[Status, int] = {
        "overdue": 0,
        "due_soon": 0,
        "upcoming": 0,
        "ok": 0,
        "unknown": 0,
    }
    for row in rows:
        counts[row.status] += 1
    return counts
