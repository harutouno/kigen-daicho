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

from core.review import preflight_check
from core.store import load_ledger, seed_generated_on

# 「今日」「工期末」といった代表的な提出日。
# 日数で持つのは、同梱データが作り直されるたびに基準日が動くため。
TARGETS: list[tuple[str, int]] = [
    ("基準日当日", 0),
    ("1か月半後", 45),
    ("1年後（工期末）", 365),
]


@dataclass(frozen=True)
class Line:
    label: str
    blocked: int      # その日に提出すると引っかかる件数
    still_valid: int  # そのうち、基準日当日にはまだ有効なもの


def table(today: date | None = None) -> list[Line]:
    """README に載せる表を作る。

    基準日は同梱データの生成日に固定する。date.today() にすると、
    コードを 1 文字も変えていないのに日が経つだけで数字が変わり、
    README と突き合わせるテストが落ちる。実測 30 日後で 12 件が 25 件になった。
    """
    lg = load_ledger()
    today = today or seed_generated_on()

    now = preflight_check(lg, target_date=today)
    now_keys = {(r.subject.id, r.requirement.id) for r in now.issues}

    out = []
    for label, days in TARGETS:
        result = preflight_check(lg, target_date=today + timedelta(days=days))
        out.append(Line(
            label=label,
            blocked=result.blocked,
            still_valid=sum(
                1 for r in result.issues
                if (r.subject.id, r.requirement.id) not in now_keys
            ),
        ))
    return out


def as_markdown(today: date | None = None) -> str:
    lines = ["| 提出日 | 引っかかる件数 | うち基準日当日はまだ有効 |",
             "|---|---|---|"]
    for line in table(today):
        lines.append(f"| {line.label} | {line.blocked}件 | {line.still_valid}件 |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(as_markdown())
