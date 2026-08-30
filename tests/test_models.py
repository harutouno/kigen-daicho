"""保存形式の読み書きの検証。

ここで気にしているのは「壊れたデータで落ちること」ではなく、
**壊れたデータが素通りして、判定だけが静かに狂うこと**である。

date_mode が知らない値だと、期限のある資格が「期限なし」として扱われ、
警告に出なくなる。画面は正常に見えるので、誰も気づけない。
落ちる方がまだ安全なので、読み込みの時点で止める。
"""

from __future__ import annotations

import copy
from datetime import date

import pytest

from core.models import Holding, Ledger, LedgerDataError, Record
from core.store import load_ledger


@pytest.fixture(scope="module")
def seed() -> dict:
    """アプリが実際に読むデータ。"""
    return load_ledger().to_dict()


def test_実データが読み込める(seed):
    lg = Ledger.from_dict(seed)
    assert lg.requirements and lg.subjects and lg.holdings


def test_保存して読み直しても内容が変わらない(seed):
    """往復で欠ける項目があると、編集して保存した時点で情報が消える。"""
    assert Ledger.from_dict(seed).to_dict() == seed


@pytest.mark.parametrize(
    "説明, 壊す",
    [
        ("date_mode に知らない値",
         lambda d: d["requirements"][0].update(date_mode="まいつき")),
        ("category に知らない値",
         lambda d: d["requirements"][0].update(category="その他")),
        ("obligation に知らない値",
         lambda d: d["requirements"][0].update(obligation="なんとなく")),
        ("周期で計算する設定なのに周期が無い",
         lambda d: d["requirements"][0].update(date_mode="cycle", cycle_months=None)),
        ("種類に名前が無い",
         lambda d: d["requirements"][0].update(name="")),
        ("実施記録に実施日が無い",
         lambda d: d["holdings"][0]["records"].insert(0, {"done_on": None})),
        ("日付として読めない文字列",
         lambda d: d["holdings"][0].update(fixed_due_on="2026-13-45")),
    ],
)
def test_受け付けられないデータは読み込みで止める(seed, 説明, 壊す):
    broken = copy.deepcopy(seed)
    壊す(broken)
    with pytest.raises(LedgerDataError):
        Ledger.from_dict(broken)


def test_実施日が無い記録は例外にする():
    """assert だと python -O で消える。消えると None のまま先へ流れる。"""
    with pytest.raises(LedgerDataError):
        Record.from_dict({"done_on": None})


def test_実施記録は型として追記のみになっている():
    """「追記のみ」を決まりごとではなく型で守らせる。

    リストのままだと holding.records.clear() ができてしまい、
    「消せない」と書いてあるのに消せる、という状態になる。
    """
    h = Holding(id="h1", subject_id="p1", requirement_id="kenshin",
                records=[Record(done_on=date(2026, 1, 1))])
    assert isinstance(h.records, tuple)
    with pytest.raises(AttributeError):
        h.records.append(Record(done_on=date(2026, 2, 1)))
    h.add_record(Record(done_on=date(2026, 2, 1)))
    assert len(h.records) == 2


# --- 拠点と職種の一覧 -----------------------------------------------------


def test_最後の一人を消しても拠点は選択肢に残る():
    """事業所は人がいなくても存在する。

    登録済みの社員から数え上げるだけだと、その拠点の最後の1人を消した瞬間に
    拠点そのものが選択肢から消え、次の人を登録できなくなる。
    """
    lg = Ledger(site_master=["本社", "奄美支店"])
    assert lg.sites == ["本社", "奄美支店"]   # 社員が0人でも残る


def test_一覧に無い拠点も選択肢に出す():
    """一覧に載せ忘れた拠点の社員が、編集画面で別の拠点にすり替わらないこと。"""
    from core.models import Subject

    lg = Ledger(site_master=["本社"],
                subjects=[Subject(id="p1", name="甲", kind="person", site="種子島出張所")])
    assert lg.sites == ["本社", "種子島出張所"]


def test_一覧の順番を並べ替えない():
    """本社を先頭に置いてあるものを五十音に並べ替えると、毎回探すことになる。"""
    lg = Ledger(site_master=["本社", "奄美支店", "指宿支店"])
    assert lg.sites == ["本社", "奄美支店", "指宿支店"]


def test_人の職種と道具の種別を混ぜない():
    """混ぜると、社員の登録画面の職種欄に「絶縁用保護具」が出る。"""
    lg = Ledger(role_master={"person": ["施工管理"], "asset": ["絶縁用保護具"]})
    assert lg.roles("person") == ["施工管理"]
    assert lg.roles("asset") == ["絶縁用保護具"]


def test_拠点と職種は保存して読み直しても残る():
    lg = Ledger(site_master=["本社"], role_master={"person": ["施工管理"]})
    back = Ledger.from_dict(lg.to_dict())
    assert back.site_master == ["本社"]
    assert back.roles("person") == ["施工管理"]


def test_空の名前は受け付けない():
    lg = Ledger()
    with pytest.raises(LedgerDataError):
        lg.add_site("   ")
    with pytest.raises(LedgerDataError):
        lg.add_role("  ", "person")


def test_同じ名前を二重に登録しない():
    lg = Ledger()
    lg.add_site("本社")
    lg.add_site("本社")
    assert lg.site_master == ["本社"]
