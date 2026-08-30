"""期限の計算。

このモジュールは外部ライブラリにも画面にも依存しない純粋な関数だけで構成する。
期限台帳の正しさはすべてここに集約され、tests/test_schedule.py で検証する。

設計上の約束:

1. 前回実施日は保持しない。実施記録の集合から導出する（latest_done）。
   同じ事実を二箇所に持つと、片方だけ更新されたときに静かに食い違うため。
2. 期日が決められないときは推測しない。None を返し、状態を UNKNOWN とする。
   分からないものを「まだ大丈夫」と表示することが、この種の台帳で最も危険なため。
3. 不正な入力は黙って丸めず ScheduleError を送出する。
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Literal

__all__ = [
    "ScheduleError",
    "Status",
    "Thresholds",
    "add_months",
    "latest_done",
    "next_due",
    "days_left",
    "status_of",
    "NO_DEADLINE",
]


class ScheduleError(ValueError):
    """期限計算に渡された値が受け付けられないことを表す。"""


# 「期限が存在しない」と「期限は存在するが分かっていない」は別の状態。
# 以前は両方 UNKNOWN にしていたため、画面では区別しているのに集計では
# 混ざる、という食い違いが起きていた。判定の側で分ける。
Status = Literal["overdue", "due_soon", "upcoming", "ok", "unknown", "no_deadline"]

OVERDUE: Status = "overdue"
DUE_SOON: Status = "due_soon"
UPCOMING: Status = "upcoming"
OK: Status = "ok"
UNKNOWN: Status = "unknown"
NO_DEADLINE: Status = "no_deadline"

STATUS_LABEL: dict[Status, str] = {
    OVERDUE: "超過",
    DUE_SOON: "間近",
    UPCOMING: "予告",
    OK: "余裕",
    UNKNOWN: "未確定",
    NO_DEADLINE: "期限なし",
}


@dataclass(frozen=True)
class Thresholds:
    """どれだけ手前から警告するか。

    画面から変更できる値であり、コードに固定しない。
    """

    soon_days: int = 30
    upcoming_days: int = 60

    def __post_init__(self) -> None:
        if self.soon_days < 0 or self.upcoming_days < 0:
            raise ScheduleError("警告日数に負の値は指定できません")
        if self.soon_days > self.upcoming_days:
            raise ScheduleError(
                "『間近』の日数は『予告』の日数以下である必要があります "
                f"(間近={self.soon_days}, 予告={self.upcoming_days})"
            )


def add_months(base: date, months: int) -> date:
    """base に months か月を加算する。月末は繰り上がらずクランプする。

    2026-01-31 に 1 か月を足すと 2026-02-28 になる（2026-03-03 にはしない）。
    点検や講習の周期は「何月何日」ではなく「何か月後」で定められることが多く、
    日数加算で代用すると月によって数日ずれるため、月単位で計算する。
    """
    if months < 0:
        raise ScheduleError("加算する月数に負の値は指定できません")

    total = base.month - 1 + months
    year = base.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(base.day, last_day))


def latest_done(done_dates: Iterable[date], as_of: date | None = None) -> date | None:
    """実施記録から前回実施日を導出する。記録が無ければ None。

    as_of を渡すと、その日までに実施されたものだけを見る。

    これが要るのは、この台帳が「今日」以外の日でも判定するためである。
    2025年6月時点の状態を出すときに、2026年1月の実施記録まで数えてしまうと、
    その時点ではまだ起きていない実施を根拠に期限を延ばすことになる。
    過去の状態を再現できなくなり、「任意の基準日で判定する」という
    この台帳の芯が成り立たない。
    """
    dates = [d for d in done_dates if as_of is None or d <= as_of]
    return max(dates) if dates else None


def next_due(
    *,
    last_done_on: date | None,
    cycle_months: int | None,
    fixed_due_on: date | None = None,
) -> date | None:
    """次回期日を返す。決められない場合は None。

    期日の決まり方は二通りある。

    - 期日指定型: 免状の有効期限のように、期日が外から与えられるもの。
      fixed_due_on をそのまま使う。
    - 周期型: 点検や定期講習のように、前回実施日から周期で決まるもの。
      last_done_on + cycle_months を使う。

    どちらの材料も揃わない場合は None を返す。ここで「とりあえず今日」や
    「登録日から起算」といった補完をすると、実際には期限が分かっていないものが
    余裕ありとして表示され、台帳としての意味を失う。
    """
    if fixed_due_on is not None:
        return fixed_due_on

    if cycle_months is None or last_done_on is None:
        return None

    if cycle_months <= 0:
        raise ScheduleError("周期は 1 か月以上で指定してください")

    return add_months(last_done_on, cycle_months)


def days_left(due_on: date | None, today: date) -> int | None:
    """期日まであと何日か。超過している場合は負の値。期日未確定なら None。"""
    if due_on is None:
        return None
    return (due_on - today).days


def status_of(
    due_on: date | None,
    today: date,
    thresholds: Thresholds | None = None,
) -> Status:
    """期日と当日から状態を決める。

    境界は「期日当日はまだ超過ではない」「しきい値ちょうどの日は含む」とする。
    """
    if due_on is None:
        return UNKNOWN

    th = thresholds or Thresholds()
    remaining = (due_on - today).days

    if remaining < 0:
        return OVERDUE
    if remaining <= th.soon_days:
        return DUE_SOON
    if remaining <= th.upcoming_days:
        return UPCOMING
    return OK


def validate_done_on(done_on: date, today: date) -> None:
    """実施日として受け付けられるかを検査する。

    未来の日付を実施記録として受け付けると、次回期日が実態より先に延び、
    本当は切れているものが余裕ありとして表示されてしまう。
    """
    if done_on > today:
        raise ScheduleError(
            f"実施日に未来の日付は登録できません（実施日={done_on}, 本日={today}）"
        )
