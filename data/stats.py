"""README に載せている数字を、同梱データから計算する。

手で書いた数字は、データを作り直したときに置き去りになる。
実際 README には「1年後 50件」と書いてあったが、同梱データでの実測は 117件で、
2倍以上ずれていた。人が読む文章の中の数字ほど、誰も確かめないまま残る。

    python -m data.stats

tests/test_readme.py が、この計算結果と README の表を突き合わせている。
ずれた状態では pytest が通らない。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from core.review import submission_check
from core.store import load_ledger

# 「今日」「工期末」といった代表的な提出日。
# 日数で持つのは、同梱データが作り直されるたびに基準日が動くため。
TARGETS: list[tuple[str, int]] = [
    ("今日", 0),
    ("1か月半後", 45),
    ("1年後（工期末）", 365),
]


@dataclass(frozen=True)
class Line:
    label: str
    blocked: int      # その日に提出すると引っかかる件数
    still_valid: int  # そのうち、今日の時点ではまだ有効なもの


def table(today: date | None = None) -> list[Line]:
    lg = load_ledger()
    today = today or date.today()

    now = {(r.subject.id, r.requirement.id)
           for r in submission_check(lg, target_date=today)}

    out = []
    for label, days in TARGETS:
        rows = submission_check(lg, target_date=today + timedelta(days=days))
        out.append(Line(
            label=label,
            blocked=len(rows),
            still_valid=sum(
                1 for r in rows if (r.subject.id, r.requirement.id) not in now
            ),
        ))
    return out


def as_markdown(today: date | None = None) -> str:
    lines = ["| 提出日 | 引っかかる件数 | うち今日はまだ有効 |",
             "|---|---|---|"]
    for line in table(today):
        lines.append(f"| {line.label} | {line.blocked}件 | {line.still_valid}件 |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(as_markdown())
