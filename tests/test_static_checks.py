"""静的検査が通ることを、テストとしても確かめる。

人が読んで探すより、機械が確実に見つけられる範囲は機械に任せる。
実際に導入したとき、次のようなものが出た。

  ・使われなくなった import（preflight_check へ寄せたあとの残り）
  ・zip() の長さが食い違っても黙って切り詰められる書き方
  ・同じ関数の中で、同じ名前を別の型に使い回している箇所
  ・呼ぶ側が確かめている前提に頼っていた関数

どれも「落ちてから気づく」たぐいのもので、テストでは捕まえにくい。

検査そのものを入れても、走らせる決まりが無ければ意味がない。
pytest から呼ぶことで、テストと同じ手順で走るようにしている。

書式の自動整形（ruff format）は入れていない。18ファイル・2,000行を
超える差分になり、履歴が読めなくなるわりに、見つかる問題が無いため。
入れるなら、機能の変更が無い時期に 1 回で入れる。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(tool: str, *args: str) -> subprocess.CompletedProcess:
    if shutil.which(tool) is None and not _module_available(tool):
        pytest.skip(f"{tool} が入っていません（pip install -r requirements-dev.txt）")
    return subprocess.run(
        [sys.executable, "-m", tool, *args],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def _module_available(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None


def test_ruffが通る():
    result = _run("ruff", "check", ".", "--output-format", "concise")
    assert result.returncode == 0, "\n" + result.stdout


def test_型検査が通る():
    result = _run("mypy")
    assert result.returncode == 0, "\n" + result.stdout
