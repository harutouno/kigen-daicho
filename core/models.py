"""台帳が扱うデータの型。

保存形式（JSON）との相互変換もここに置く。画面もこの型だけを見る。

命名について:
    Requirement  … 「何を、どの周期で満たす必要があるか」の定義（種別マスタ）
    Subject      … 「誰が／何が」その対象になるか（社員・設備）
    Holding      … Subject × Requirement の組。期限が実際に発生する単位
    Record       … 実施した事実。Holding の下にぶら下がる
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from core.schedule import latest_done, next_due

__all__ = [
    "Category",
    "DateMode",
    "Obligation",
    "Requirement",
    "Subject",
    "Record",
    "Holding",
    "Ledger",
]

Category = Literal["qualification", "inspection"]
DateMode = Literal["cycle", "fixed", "none"]
Obligation = Literal["legal", "contract", "effort", "none"]

CATEGORY_LABEL: dict[str, str] = {
    "qualification": "資格・講習・健診",
    "inspection": "点検・校正",
}

OBLIGATION_LABEL: dict[str, str] = {
    "legal": "法令義務",
    "contract": "規格・契約",
    "effort": "努力義務",
    "none": "期限なし",
}

DATE_MODE_LABEL: dict[str, str] = {
    "cycle": "周期から計算",
    "fixed": "有効期限を直接入力",
    "none": "期限を持たない",
}


def _to_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _from_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


@dataclass(frozen=True)
class Requirement:
    """種別マスタ。

    周期も警告日数もここに持ち、コードには固定しない。
    実際の周期は設備や契約や社内規程で変わるため、画面から編集できる必要がある。
    """

    id: str
    name: str
    category: Category
    obligation: Obligation
    date_mode: DateMode
    cycle_months: int | None = None
    source: str = ""
    note: str = ""

    @property
    def has_deadline(self) -> bool:
        return self.date_mode != "none"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Requirement:
        return cls(
            id=d["id"],
            name=d["name"],
            category=d["category"],
            obligation=d.get("obligation", "legal"),
            date_mode=d.get("date_mode", "cycle"),
            cycle_months=d.get("cycle_months"),
            source=d.get("source", ""),
            note=d.get("note", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "obligation": self.obligation,
            "date_mode": self.date_mode,
            "cycle_months": self.cycle_months,
            "source": self.source,
            "note": self.note,
        }


@dataclass(frozen=True)
class Subject:
    """期限の対象。社員か設備。"""

    id: str
    name: str
    kind: Literal["person", "asset"]
    site: str = ""
    role: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Subject:
        return cls(
            id=d["id"],
            name=d["name"],
            kind=d.get("kind", "person"),
            site=d.get("site", ""),
            role=d.get("role", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "site": self.site,
            "role": self.role,
        }


@dataclass(frozen=True)
class Record:
    """実施した事実。追記のみで、書き換えない。"""

    done_on: date
    done_by: str = ""
    memo: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Record:
        done_on = _to_date(d["done_on"])
        assert done_on is not None
        return cls(done_on=done_on, done_by=d.get("done_by", ""), memo=d.get("memo", ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "done_on": _from_date(self.done_on),
            "done_by": self.done_by,
            "memo": self.memo,
        }


@dataclass
class Holding:
    """Subject × Requirement。期限が発生する単位。

    前回実施日は持たない。records から導出する（core.schedule.latest_done）。
    同じ事実を二箇所に置くと、片方だけ更新されたときに静かに食い違うため。
    """

    id: str
    subject_id: str
    requirement_id: str
    fixed_due_on: date | None = None
    records: list[Record] = field(default_factory=list)
    note: str = ""

    @property
    def last_done_on(self) -> date | None:
        return latest_done(r.done_on for r in self.records)

    def due_on(self, requirement: Requirement) -> date | None:
        """次回期日。決められない場合は None を返し、推測で埋めない。"""
        if requirement.date_mode == "none":
            return None
        if requirement.date_mode == "fixed":
            return next_due(
                last_done_on=None, cycle_months=None, fixed_due_on=self.fixed_due_on
            )
        return next_due(
            last_done_on=self.last_done_on,
            cycle_months=requirement.cycle_months,
        )

    def add_record(self, record: Record) -> None:
        self.records.append(record)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Holding:
        return cls(
            id=d["id"],
            subject_id=d["subject_id"],
            requirement_id=d["requirement_id"],
            fixed_due_on=_to_date(d.get("fixed_due_on")),
            records=[Record.from_dict(r) for r in d.get("records", [])],
            note=d.get("note", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "requirement_id": self.requirement_id,
            "fixed_due_on": _from_date(self.fixed_due_on),
            "records": [r.to_dict() for r in self.records],
            "note": self.note,
        }


@dataclass
class Ledger:
    """台帳全体。"""

    requirements: list[Requirement] = field(default_factory=list)
    subjects: list[Subject] = field(default_factory=list)
    holdings: list[Holding] = field(default_factory=list)
    soon_days: int = 30
    upcoming_days: int = 60

    def set_soon_days(self, days: int) -> None:
        """『期限間近』とする日数を変える。

        『予告』の日数を追い越すと Thresholds が例外を出すため、ここで一緒に
        引き上げる。この決まりを呼び出す側に持たせると、呼ぶ場所が増えたときに
        揃え忘れて落ちる。不変条件はデータを持っている側で守る。
        """
        if days < 1:
            raise ValueError("『期限間近』とする日数は 1 日以上で指定してください")
        self.soon_days = days
        self.upcoming_days = max(self.upcoming_days, days)

    def requirement(self, requirement_id: str) -> Requirement | None:
        return next((r for r in self.requirements if r.id == requirement_id), None)

    def subject(self, subject_id: str) -> Subject | None:
        return next((s for s in self.subjects if s.id == subject_id), None)

    def holdings_of(self, subject_id: str) -> list[Holding]:
        return [h for h in self.holdings if h.subject_id == subject_id]

    @property
    def sites(self) -> list[str]:
        return sorted({s.site for s in self.subjects if s.site})

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Ledger:
        return cls(
            requirements=[Requirement.from_dict(x) for x in d.get("requirements", [])],
            subjects=[Subject.from_dict(x) for x in d.get("subjects", [])],
            holdings=[Holding.from_dict(x) for x in d.get("holdings", [])],
            soon_days=d.get("soon_days", 30),
            upcoming_days=d.get("upcoming_days", 60),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirements": [r.to_dict() for r in self.requirements],
            "subjects": [s.to_dict() for s in self.subjects],
            "holdings": [h.to_dict() for h in self.holdings],
            "soon_days": self.soon_days,
            "upcoming_days": self.upcoming_days,
        }
