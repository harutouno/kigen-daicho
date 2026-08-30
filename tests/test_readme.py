"""README に書いてある数字が、実物と合っているかを確かめる。

README は誰も検算しない。実際この README には「1年後 50件」と書いてあったが、
同梱データでの実測は 117件で、2倍以上ずれていた。
コードは直しても、説明は直し忘れる。だから機械に突き合わせさせる。

ここが落ちたときは README を直すか、`python -m data.stats` の出力を貼り直す。

見ているのは「製品の振る舞いについての主張」だけにしている。
テストの本数や行数まで縛ると、テストを1本足すたびに落ちるだけで、
正しさは何も守れない。
"""

from __future__ import annotations

import re
from pathlib import Path

from data.stats import as_markdown, table

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_提出日ごとの件数が同梱データと一致する():
    for line in table():
        row = re.search(
            rf"^\|\s*{re.escape(line.label)}\s*\|\s*(\d+)件\s*\|\s*(\d+)件\s*\|",
            README, re.M,
        )
        assert row, f"README に「{line.label}」の行がありません"
        assert int(row.group(1)) == line.blocked, (
            f"{line.label}: README は {row.group(1)}件、実測は {line.blocked}件。"
            f"\n`python -m data.stats` の出力に貼り替えてください:\n{as_markdown()}"
        )
        assert int(row.group(2)) == line.still_valid, (
            f"{line.label}（うち今日はまだ有効）: "
            f"README は {row.group(2)}件、実測は {line.still_valid}件"
        )


def test_問題なしの人数が同梱データと一致する():
    from datetime import date

    from core.review import summarize_by_subject
    from core.store import load_ledger

    people = summarize_by_subject(load_ledger(), date.today(), kind="person")
    ok = sum(1 for s in people if not s.needs_action)

    written = re.search(r"今日の画面では(\d+)人中(\d+)人が「問題なし」", README)
    assert written, "README に人数の記載がありません"
    assert (int(written.group(1)), int(written.group(2))) == (len(people), ok)


def test_構成に書いたファイルがすべて存在する():
    """構成の説明に、もう無いファイルが残らないようにする。"""
    block = README.split("## 8. 構成")[1]
    for name in re.findall(r"^\s{2,}(\S+\.py)\s", block, re.M):
        matches = list(ROOT.rglob(name))
        assert matches, f"README に載っている {name} が見つかりません"
