"""期限計算の検証。

境界と、間違えたときに「安全側に倒れない」ケースを重点的に確認する。
"""

from __future__ import annotations

from datetime import date

import pytest

from core.schedule import (
    DUE_SOON,
    OK,
    OVERDUE,
    UNKNOWN,
    UPCOMING,
    ScheduleError,
    Thresholds,
    add_months,
    days_left,
    latest_done,
    next_due,
    status_of,
    validate_done_on,
)


# --- 月加算 ---------------------------------------------------------------


def test_月加算の基本():
    assert add_months(date(2026, 1, 15), 1) == date(2026, 2, 15)
    assert add_months(date(2026, 1, 15), 12) == date(2027, 1, 15)


def test_月末は繰り上がらずクランプする():
    # 1/31 の 1 か月後は 3/3 ではなく 2/28。
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 3, 31), 1) == date(2026, 4, 30)


def test_うるう年の2月29日に着地する():
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)


def test_うるう日から1年後は2月28日になる():
    assert add_months(date(2028, 2, 29), 12) == date(2029, 2, 28)


def test_年をまたぐ月加算():
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


def test_0か月の加算は同じ日を返す():
    assert add_months(date(2026, 8, 29), 0) == date(2026, 8, 29)


def test_負の月数は拒否する():
    with pytest.raises(ScheduleError):
        add_months(date(2026, 8, 29), -1)


# --- 前回実施日の導出 -----------------------------------------------------


def test_実施記録から最新の日付を導出する():
    records = [date(2025, 4, 1), date(2026, 4, 1), date(2025, 10, 1)]
    assert latest_done(records) == date(2026, 4, 1)


def test_実施記録が無ければ前回実施日は無い():
    assert latest_done([]) is None


# --- 次回期日 -------------------------------------------------------------


def test_周期型は前回実施日から計算する():
    due = next_due(last_done_on=date(2026, 4, 10), cycle_months=12)
    assert due == date(2027, 4, 10)


def test_期日指定型は与えられた期日をそのまま使う():
    due = next_due(
        last_done_on=None,
        cycle_months=None,
        fixed_due_on=date(2027, 6, 30),
    )
    assert due == date(2027, 6, 30)


def test_期日指定は周期より優先する():
    # 免状の有効期限のように外から期日が決まっている場合、計算値で上書きしない。
    due = next_due(
        last_done_on=date(2026, 1, 1),
        cycle_months=12,
        fixed_due_on=date(2026, 3, 3),
    )
    assert due == date(2026, 3, 3)


def test_前回実施日が無ければ期日は未確定():
    assert next_due(last_done_on=None, cycle_months=12) is None


def test_周期が無ければ期日は未確定():
    assert next_due(last_done_on=date(2026, 4, 10), cycle_months=None) is None


def test_未確定を今日や登録日で補完しない():
    # 「材料が無いので分からない」を「余裕あり」に変えないことを明示的に確認する。
    due = next_due(last_done_on=None, cycle_months=None)
    assert due is None
    assert status_of(due, date(2026, 8, 29)) == UNKNOWN


def test_周期に0以下は拒否する():
    with pytest.raises(ScheduleError):
        next_due(last_done_on=date(2026, 4, 10), cycle_months=0)
    with pytest.raises(ScheduleError):
        next_due(last_done_on=date(2026, 4, 10), cycle_months=-3)


# --- 状態判定 -------------------------------------------------------------


TODAY = date(2026, 8, 29)


def test_期日を過ぎていれば超過():
    assert status_of(date(2026, 8, 28), TODAY) == OVERDUE


def test_期日当日はまだ超過ではない():
    assert status_of(TODAY, TODAY) == DUE_SOON


def test_しきい値ちょうどの日は間近に含む():
    assert status_of(date(2026, 9, 28), TODAY) == DUE_SOON  # 30 日後


def test_間近の翌日から予告になる():
    assert status_of(date(2026, 9, 29), TODAY) == UPCOMING  # 31 日後


def test_予告のしきい値ちょうどは予告に含む():
    assert status_of(date(2026, 10, 28), TODAY) == UPCOMING  # 60 日後


