"""同梱データ（架空）を生成する。

このファイルを実行すると data/seed.json が作り直される。

    python data/generate_seed.py

方針:
  * 実在の個人・顧客とは一切関係のない架空データである。
  * 「問題のある人」は手で書く。デモとして意味のある場面を意図して作るため。
  * 残りの大多数は機械的に生成する。76名規模でこそ「手を付ける人だけ表示する」
    ことの価値が見えるため。
  * 乱数は使わない。実行するたびに同じ結果になり、差分が読めるようにする。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent / "seed.json"

# 同梱データの基準日。実行した日を使う。
#
# 固定の日付を書き込むと、時間が経つほど「期限間近」の人が全員「期限切れ」へ
# 移っていき、デモとして意図した見え方が崩れる。見せる直前にこの生成を
# 走らせれば、いつでも同じ配分になる。
TODAY = date.today()

SITES = ["鹿児島本社", "川内支店", "国分支店", "志布志支店", "姶良支店", "苓北支店"]
ROLES = ["施工管理", "電気工事", "保守・計装", "事務"]

SURNAMES = [
    "山口", "迫田", "東", "前田", "川畑", "有村", "田中", "中村", "山下", "佐藤",
    "松本", "加藤", "今村", "上野", "内田", "大久保", "串木野", "郡山", "重信", "白坂",
    "瀬戸口", "曽木", "高崎", "竹之内", "田代", "鶴田", "徳永", "永田", "西田", "野元",
    "橋口", "浜田", "肥後", "福留", "藤崎", "堀之内", "牧野", "溝口", "宮里", "村岡",
]
GIVEN = [
    "直人", "和樹", "亮", "修司", "悠", "千尋", "新一", "健一", "太郎", "龍也",
    "裕介", "由美", "和也", "誠", "翔", "陽子", "健太", "麻衣", "拓海", "沙織",
]


def person(idx: int) -> dict:
    # 姓と名の周期が重なると、離れた番号の人が同姓同名になる。
    # 名の選び方に姓の周回数を混ぜて、それを避ける。
    surname = SURNAMES[idx % len(SURNAMES)]
    given = GIVEN[(idx * 7 + idx // len(SURNAMES)) % len(GIVEN)]
    return {
        "id": f"p-{idx:03d}",
        "name": f"{surname} {given}",
        "kind": "person",
        "site": SITES[idx % len(SITES)],
        "role": ROLES[(idx * 3) % len(ROLES)],
    }


def iso(d: date) -> str:
    return d.isoformat()


def build() -> dict:
    requirements = json.loads(
        (Path(__file__).resolve().parent / "requirements.json").read_text(encoding="utf-8")
    )

    subjects: list[dict] = []
    holdings: list[dict] = []
    seq = 0

    def add_holding(subject_id: str, requirement_id: str, **kw) -> None:
        nonlocal seq
        seq += 1
        holdings.append(
            {
                "id": f"h-{seq:04d}",
                "subject_id": subject_id,
                "requirement_id": requirement_id,
                "fixed_due_on": kw.get("fixed_due_on"),
                "records": kw.get("records", []),
                "note": kw.get("note", ""),
            }
        )

    # --- 手で作る「問題のある人」 -----------------------------------------
    #
    # それぞれ違う種類の問題を担当させ、画面で何が起きるかを一通り見せる。

    named = [
        # 資格者証は有効なのに講習だけ切れている。片方だけ見ると通してしまう例。
        {
            "id": "p-001", "name": "迫田 和樹", "site": "鹿児島本社", "role": "施工管理",
            "holdings": [
                ("req-kanri-cert", {"fixed_due_on": iso(TODAY + timedelta(days=214))}),
                ("req-kanri-course", {"fixed_due_on": iso(TODAY - timedelta(days=29)),
                                      "note": "資格者証は有効だが講習が切れている。"}),
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=192)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        # 健診が大きく超過している例。
        {
            "id": "p-002", "name": "田中 新一", "site": "志布志支店", "role": "施工管理",
            "holdings": [
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=476)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        # 定期講習が切れている例。
        {
            "id": "p-003", "name": "山口 正人", "site": "川内支店", "role": "電気工事",
            "holdings": [
                ("req-den1-course", {"records": [{"done_on": iso(TODAY - timedelta(days=1962)),
                                                  "done_by": "本人", "memo": "定期講習"}]}),
                ("req-den1-menjo", {}),
                # 定期講習が切れているうえに健診も近い。1 人が複数抱える例。
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=343)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        # 前回受講日が台帳に無く、期日を計算できない例。
        {
            "id": "p-004", "name": "東 亮", "site": "鹿児島本社", "role": "施工管理",
            "holdings": [
                ("req-den1-course", {"note": "免状は保有しているが前回の受講日が台帳に無い。"}),
                ("req-den1-menjo", {}),
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=88)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        # 同じく未確定。危険物の保安講習。
        {
            "id": "p-005", "name": "中村 健一", "site": "国分支店", "role": "電気工事",
            "holdings": [
                ("req-kikenbutsu-course", {"note": "従事開始時の受講日が不明。"}),
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=52)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        # 期限が近い人たち。
        {
            "id": "p-006", "name": "山下 太郎", "site": "鹿児島本社", "role": "施工管理",
            "holdings": [
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=353)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        {
            "id": "p-007", "name": "佐藤 龍也", "site": "志布志支店", "role": "電気工事",
            "holdings": [
                ("req-den1-course", {"records": [{"done_on": iso(TODAY - timedelta(days=1804)),
                                                  "done_by": "本人", "memo": "定期講習"}]}),
                ("req-den1-menjo", {}),
            ],
        },
        {
            "id": "p-008", "name": "松本 裕介", "site": "川内支店", "role": "施工管理",
            "holdings": [
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=338)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
                ("req-driver-license", {"fixed_due_on": iso(TODAY + timedelta(days=52))}),
            ],
        },
        {
            "id": "p-009", "name": "加藤 由美", "site": "姶良支店", "role": "事務",
            "holdings": [
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=335)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        {
            "id": "p-010", "name": "今村 和也", "site": "苓北支店", "role": "電気工事",
            "holdings": [
                ("req-kikenbutsu-course", {"records": [{"done_on": iso(TODAY - timedelta(days=1064)),
                                                        "done_by": "本人", "memo": "保安講習"}]}),
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=148)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        # 監理技術者として配置できる人（比較用）。
        {
            "id": "p-011", "name": "有村 千尋", "site": "鹿児島本社", "role": "保守・計装",
            "holdings": [
                ("req-kanri-cert", {"fixed_due_on": iso(TODAY + timedelta(days=1189))}),
                ("req-kanri-course", {"fixed_due_on": iso(TODAY + timedelta(days=1220))}),
                ("req-shunin-menjo", {}),
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=52)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        # 有効期限のない免状しか持っていない人（灰色に数えない例）。
        {
            "id": "p-012", "name": "川畑 悠", "site": "姶良支店", "role": "電気工事",
            "holdings": [
                ("req-den1-menjo", {}),
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=100)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
    ]

    for spec in named:
        subjects.append(
            {
                "id": spec["id"], "name": spec["name"], "kind": "person",
                "site": spec["site"], "role": spec["role"],
            }
        )
        for requirement_id, kw in spec["holdings"]:
            add_holding(spec["id"], requirement_id, **kw)

    # --- 機械的に作る「問題のない人」 -------------------------------------
    #
    # 健診を全員に持たせ、期日が十分先になるよう実施日をずらす。
    # 76 名規模にしないと「手を付ける人だけ表示する」ことの価値が見えないため。

    for i in range(13, 77):
        subjects.append(person(i))
        # 期日 = 実施日 + 12 か月。90〜300 日先に散らす。
        offset = 90 + (i * 13) % 210
        done = TODAY + timedelta(days=offset) - timedelta(days=365)
        add_holding(f"p-{i:03d}", "req-kenshin",
                    records=[{"done_on": iso(done), "done_by": "総務", "memo": "定期健診"}])

    # 同姓同名がいると、画面でどちらの話をしているのか分からなくなる。
    # 生成規則を変えたときに気づけるよう、書き出す前に検査する。
    names = [s["name"] for s in subjects]
    duplicated = sorted({n for n in names if names.count(n) > 1})
    if duplicated:
        raise SystemExit(f"同姓同名が生成されました: {duplicated}")

    ids = [s["id"] for s in subjects]
    if len(ids) != len(set(ids)):
        raise SystemExit("対象の id が重複しています")

    return {
        # どの日を基準に作られたデータかを残す。テストはこれを読んで判定する。
        "generated_on": iso(TODAY),
        "soon_days": 30,
        "upcoming_days": 60,
        "requirements": requirements,
        "subjects": subjects,
        "holdings": holdings,
    }


if __name__ == "__main__":
    OUT.write_text(
        json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    data = json.loads(OUT.read_text(encoding="utf-8"))
    print(f"{OUT.name} を書き出しました: "
          f"種別 {len(data['requirements'])} / 対象 {len(data['subjects'])} / "
          f"保有 {len(data['holdings'])}")
