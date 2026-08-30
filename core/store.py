"""台帳の読み書き。

保存先は JSON ファイル 1 本にしている。このデモの目的は期限の扱い方を示すことで
あり、データベースの選定ではないため。実運用では差し替える前提で、画面が直接
ファイルを触らないようにこの層を挟んでいる。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from core.models import Ledger, LedgerDataError

__all__ = ["load_ledger", "save_ledger", "seed_generated_on", "SEED_PATH"]

SEED_PATH = Path(__file__).resolve().parents[1] / "data" / "seed.json"


def load_ledger(path: Path | str = SEED_PATH) -> Ledger:
    """JSON から台帳を読む。"""
    p = Path(path)
    with p.open(encoding="utf-8") as f:
        return Ledger.from_dict(json.load(f))


def seed_generated_on(path: Path | str = SEED_PATH) -> date:
    """同梱データが、どの日を基準に組み立てられたか。

    同梱データは実行した日を基準に作られるので、「今日」で数えた統計は
    日が経つだけで変わる。README に載せる数字はこの日で固定する。
    """
    p = Path(path)
    with p.open(encoding="utf-8") as f:
        value = json.load(f).get("generated_on")
    if not value:
        raise LedgerDataError(f"{p.name} に generated_on がありません")
    return date.fromisoformat(value)


def save_ledger(ledger: Ledger, path: Path | str) -> None:
    """台帳を JSON に書く。

    書き込み中に落ちた場合に元のファイルを壊さないよう、一時ファイルへ
    書いてから置き換える。
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(ledger.to_dict(), f, ensure_ascii=False, indent=2)
    tmp.replace(p)
