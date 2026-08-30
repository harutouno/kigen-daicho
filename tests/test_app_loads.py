"""画面（app.py）が読み込める状態かを確かめる。

このテストを足した理由:

    app.py の構文を壊したまま公開してしまったことがある。ほかのテストは
    core/ だけを対象にしていて app.py を一度も読み込まないため、
    「58件すべて成功」と出ていても app.py が壊れているという状態が起きた。

    テストが通ったことと、動くことは別である。テストが何を読んでいないかを
    分かっていないと、通ったこと自体が誤った安心につながる。

Streamlit を実行せずに検証したいので、import ではなく構文解析で確かめる。
import すると st.set_page_config が走り、実行環境が必要になるため。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def python_files() -> list[Path]:
    """リポジトリ内の Python ファイル。仮想環境や生成物は含めない。"""
    skip = {".probe", ".venv", "__pycache__", ".git"}
    return [
        p for p in ROOT.rglob("*.py")
        if not any(part in skip for part in p.parts)
    ]


def test_リポジトリ内のすべてのpythonファイルが構文として正しい():
    broken: list[str] = []
    for path in python_files():
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:
            broken.append(f"{path.relative_to(ROOT)}:{e.lineno} {e.msg}")

    assert not broken, "構文が壊れているファイルがあります:\n" + "\n".join(broken)


def test_画面がドメイン層のどこを使っているか():
    """app.py が core/ の関数を実際に呼んでいることを確かめる。

    名前を変えたときに import だけ残って呼び出しが消える、という壊れ方を防ぐ。
    """
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    for name in ("build_rows", "summarize_by_subject", "preflight_check"):
        assert name in called, f"app.py が {name} を呼んでいません"


def test_画面はドメイン層に計算を持ち込んでいない():
    """app.py が日付計算を自前で持っていないことを確かめる。

    期日の決め方は core/schedule.py に集約する、という約束を守れているか。
    画面側で timedelta を使い始めると、判定が二箇所に分かれて食い違う。
    """
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "timedelta" not in source, (
        "app.py で日付計算をしています。期日の計算は core/schedule.py に置いてください"
    )
