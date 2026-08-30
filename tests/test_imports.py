"""app.py が取り込んでいる名前が、取り込み先に実在するかを確かめる。

本番で `ImportError` を出したことがある。ローカルでは動いていた。
原因はコードではなく、古いモジュールを抱えたままのプロセスだったが、
「app.py が新しい名前を増やしたのに、その名前がモジュール側に無い」という
本物の壊れ方も、まったく同じ見え方をする。

app.py を import してしまえば Streamlit の実行環境が要るので、
取り込み文だけを構文木から読み、名前の実在を確かめる。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 画面側のファイル。core/ の中は互いに import し合うので、まとめて見る。
SOURCES = sorted(
    p for p in ROOT.rglob("*.py")
    if "__pycache__" not in p.parts and "tests" not in p.parts
)


def _imports(path: Path) -> list[tuple[str, str, int]]:
    """(モジュール名, 取り込む名前, 行番号) を返す。自作モジュールのみ。"""
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] not in {"core", "ui", "data"}:
                continue
            for alias in node.names:
                out.append((node.module, alias.name, node.lineno))
    return out


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_取り込んでいる名前が実在する(path: Path):
    for module_name, name, lineno in _imports(path):
        module = importlib.import_module(module_name)
        if hasattr(module, name):
            continue
        # `from core import intent_llm` のように、下位モジュールを
        # 取り込んでいる場合。読み込めれば実在する。
        try:
            importlib.import_module(f"{module_name}.{name}")
            continue
        except ImportError:
            pass
        assert False, (
            f"{path.name}:{lineno} が {module_name}.{name} を取り込もうとしていますが、"
            f"{module_name} にその名前はありません"
        )


def test_公開されている名前がそろっている():
    """__all__ に書いたのに定義していない、という食い違いを見つける。"""
    for path in SOURCES:
        module_name = (
            path.stem if path.parent == ROOT
            else f"{path.parent.name}.{path.stem}"
        )
        module = importlib.import_module(module_name)
        for name in getattr(module, "__all__", []):
            assert hasattr(module, name), f"{module_name}.__all__ の {name} が未定義"
