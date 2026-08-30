"""証明書の読み取りの検証。

一番大事なのは「読めなかったものを埋めない」こと。
埋めると、人の目視確認が形だけになる。
"""

from __future__ import annotations

from datetime import date

from core.certificate_reader import (
    ClaudeCertificateReader,
    Extracted,
    MockCertificateReader,
)


def test_読み取れなかった項目は空のまま():
    got = Extracted()
    assert got.expiry_on is None
    assert got.issued_on is None
    assert got.number is None
    assert got.anything_found is False


def test_何か読み取れれば分かる():
    got = Extracted(expiry_on=date(2027, 3, 31))
    assert got.anything_found is True


def test_モックは画像を解析しないことを明示する():
    got = MockCertificateReader().read(b"not-an-image", "dummy.jpg")
    assert got.expiry_on is None
    assert "モック" in got.note
    assert "解析していません" in got.note


def test_モックに日付を持たせて流れを見せられる():
    got = MockCertificateReader(expiry_on=date(2027, 3, 31)).read(b"", "x.jpg")
    assert got.expiry_on == date(2027, 3, 31)


def test_読み取り役の指示に推測を禁じる文言が入っている():
    """指示から『推測しない』が消えると、空欄が埋まって目視確認が形骸化する。

    文言そのものが仕様なので、消えたら気づけるようにしている。
    """
    system = ClaudeCertificateReader.SYSTEM
    assert "null" in system
    assert "推測して埋めないで" in system


def test_氏名と生年月日を読み取らせない():
    """この台帳では使わない個人情報を、画像から拾わせない。"""
    assert "氏名や生年月日は読み取らないで" in ClaudeCertificateReader.SYSTEM


def test_読み取れない値は例外にせず空として扱う():
    """途中で落ちるより、空欄にして人へ渡す方が使える。"""
    from core.certificate_reader import _to_date

    assert _to_date(None) is None
    assert _to_date("") is None
    assert _to_date("令和9年3月31日") is None
    assert _to_date("2027-03-31") == date(2027, 3, 31)


def test_日付の項目はすべて必須として問い合わせる():
    """null を返させるために、項目自体は必ず返させる。

    省略できると『触れていない』と『読めなかった』の区別が付かなくなる。
    """
    schema = ClaudeCertificateReader.SCHEMA
    assert set(schema["required"]) == {"expiry_on", "issued_on", "number"}
    assert schema["additionalProperties"] is False
    for name in ("expiry_on", "issued_on", "number"):
        assert "null" in schema["properties"][name]["type"]
