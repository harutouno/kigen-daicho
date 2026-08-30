"""本物の AI を使う場合の意図の言い換え。

**この経路は未検証です。** API キーが無いため、実際に呼び出して動くことを
確認していません。書いてあるだけで、動作を保証しません。

--------------------------------------------------------------------------
なにをさせるか
--------------------------------------------------------------------------

答えの文章は作らせません。**言い換えだけ**をさせます。

    「うちの現場、来月あたま出す書類で引っかかりそうなのある？」
      ↓ 言い換え
    「2026年10月1日に提出したら何が引っかかる？」
      ↓ 従来どおり台帳の判定関数へ
    submission_check(...) の結果をそのまま言葉にする

こうすると、件数や名前は必ず台帳から出ます。文章を自由に生成させると、
画面が「3件」と言っているのに「4件」と答える、という食い違いが起きます。
それがこの道具では一番困るので、生成させる範囲を言い換えに限定しています。

言い換えられないと判断した場合は supported=false を返させ、
呼び出し側は従来どおり「答えられません」と言います。推測させません。

--------------------------------------------------------------------------
使い方
--------------------------------------------------------------------------

    pip install anthropic
    set ANTHROPIC_API_KEY=...        （または ant auth login）

そのうえで app.py の AI_API_ENABLED を True にします。
SDK はここでしか読み込まないので、無効のままなら依存は増えません。
"""

from __future__ import annotations

import json

MODEL = "claude-opus-5"

# 言い換え先として認めている形。ここに無い聞き方は supported=false にさせる。
CANONICAL_FORMS = [
    "期限が切れているものを教えて",
    "N日以内に切れるものは？",
    "資格情報が無い人は？",
    "YYYY年M月D日に提出したら何が引っかかる？",
    "（氏名）さんは大丈夫？",
    "社員を登録したい",
    "道具を登録したい",
    "資格を追加したい",
    "実施を記録したい",
    "提出前チェックをしたい",
    "点検の周期を変えたい",
]

SYSTEM = (
    "あなたは、設備工事会社の期限管理台帳の入力を整える係です。\n"
    "利用者が書いた文を、決められた言い方のどれかに言い換えてください。\n"
    "\n"
    "守ること:\n"
    "・答えを作らないでください。件数や名前を推測して書いてはいけません。\n"
    "  実際の答えは台帳が出します。あなたの仕事は言い換えだけです。\n"
    "・日付を含む言い方に直すときは、西暦・月・日をすべて数字で書いてください。\n"
    "・どの言い方にも当てはまらないと判断したら、supported を false にしてください。\n"
    "  無理に近いものへ寄せないでください。台帳の表示と食い違う答えが出る方が困ります。\n"
    "\n"
    "決められた言い方:\n" + "\n".join(f"・{f}" for f in CANONICAL_FORMS)
)

SCHEMA = {
    "type": "object",
    "properties": {
        "supported": {"type": "boolean"},
        "canonical": {"type": "string"},
    },
    "required": ["supported", "canonical"],
    "additionalProperties": False,
}


def is_available() -> bool:
    """SDK が入っているか。鍵の有無までは見ない（呼び出しで分かる）。"""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def normalize(question: str, *, today_text: str) -> str | None:
    """自由な文を、判定できる言い方へ直す。直せなければ None。

    **未検証。** 例外は呼び出し側で受けてください。ここでは握り潰しません。
    黙って None を返すと、接続できていないのか言い換えられないのかが
    区別できなくなるためです。
    """
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        system=SYSTEM,
        # 分類なので浅い思考で足りる。深くしても言い換えの質は上がらない。
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
        messages=[
            {
                "role": "user",
                "content": f"今日は {today_text} です。\n\n次の文を言い換えてください。\n{question}",
            }
        ],
    )

    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)

    if not data.get("supported"):
        return None
    canonical = str(data.get("canonical", "")).strip()
    return canonical or None
