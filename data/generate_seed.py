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
import sys
from datetime import date, timedelta
from pathlib import Path

# python data/generate_seed.py と直接叩けるようにする。
# リポジトリ直下がパスに入らないため、core を読めない。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.schedule import business_today  # noqa: E402

OUT = Path(__file__).resolve().parent / "seed.json"

# 同梱データの基準日。実行した日を使う。
#
# 固定の日付を書き込むと、時間が経つほど「期限間近」の人が全員「期限切れ」へ
# 移っていき、デモとして意図した見え方が崩れる。見せる直前にこの生成を
# 走らせれば、いつでも同じ配分になる。
TODAY = business_today()

# 拠点名は架空。実在の会社の事業所一覧と一致させないこと。
# 一致していると、社名を書かなくてもどの会社の話か分かってしまう。
# 実在の地名を借りているが、この組み合わせは実在の会社のものではない。
SITES = ["本社", "鹿屋支店", "指宿支店", "出水支店", "枕崎支店", "奄美支店"]
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
        "code": f"E-{idx:04d}",
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
                "planned_on": kw.get("planned_on"),
            }
        )

    # --- 手で作る「問題のある人」 -----------------------------------------
    #
    # それぞれ違う種類の問題を担当させ、画面で何が起きるかを一通り見せる。

    named = [
        # 資格者証は有効なのに講習だけ切れている。片方だけ見ると通してしまう例。
        {
            "id": "p-001", "code": "E-0001", "kana": "さこだ かずき", "name": "迫田 和樹", "site": "本社", "role": "施工管理",
            "holdings": [
                ("req-kanri-cert", {"fixed_due_on": iso(TODAY + timedelta(days=214))}),
                ("req-kanri-course", {"fixed_due_on": iso(TODAY - timedelta(days=29)),
                                      "planned_on": iso(TODAY + timedelta(days=21)),
                                      "note": "資格者証は有効だが講習が切れている。受講を予約済み。"}),
                ("req-kenshin", {"records": [
                    {"done_on": iso(TODAY - timedelta(days=557)), "done_by": "総務",
                     "memo": "前年度の定期健診"},
                    {"done_on": iso(TODAY - timedelta(days=192)), "done_by": "総務",
                     "memo": "定期健診",
                     "attachments": [{"id": "att-0010", "filename": "健康診断個人票_2026.pdf",
                                      "size": 156000,
                                      "uploaded_on": iso(TODAY - timedelta(days=185)),
                                      "uploaded_by": "総務"}]},
                ]}),
                # 有効期限が存在しない資格・講習。保有の記録として持つが期限は発生しない。
                ("req-den1-menjo", {}),
                ("req-awp-skill", {}),
                ("req-harness-edu", {}),
                ("req-lowvolt-edu", {}),
                ("req-highvolt-edu", {}),
                ("req-shokucho-edu", {}),
            ],
        },
        # 健診が大きく超過している例。
        {
            "id": "p-002", "code": "E-0002", "kana": "たなか しんいち", "name": "田中 新一", "site": "出水支店", "role": "施工管理",
            "holdings": [
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=476)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        # 定期講習が切れている例。
        {
            "id": "p-003", "code": "E-0003", "kana": "やまぐち まさと", "name": "山口 正人", "site": "鹿屋支店", "role": "電気工事",
            "holdings": [
                # 過去の回が積み上がっている例。回ごとに証明書の控えが付く。
                ("req-den1-course", {"records": [
                    {"done_on": iso(TODAY - timedelta(days=3789)), "done_by": "本人",
                     "memo": "初回（免状交付後）",
                     "attachments": [{"id": "att-0001", "filename": "定期講習修了証_2016.jpg",
                                      "size": 412000,
                                      "uploaded_on": iso(TODAY - timedelta(days=3780)),
                                      "uploaded_by": "総務"}]},
                    {"done_on": iso(TODAY - timedelta(days=1962)), "done_by": "本人",
                     "memo": "2回目",
                     "attachments": [{"id": "att-0002", "filename": "定期講習修了証_2021.jpg",
                                      "size": 398000,
                                      "uploaded_on": iso(TODAY - timedelta(days=1955)),
                                      "uploaded_by": "総務"},
                                     {"id": "att-0003", "filename": "受講申込控_2021.pdf",
                                      "size": 88000,
                                      "uploaded_on": iso(TODAY - timedelta(days=1990)),
                                      "uploaded_by": "本人"}]},
                ]}),
                ("req-den1-menjo", {}),
                # 定期講習が切れているうえに健診も近い。1 人が複数抱える例。
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=343)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        # 前回受講日が台帳に無く、期日を計算できない例。
        {
            "id": "p-004", "code": "E-0004", "kana": "ひがし りょう", "name": "東 亮", "site": "本社", "role": "施工管理",
            "holdings": [
                ("req-den1-course", {"note": "免状は保有しているが前回の受講日が台帳に無い。"}),
                ("req-den1-menjo", {}),
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=88)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        # 同じく未確定。危険物の保安講習。
        {
            "id": "p-005", "code": "E-0005", "kana": "なかむら けんいち", "name": "中村 健一", "site": "指宿支店", "role": "電気工事",
            "holdings": [
                ("req-kikenbutsu-course", {"note": "従事開始時の受講日が不明。"}),
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=52)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        # 期限が近い人たち。
        {
            "id": "p-006", "code": "E-0006", "kana": "やました たろう", "name": "山下 太郎", "site": "本社", "role": "施工管理",
            "holdings": [
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=353)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        {
            "id": "p-007", "code": "E-0007", "kana": "さとう たつや", "name": "佐藤 龍也", "site": "出水支店", "role": "電気工事",
            "holdings": [
                ("req-den1-course", {"records": [{"done_on": iso(TODAY - timedelta(days=1804)),
                                                  "done_by": "本人", "memo": "定期講習"}]}),
                ("req-den1-menjo", {}),
            ],
        },
        {
            "id": "p-008", "code": "E-0008", "kana": "まつもと ゆうすけ", "name": "松本 裕介", "site": "鹿屋支店", "role": "施工管理",
            "holdings": [
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=338)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
                ("req-driver-license", {"fixed_due_on": iso(TODAY + timedelta(days=52))}),
            ],
        },
        {
            "id": "p-009", "code": "E-0009", "kana": "かとう ゆみ", "name": "加藤 由美", "site": "枕崎支店", "role": "事務",
            "holdings": [
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=335)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        {
            "id": "p-010", "code": "E-0010", "kana": "いまむら かずや", "name": "今村 和也", "site": "奄美支店", "role": "電気工事",
            "holdings": [
                ("req-kikenbutsu-course", {"records": [{"done_on": iso(TODAY - timedelta(days=1064)),
                                                        "done_by": "本人", "memo": "保安講習"}]}),
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=148)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        # 監理技術者として配置できる人（比較用）。
        {
            "id": "p-011", "code": "E-0011", "kana": "ありむら ちひろ", "name": "有村 千尋", "site": "本社", "role": "保守・計装",
            "holdings": [
                ("req-kanri-cert", {"fixed_due_on": iso(TODAY + timedelta(days=1189))}),
                ("req-kanri-course", {"fixed_due_on": iso(TODAY + timedelta(days=1220))}),
                ("req-shunin-menjo", {}),
                ("req-kenshin", {"records": [{"done_on": iso(TODAY - timedelta(days=52)),
                                              "done_by": "総務", "memo": "定期健診"}]}),
            ],
        },
        # 登録しただけで、まだ何も紐づいていない人。
        # 「問題なし」ではなく「資格情報なし」として扱われることを見せるため。
        {
            "id": "p-013", "code": "E-0013", "kana": "しらいし みなと",
            "name": "白石 湊", "site": "本社", "role": "電気工事",
            "holdings": [],
        },
        {
            "id": "p-014", "code": "E-0014", "kana": "くわはら あおい",
            "name": "桑原 葵", "site": "鹿屋支店", "role": "事務",
            "holdings": [],
        },
        # 有効期限のない免状しか持っていない人（灰色に数えない例）。
        {
            "id": "p-012", "code": "E-0012", "kana": "かわばた ゆう", "name": "川畑 悠", "site": "枕崎支店", "role": "電気工事",
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
                "code": spec.get("code", ""), "kana": spec.get("kana", ""),
            }
        )
        for requirement_id, kw in spec["holdings"]:
            add_holding(spec["id"], requirement_id, **kw)

    # --- 機械的に作る「問題のない人」 -------------------------------------
    #
    # 健診を全員に持たせ、期日が十分先になるよう実施日をずらす。
    # 76 名規模にしないと「手を付ける人だけ表示する」ことの価値が見えないため。

    for i in range(15, 77):
        subjects.append(person(i))
        # 期日 = 実施日 + 12 か月。90〜300 日先に散らす。
        offset = 90 + (i * 13) % 210
        done = TODAY + timedelta(days=offset) - timedelta(days=365)
        add_holding(f"p-{i:03d}", "req-kenshin",
                    records=[{"done_on": iso(done), "done_by": "総務", "memo": "定期健診"}])

    # --- 道具・機器 -------------------------------------------------------
    #
    # 道具は名前だけでは特定できないので、管理番号と型番を持たせる。
    # 「期限まであと何日か」を指定して、そこから前回実施日を逆算する。

    def add_asset(
        code: str, name: str, model: str, site: str, role: str,
        requirement_id: str, *, days_until_due: int | None = None,
        cycle_months: int = 0, fixed_due_in: int | None = None,
    ) -> None:
        subject_id = f"a-{code}"
        subjects.append({
            "id": subject_id, "name": name, "kind": "asset",
            "site": site, "role": role, "code": code, "model": model,
        })
        if fixed_due_in is not None:
            add_holding(subject_id, requirement_id,
                        fixed_due_on=iso(TODAY + timedelta(days=fixed_due_in)))
        elif days_until_due is None:
            # 前回の点検日が台帳に無い状態。期日を計算できない。
            add_holding(subject_id, requirement_id,
                        note="前回の点検日が台帳に無い。")
        else:
            last = TODAY + timedelta(days=days_until_due - round(cycle_months * 30.44))
            add_holding(subject_id, requirement_id,
                        records=[{"done_on": iso(last), "done_by": "外部委託", "memo": ""}])

    INS = "req-insulator-check"        # 絶縁用保護具（6か月）
    SPC = "req-specific-inspection"    # 特定自主検査（12か月）
    CAL = "req-instrument-cal"         # 測定機器の校正（12か月）
    VEH = "req-vehicle-inspection"     # 車検（期日指定）

    # 絶縁用保護具。周期が6か月と短く、現物の数が多い。抜けが起きやすい。
    gloves = [
        ("GLO-001", "絶縁手袋 A組", "YOTSUGI YS-101-23-01", "本社", -20),
        ("GLO-002", "絶縁手袋 B組", "YOTSUGI YS-101-23-01", "鹿屋支店", None),
        ("GLO-003", "絶縁手袋 C組", "YOTSUGI YS-101-23-01", "指宿支店", 11),
        ("GLO-004", "絶縁手袋 D組", "YOTSUGI YS-101-23-01", "出水支店", 96),
        ("GLO-005", "絶縁手袋 E組", "YOTSUGI YS-101-23-01", "枕崎支店", 120),
        ("GLO-006", "絶縁手袋 F組", "YOTSUGI YS-101-23-01", "奄美支店", 145),
        ("BOO-001", "絶縁長靴 A組", "YOTSUGI YS-201-25", "本社", 19),
        ("BOO-002", "絶縁長靴 B組", "YOTSUGI YS-201-25", "鹿屋支店", 88),
        ("BOO-003", "絶縁長靴 C組", "YOTSUGI YS-201-25", "指宿支店", 132),
        ("BOO-004", "絶縁長靴 D組", "YOTSUGI YS-201-25", "枕崎支店", 160),
        ("SHT-001", "絶縁シート 1号", "YOTSUGI YS-232-01", "本社", None),
        ("SHT-002", "絶縁シート 2号", "YOTSUGI YS-232-01", "鹿屋支店", 26),
        ("SHT-003", "絶縁シート 3号", "YOTSUGI YS-232-01", "出水支店", 104),
        ("SHT-004", "絶縁シート 4号", "YOTSUGI YS-232-01", "奄美支店", 151),
    ]
    for code, name, model, site, days in gloves:
        add_asset(code, name, model, site, "絶縁用保護具", INS,
                  days_until_due=days, cycle_months=6)

    # 高所作業車。特定自主検査は1年以内ごと。
    add_asset("AWP-01", "高所作業車 10m", "アイチ SR10A", "本社", "車両",
              SPC, days_until_due=-46, cycle_months=12)
    add_asset("AWP-02", "高所作業車 12m", "タダノ AT-121TG", "鹿屋支店", "車両",
              SPC, days_until_due=21, cycle_months=12)

    # 測定機器。校正が切れた機器で測った記録は成績書として使えない。
    meters = [
        ("INS-001", "絶縁抵抗計 No.1", "HIOKI IR4052", "本社", -35),
        ("INS-002", "絶縁抵抗計 No.2", "HIOKI IR4052", "鹿屋支店", 63),
        ("INS-003", "絶縁抵抗計 No.3", "HIOKI IR4052", "指宿支店", 148),
        ("ERT-001", "接地抵抗計 No.1", "HIOKI FT6031-50", "本社", 16),
        ("ERT-002", "接地抵抗計 No.2", "HIOKI FT6031-50", "出水支店", 201),
        ("CLA-001", "クランプメータ No.1", "HIOKI 3280-10F", "本社", 11),
        ("CLA-002", "クランプメータ No.2", "HIOKI 3280-10F", "枕崎支店", 174),
        ("DMM-001", "デジタルマルチメータ No.1", "FLUKE 117", "本社", 16),
        ("DMM-002", "デジタルマルチメータ No.2", "FLUKE 117", "奄美支店", 233),
        ("VOL-001", "検電器 No.1", "長谷川電機 HST-6", "指宿支店", None),
        ("VOL-002", "検電器 No.2", "長谷川電機 HST-6", "鹿屋支店", 189),
        ("LD-001", "レーザー距離計", "BOSCH GLM50C", "本社", 26),
    ]
    for code, name, model, site, days in meters:
        add_asset(code, name, model, site, "測定機器", CAL,
                  days_until_due=days, cycle_months=12)

    # 社用車。周期ではなく車検証に書かれた満了日で管理する。
    cars = [
        ("CAR-001", "社用車 1号", "トヨタ ハイエース", "本社", -18),
        ("CAR-002", "社用車 2号", "トヨタ ハイエース", "鹿屋支店", 24),
        ("CAR-003", "社用車 3号", "日産 NV200", "指宿支店", 77),
        ("CAR-004", "社用車 4号", "日産 NV200", "出水支店", 132),
        ("CAR-005", "社用車 5号", "スズキ エブリイ", "枕崎支店", 198),
        ("CAR-006", "社用車 6号", "スズキ エブリイ", "奄美支店", 245),
        ("CAR-007", "社用車 7号", "トヨタ ハイエース", "本社", 289),
        ("CAR-008", "社用車 8号", "日産 NV200", "鹿屋支店", 331),
    ]
    for code, name, model, site, days in cars:
        add_asset(code, name, model, site, "車両", VEH, fixed_due_in=days)

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
        # 事業所と職種は人がいなくても存在する。数え上げに任せると、
        # その拠点の最後の1人を消した時点で選択肢ごと消える。
        "site_master": SITES,
        # 道具の種別は add_asset に直接渡しているので、使われた順に拾う。
        "role_master": {
            "person": ROLES,
            "asset": list(dict.fromkeys(
                s["role"] for s in subjects if s["kind"] == "asset" and s.get("role")
            )),
        },
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