def test_予告を超えれば余裕():
    assert status_of(date(2026, 10, 29), TODAY) == OK  # 61 日後


def test_期日が未確定なら未確定を返す():
    assert status_of(None, TODAY) == UNKNOWN


def test_しきい値は画面から変更できる():
    th = Thresholds(soon_days=7, upcoming_days=14)
    assert status_of(date(2026, 9, 5), TODAY, th) == DUE_SOON  # 7 日後（境界・含む）
    assert status_of(date(2026, 9, 6), TODAY, th) == UPCOMING  # 8 日後
    assert status_of(date(2026, 9, 12), TODAY, th) == UPCOMING  # 14 日後（境界・含む）
    assert status_of(date(2026, 9, 13), TODAY, th) == OK  # 15 日後（予告の外）


def test_間近が予告より長いしきい値は拒否する():
    with pytest.raises(ScheduleError):
        Thresholds(soon_days=60, upcoming_days=30)


def test_負のしきい値は拒否する():
    with pytest.raises(ScheduleError):
        Thresholds(soon_days=-1, upcoming_days=30)


# --- 残日数 ---------------------------------------------------------------


def test_残日数は超過時に負になる():
    assert days_left(date(2026, 8, 20), TODAY) == -9


def test_残日数は期日当日に0になる():
    assert days_left(TODAY, TODAY) == 0


def test_期日未確定なら残日数も無い():
    assert days_left(None, TODAY) is None


# --- 実施日の検査 ---------------------------------------------------------


def test_過去と当日の実施日は受け付ける():
    validate_done_on(date(2026, 8, 28), TODAY)
    validate_done_on(TODAY, TODAY)


def test_未来の実施日は拒否する():
    # 受け付けてしまうと、次回期日が実態より先に延びて超過が隠れる。
    with pytest.raises(ScheduleError):
        validate_done_on(date(2026, 8, 30), TODAY)


# --- 実施を記録したときの一巡 ---------------------------------------------


def test_実施を記録すると次回期日が立ち直す():
    records = [date(2025, 4, 10)]
    th = Thresholds()

    before = next_due(last_done_on=latest_done(records), cycle_months=12)
    assert before == date(2026, 4, 10)
    assert status_of(before, TODAY, th) == OVERDUE

    records.append(date(2026, 8, 20))

    after = next_due(last_done_on=latest_done(records), cycle_months=12)
    assert after == date(2027, 8, 20)
    assert status_of(after, TODAY, th) == OK


def test_古い実施日を後から追加しても次回期日は後退しない():
    # 記録漏れを後から入力しても、最新の実施日が正本であり続けることを確認する。
    records = [date(2026, 8, 20)]
    after_late_entry = records + [date(2025, 4, 10)]

    assert next_due(
        last_done_on=latest_done(after_late_entry), cycle_months=12
    ) == date(2027, 8, 20)


# --- 業務上の今日 ---------------------------------------------------------


def test_業務上の今日は日本時間で決まる():
    """公開しているデモは協定世界時で動いている。

    date.today() をそのまま使うと、日本の朝9時より前は前日を指す。
    期限当日を境界として厳密に扱っているので、1日のずれが
    そのまま「期限切れ」と「まだ有効」の差になる。
    """
    from datetime import datetime, timezone

    from core.schedule import BUSINESS_TZ, business_today

    # 日本時間 2026-08-31 00:30 は、協定世界時ではまだ 08-30 15:30
    jst_midnight = datetime(2026, 8, 31, 0, 30, tzinfo=BUSINESS_TZ)
    assert jst_midnight.astimezone(timezone.utc).date() == date(2026, 8, 30)
    assert jst_midnight.date() == date(2026, 8, 31)

    # 実際の呼び出しが日本時間の日付と一致すること
    assert business_today() == datetime.now(BUSINESS_TZ).date()


def test_境界の1日ずれが判定を変えてしまうこと():
    """なぜ日付のずれを気にするのか、を残しておく。"""
    due = date(2026, 8, 31)
    assert status_of(due, date(2026, 8, 31)) == "due_soon"   # 当日はまだ切れていない
    assert status_of(due, date(2026, 9, 1)) == "overdue"     # 翌日は切れている
