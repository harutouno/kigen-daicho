"""画面を実際に起動して、通しで動くかを確かめる。

test_app_loads.py は構文が壊れていないことしか見ない。
以前 app.py の構文を壊したまま「58件通過」と表示された事故を受けて足したものだが、
構文が正しくても、実行時に落ちる壊れ方は捕まえられない。

  ・Streamlit の使い方の誤り
  ・実行時の NameError
  ・widget の key の重複
  ・session_state の遷移の壊れ

ここでは Streamlit 公式のテスト機構でアプリを実際に走らせ、
画面が立ち上がって主要な導線が通ることを確かめる。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

APP = str(Path(__file__).resolve().parents[1] / "app.py")

AppTest = pytest.importorskip(
    "streamlit.testing.v1", reason="Streamlit が入っていない環境では飛ばす"
).AppTest


def run(**session):
    at = AppTest.from_file(APP, default_timeout=30)
    for key, value in session.items():
        at.session_state[key] = value
    return at.run()


def test_初期画面が例外なく立ち上がる():
    at = run()
    assert not at.exception, [str(e) for e in at.exception]


def test_主要な画面がすべて例外なく開く():
    for page in (
        "社員の資格・健診",
        "道具・機器の点検",
        "安全書類の提出前チェック",
        "種類の設定",
        "AIサポート",
    ):
        at = run(nav=page)
        assert not at.exception, f"{page}: {[str(e) for e in at.exception]}"


def test_社員の登録画面が開く():
    at = run(nav="社員の資格・健診", registering="person")
    assert not at.exception, [str(e) for e in at.exception]


def test_道具の登録画面が開く():
    at = run(nav="道具・機器の点検", registering="asset")
    assert not at.exception, [str(e) for e in at.exception]


def test_社員の詳細が開く():
    at = run(nav="社員の資格・健診", selected="p-001")
    assert not at.exception, [str(e) for e in at.exception]


def test_資格の詳細が開く():
    at = run(nav="社員の資格・健診", selected="p-001", selected_holding="h-0002")
    assert not at.exception, [str(e) for e in at.exception]


def test_社員の編集画面が開く():
    at = run(nav="社員の資格・健診", editing="p-001")
    assert not at.exception, [str(e) for e in at.exception]


def test_削除の確認が開く():
    at = run(nav="社員の資格・健診", selected="p-001", deleting="p-001")
    assert not at.exception, [str(e) for e in at.exception]


def test_AIサポートが答えを返す():
    at = run(nav="AIサポート")
    at.session_state["ai-question"] = "期限が切れているものを教えて"
    at = at.run()
    assert not at.exception, [str(e) for e in at.exception]


def test_検索して見つからないとき登録へ進める():
    """探して見つからなかった直後が、登録したい瞬間である。

    以前ここには「新しく登録する機能はまだ作っていません」と出ていた。
    登録画面はすでにあるので、案内が実装より古いままだった。
    """
    at = run(nav="社員の資格・健診")
    at.session_state["search"] = "存在しない名前ZZZ"
    at = at.run()
    assert not at.exception, [str(e) for e in at.exception]

    btn = [b for b in at.button if b.key == "btn-register-from-search-person"]
    assert btn, "0件のときに登録への導線が無い"

    at = btn[0].click().run()
    assert not at.exception, [str(e) for e in at.exception]
    # 打った言葉が名前欄に引き継がれていること。捨てると打ち直しになる。
    assert at.session_state["reg-name"] == "存在しない名前ZZZ"


def _click(at, key):
    hit = [b for b in at.button if b.key == key]
    assert hit, f"{key} が画面に無い"
    return hit[0].click().run()


def test_設定画面から拠点を追加できる():
    at = run(nav="種類の設定")
    assert not at.exception, [str(e) for e in at.exception]

    at.session_state["master-input-site"] = "種子島出張所"
    at = _click(at.run(), "master-add-site")
    assert not at.exception, [str(e) for e in at.exception]

    # 追加した拠点が、社員の登録画面で選べるようになっていること。
    # 台帳に足せても選べなければ、足した意味がない。
    at.session_state["registering"] = "person"
    at.session_state["nav"] = "社員の資格・健診"
    at = at.run()
    assert not at.exception, [str(e) for e in at.exception]
    assert "種子島出張所" in at.radio(key="reg-site").options


def test_空の名前を追加しようとしても落ちない():
    at = run(nav="種類の設定")
    at.session_state["master-input-site"] = "   "
    at = _click(at.run(), "master-add-site")
    assert not at.exception, [str(e) for e in at.exception]
    assert at.error, "理由が表示されていない"


def test_資格情報なしのカードが自分を打ち消さない():
    """一覧では要対応として並べているのに、カードの中で
    「対応が必要な項目はありません」と書いていた。

    記録が1件も無いことは、問題が無いことではない。
    """
    at = run(nav="社員の資格・健診")
    assert not at.exception, [str(e) for e in at.exception]

    text = " ".join(str(m.value) for m in at.markdown) + " ".join(
        str(m.value) for m in at.get("text")
    )
    if "資格情報なし" in text:
        assert "対応が必要な項目はありません" not in text, (
            "「資格情報なし」と「対応が必要な項目はありません」が同時に出ている"
        )


# --- 訂正と有効期限の更新 -------------------------------------------------


def _holding_of(at, date_mode: str):
    """同梱データから、指定した期限の決まり方の保有を1件選ぶ。"""
    lg = at.session_state["ledger"]
    for h in lg.holdings:
        req = lg.requirement(h.requirement_id)
        if req and req.date_mode == date_mode and h.records:
            return lg, h
    raise AssertionError(f"{date_mode} の記録つき保有が同梱データに無い")


def test_実施記録を画面から訂正できる():
    """正しい日付をただ追記しても直らない。訂正の導線が要る。"""
    at = run(nav="社員の資格・健診")
    lg, h = _holding_of(at, "cycle")
    target = sorted(h.records, key=lambda r: r.done_on)[-1]
    # 台帳はその場で書き換わるので、件数は先に控える。
    # 同じオブジェクトを前後で比べても差は出ない。
    before_count = len(h.records)

    at.session_state["selected"] = h.subject_id
    at.session_state["selected_holding"] = h.id
    at.session_state["seen_record"] = target.id
    at = at.run()
    assert not at.exception, [str(e) for e in at.exception]

    at.session_state[f"fix-date-{h.id}-{len(h.records)}"] = date(2025, 3, 3)
    at = at.run()
    btn = [b for b in at.button if b.key.startswith(f"btn-fix-{h.id}")]
    assert btn, "訂正のボタンが無い"
    at = btn[0].click().run()
    assert not at.exception, [str(e) for e in at.exception]

    lg = at.session_state["ledger"]
    h2 = next(x for x in lg.holdings if x.id == h.id)
    assert target.id in h2.superseded_ids, "訂正済みになっていない"
    assert h2.last_done_on == date(2025, 3, 3)
    assert len(h2.records) == before_count + 1, "元の記録が消えている"


def test_有効期限の更新が過去の判定を書き換えない():
    at = run(nav="社員の資格・健診")
    lg = at.session_state["ledger"]
    h = next(x for x in lg.holdings
             if lg.requirement(x.requirement_id).date_mode == "fixed"
             and x.fixed_due_on)
    before = h.fixed_due_on

    at.session_state["selected"] = h.subject_id
    at.session_state["selected_holding"] = h.id
    at = at.run()
    at.session_state[f"detail-newdue-{h.id}"] = date(2035, 12, 31)
    at = at.run()
    btn = [b for b in at.button if b.key == "btn-detail-renew"]
    assert btn, "期限を登録するボタンが無い"
    at = btn[0].click().run()
    assert not at.exception, [str(e) for e in at.exception]

    lg = at.session_state["ledger"]
    h2 = next(x for x in lg.holdings if x.id == h.id)
    # 受け取った日（既定は今日）より前の判定は、元の期限のまま。
    # 「その時点では切れていた」という事実を、あとから消さないため。
    assert h2.expiry_on_at(date(2020, 1, 1)) == before
    # 受け取った日より後は、新しい期限が効く。
    assert h2.expiry_on_at(date(2035, 1, 1)) == date(2035, 12, 31)
