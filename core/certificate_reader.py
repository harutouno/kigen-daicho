"""証明書の画像から日付を読み取る。

差し替えられる形にしてある。既定はモックで、外部へはつながらない。

--------------------------------------------------------------------------
守ること
--------------------------------------------------------------------------

**読み取れなかった項目は空欄のままにする。推測で埋めない。**

日付が読めなかったときに「たぶんこれだろう」を入れると、目視確認が形骸化する。
人は埋まっている欄をそのまま通してしまう。空欄なら気づく。

**読み取った値をそのまま保存しない。** 必ず人が見て確定させる。
この決まりは画面側で守っている（フォームを 1 枚挟む）。

--------------------------------------------------------------------------
本物の読み取りについて
--------------------------------------------------------------------------

**ClaudeCertificateReader は未検証です。** API キーが無いため、実際に呼び出して
動くことを確認していません。書いてあるだけで、動作を保証しません。

    pip install anthropic
    set ANTHROPIC_API_KEY=...
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date
from typing import Protocol

__all__ = [
    "Extracted",
    "CertificateReader",
    "MockCertificateReader",
    "ClaudeCertificateReader",
]

MODEL = "claude-opus-5"


@dataclass(frozen=True)
class Extracted:
    """画像から読み取れた項目。読めなかったものは None のまま。

    「読めなかった」と「空だった」を区別しない。どちらも人が入れる必要がある、
    という点では同じであり、区別しても画面ですることは変わらないため。
    """

    expiry_on: date | None = None
    issued_on: date | None = None
    number: str | None = None
    note: str = ""

    @property
    def anything_found(self) -> bool:
        return any((self.expiry_on, self.issued_on, self.number))


class CertificateReader(Protocol):
    """読み取り役。差し替えられるようにインターフェースを切っている。"""

    def read(self, data: bytes, filename: str) -> Extracted: ...


class MockCertificateReader:
    """外部へつながない読み取り役。決まった値を返す。

    デモで流れを見せるためのもの。実際に画像を解析してはいない。
    画面にもモックである旨を出すこと。
    """

    def __init__(self, expiry_on: date | None = None) -> None:
        self._expiry_on = expiry_on

    def read(self, data: bytes, filename: str) -> Extracted:
        return Extracted(
            expiry_on=self._expiry_on,
            number=None,
            note=(
                "モックの読み取り結果です。画像は解析していません。"
                "実際の値を入力してください。"
            ),
        )


class ClaudeCertificateReader:
    """画像から日付を読み取る。**未検証。**

    読めなかった項目は null で返させる。推測させると、目視確認が形骸化する。
    """

    SCHEMA = {
        "type": "object",
        "properties": {
            "expiry_on": {"type": ["string", "null"], "description": "有効期限 YYYY-MM-DD"},
            "issued_on": {"type": ["string", "null"], "description": "交付日 YYYY-MM-DD"},
            "number": {"type": ["string", "null"], "description": "証番号"},
        },
        "required": ["expiry_on", "issued_on", "number"],
        "additionalProperties": False,
    }

    SYSTEM = (
        "資格の証明書や修了証の画像から、日付と番号を読み取ってください。\n"
        "\n"
        "守ること:\n"
        "・画像にはっきり書かれていないものは null にしてください。\n"
        "  推測して埋めないでください。埋めると、人の目視確認が形だけになります。\n"
        "・和暦（令和・平成）は西暦に直してください。\n"
        "・日付は YYYY-MM-DD で書いてください。\n"
        "・氏名や生年月日は読み取らないでください。この台帳では使いません。"
    )

    def read(self, data: bytes, filename: str) -> Extracted:
        import anthropic

        media_type = "image/png" if filename.lower().endswith(".png") else "image/jpeg"

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=self.SYSTEM,
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": self.SCHEMA},
            },
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.b64encode(data).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": "この証明書から読み取ってください。"},
                    ],
                }
            ],
        )

        text = next(b.text for b in response.content if b.type == "text")
        parsed = json.loads(text)
        return Extracted(
            expiry_on=_to_date(parsed.get("expiry_on")),
            issued_on=_to_date(parsed.get("issued_on")),
            number=parsed.get("number") or None,
            note="読み取り結果です。必ず現物と見比べてから確定してください。",
        )


def _to_date(value) -> date | None:
    """読めない値は None。例外にせず、空欄として人へ渡す。"""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None
