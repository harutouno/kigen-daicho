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
    NO_DEADLINE,
    OVERDUE,
    UNKNOWN,
    Status,
    Thresholds,
    days_left,
    status_of,
)

__all__ = [
    "Row",
    "STATUS_ORDER",
    "UNREGISTERED",
    "SubjectSummary",
    "build_rows",
    "summarize_by_subject",
    "submission_check",
    "unrecorded_subjects",
    "assignment_check",
    "summarize",
]

# 画面に出す順番。悪いものほど小さい値。
#
# 超過を未確定より先に出す。未確定の方が実態としては危ないこともあるが、
# 超過は「今日その人を現場に出せない」ので、先に手を付ける必要があるため。
#
# 「unregistered」は、その対象に記録が 1 件も無い状態。Status には無く、
# 対象ごとの集約でだけ現れる。日付未入力の隣に置くのは、どちらも
# 「分かっていない」状態で、性質が近いため。
UNREGISTERED = "unregistered"

STATUS_ORDER: dict[str, int] = {
    "overdue": 0,
    "unknown": 1,
    UNREGISTERED: 2,
    "due_soon": 3,
    "upcoming": 4,
    "ok": 5,
    # 期限が存在しないものは、危ない順のどこにも入らない。最後に置く。
    NO_DEADLINE: 6,
}


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
        # 期限が存在しない種別は no_deadline になるので、ここで弾く必要はない。
        return self.status in (OVERDUE, UNKNOWN)


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

        due = holding.due_on(requirement, as_of)
        # 期限が存在しない種別を「未確定」にしない。分かっていないのではなく、
        # そもそも期限が無い。混ぜると、本当に日付が入っていない行が埋もれる。
        status = (
            NO_DEADLINE
            if not requirement.has_deadline
            else status_of(due, as_of, thresholds)
        )
        rows.append(
            Row(
                subject=subject,
                requirement=requirement,
                holding=holding,
                due_on=due,
                status=status,
                days_left=days_left(due, as_of),
            )
        )

    # 期日未確定を末尾ではなく先頭側に置く。放置されやすいのはこちらのため。
    def sort_key(row: Row) -> tuple[int, date]:
        if row.due_on is None:
            return (0, date.min)
        return (1, row.due_on)

    return sorted(rows, key=sort_key)


@dataclass(frozen=True)
class SubjectSummary:
    """1 人（または 1 台）を 1 枚のカードに出すための集約。"""

    subject: Subject
    rows: list[Row]
    worst: str
    cause_due_on: date | None
    cause_days_left: int | None
    cause: Row | None
    other_action_count: int

    @property
    def needs_action(self) -> bool:
        """今日この人に手を付ける必要があるか。

        画面には手を付けるべきものだけを出し、それ以外は畳む。
        「予告」（まだ先だが期日が見えている）は今日やることが無いので畳む側に入れる。

        記録が 1 件も無い対象も手を付ける側に入れる。問題が無いのではなく、
        何も分かっていないだけであり、それを「問題なし」に見せると、
        登録しただけで放置された人が緑の側へ消える。
        """
        return self.worst in ("overdue", "unknown", UNREGISTERED, "due_soon")


def summarize_by_subject(
    ledger: Ledger,
    as_of: date,
    *,
    kind: str | None = None,
) -> list[SubjectSummary]:
    """対象ごとにまとめ、悪い順に並べて返す。

    並び順の規則:
      1. その人が持つ中で最も悪い状態（超過 → 未確定 → 間近 → 予告 → 問題なし）
      2. 同じ状態なら期日が近い順
      3. 期日が無いものは名前順

    期限を持たない種別（有効期限の無い免状など）は、状態の判定に数えない。
    期限が存在しないことを「未確定」として扱うと、把握できていない行が埋もれるため。
    """
    rows = build_rows(ledger, as_of)

    by_subject: dict[str, list[Row]] = {}
    for row in rows:
        by_subject.setdefault(row.subject.id, []).append(row)

    summaries: list[SubjectSummary] = []
    for subject in ledger.subjects:
        if kind is not None and subject.kind != kind:
            continue

        owned = by_subject.get(subject.id, [])
        judged = [r for r in owned if r.requirement.has_deadline]

        # 記録が 1 件も無いのは「問題なし」ではない。何も分かっていないだけ。
        # 登録しただけの人が緑の側に消えないよう、別の状態として扱う。
        worst: str = UNREGISTERED if not owned else "ok"
        for row in judged:
            if STATUS_ORDER[row.status] < STATUS_ORDER[worst]:
                worst = row.status

        # カードには「最も悪い状態」を作り出している当の行を出す。
        # 状態と期日を別々の行から拾うと、「日付が未入力」と言いながら
        # 期日が出ている、という矛盾したカードになるため。
        same_status = [r for r in judged if r.status == worst]
        dated = [r for r in same_status if r.due_on is not None]
        if dated:
            cause = min(dated, key=lambda r: r.due_on)  # type: ignore[arg-type,return-value]
        elif same_status:
            cause = same_status[0]
        else:
            cause = None

        acting = [r for r in judged if r.blocks_assignment or r.status == "due_soon"]
        others = max(len(acting) - 1, 0) if cause is not None else len(acting)

        summaries.append(
            SubjectSummary(
                subject=subject,
                rows=owned,
                worst=worst,
                cause_due_on=cause.due_on if cause else None,
                cause_days_left=cause.days_left if cause else None,
                cause=cause,
                other_action_count=others,
            )
        )

    def sort_key(s: SubjectSummary) -> tuple[int, date, str]:
        return (
            STATUS_ORDER[s.worst],
            s.cause_due_on or date.max,
            s.subject.name,
        )

    return sorted(summaries, key=sort_key)


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


def unrecorded_subjects(
    ledger: Ledger,
    *,
    subject_ids: list[str] | None = None,
    kind: str | None = None,
) -> list[Subject]:
    """記録が 1 件も無い対象を返す。

    submission_check は行（Subject × Requirement）を見るため、記録が 1 件も
    無い対象は行が作られず、素通りしてしまう。それでは「何も分かっていない人」を
    書類に載せてよいと答えることになる。行の検査とは別に、対象そのものを見る。
    """
    having = {h.subject_id for h in ledger.holdings}

    out: list[Subject] = []
    for subject in ledger.subjects:
        if subject.id in having:
            continue
        if kind is not None and subject.kind != kind:
            continue
        if subject_ids is not None and subject.id not in subject_ids:
            continue
        out.append(subject)
    return out


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
        "no_deadline": 0,
    }
    for row in rows:
        counts[row.status] += 1
    return counts
