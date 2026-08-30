"""台帳が扱うデータの型。

保存形式（JSON）との相互変換もここに置く。画面もこの型だけを見る。

命名について:
    Requirement  … 「何を、どの周期で満たす必要があるか」の定義（種別マスタ）
    Subject      … 「誰が／何が」その対象になるか（社員・設備）
    Holding      … Subject × Requirement の組。期限が実際に発生する単位
    Record       … 実施した事実。Holding の下にぶら下がる
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Iterable, Literal
from uuid import uuid4

from core.schedule import latest_done, next_due

__all__ = [
    "LedgerDataError",
    "SUBJECT_KIND_LABEL",
    "Category",
    "DateMode",
    "Obligation",
    "Attachment",
    "Requirement",
    "Subject",
    "Record",
    "Holding",
    "Ledger",
]

Category = Literal["qualification", "inspection"]
DateMode = Literal["cycle", "fixed", "none"]
Obligation = Literal["legal", "contract", "effort", "none"]

CATEGORY_LABEL: dict[str, str] = {
    "qualification": "資格・講習・健診",
    "inspection": "点検・校正",
}

OBLIGATION_LABEL: dict[str, str] = {
    "legal": "法令義務",
    "contract": "規格・契約",
    "effort": "努力義務",
    "none": "期限なし",
}

SUBJECT_KIND_LABEL: dict[str, str] = {
    "person": "社員",
    "asset": "道具・機器",
}

DATE_MODE_LABEL: dict[str, str] = {
    "cycle": "周期から計算",
    "fixed": "有効期限を直接入力",
    "none": "期限を持たない",
}


class LedgerDataError(ValueError):
    """保存された台帳の内容が受け付けられないことを表す。

    読み込みで気づかずに通すと、画面が壊れるのではなく判定が静かに狂う。
    たとえば date_mode が知らない値だと、期限のある資格が
    「期限なし」として扱われ、警告に出なくなる。
    落ちる方がまだ安全なので、読み込みの時点で止める。
    """


def _require(d: dict[str, Any], key: str, where: str) -> Any:
    if key not in d or d[key] in (None, ""):
        raise LedgerDataError(f"{where}: 「{key}」がありません")
    return d[key]


def _one_of(value: Any, allowed: dict[str, str], key: str, where: str) -> Any:
    """決められた区分値のどれかであることを確かめる。

    許される値そのものではなく、画面で使っている対応表を引数に取る。
    表に無い値は画面でも表示できないため、二重に管理しなくて済む。
    """
    if value not in allowed:
        raise LedgerDataError(
            f"{where}: 「{key}」に知らない値 {value!r} が入っています"
            f"（使えるのは {'・'.join(allowed)}）"
        )
    return value


def _merge(master: list[str], used: Any) -> list[str]:
    """一覧を先に、一覧に無いものを後ろに。重複と空文字は落とす。

    並べ直さないのは、一覧の順番が意味を持つため。本社を先頭に置いてある
    ものを五十音で並べ替えると、毎回スクロールして探すことになる。
    """
    out = [s for s in master if s]
    for s in used:
        if s and s not in out:
            out.append(s)
    return out


def _to_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as e:
        raise LedgerDataError(f"日付として読めません: {value!r}") from e


def _from_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


@dataclass(frozen=True)
class Requirement:
    """種別マスタ。

    周期も警告日数もここに持ち、コードには固定しない。
    実際の周期は設備や契約や社内規程で変わるため、画面から編集できる必要がある。
    """

    id: str
    name: str
    category: Category
    obligation: Obligation
    date_mode: DateMode
    cycle_months: int | None = None
    source: str = ""
    note: str = ""

    @property
    def has_deadline(self) -> bool:
        return self.date_mode != "none"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Requirement:
        where = f"種類 {d.get('id', '?')!r}"
        date_mode = _one_of(
            d.get("date_mode", "cycle"), DATE_MODE_LABEL, "date_mode", where
        )
        cycle_months = d.get("cycle_months")
        # 周期で計算すると書いてあるのに周期が無いと、期限を出せないまま
        # 「未確定」が並ぶ。原因が台帳の中身にあると気づきにくいので、ここで言う。
        if date_mode == "cycle" and not cycle_months:
            raise LedgerDataError(f"{where}: 周期で計算する設定ですが cycle_months がありません")
        return cls(
            id=_require(d, "id", where),
            name=_require(d, "name", where),
            category=_one_of(_require(d, "category", where),
                             CATEGORY_LABEL, "category", where),
            obligation=_one_of(d.get("obligation", "legal"),
                               OBLIGATION_LABEL, "obligation", where),
            date_mode=date_mode,
            cycle_months=cycle_months,
            source=d.get("source", ""),
            note=d.get("note", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "obligation": self.obligation,
            "date_mode": self.date_mode,
            "cycle_months": self.cycle_months,
            "source": self.source,
            "note": self.note,
        }


@dataclass(frozen=True)
class Subject:
    """期限の対象。社員か道具・機器。

    道具は名前では特定できない。「絶縁手袋」は何組もあるため、現物にたどり着くには
    管理番号が要る。型番は校正や修理を頼むときに要る。社員には使わない。
    """

    id: str
    name: str
    kind: Literal["person", "asset"]
    site: str = ""
    role: str = ""
    code: str = ""      # 社員は社員番号、道具は管理番号
    model: str = ""     # 道具の型番
    kana: str = ""      # 社員のふりがな
    note: str = ""      # 備考

    @property
    def search_text(self) -> str:
        """検索で引っかける対象。名称・保管場所に加え、道具は管理番号と型番でも引く。"""
        parts = [
            self.name, self.name.replace(" ", ""), self.site,
            self.code, self.model, self.kana,
        ]
        return " ".join(p for p in parts if p)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Subject:
        return cls(
            id=d["id"],
            name=d["name"],
            # 知らない値を通すと、社員でも道具でもない対象になり、
            # どちらの一覧にも出ないまま台帳に残る。
            kind=_one_of(_require(d, "kind", f"対象 {d.get('id', '?')!r}"),
                         SUBJECT_KIND_LABEL, "kind", f"対象 {d.get('id', '?')!r}"),
            site=d.get("site", ""),
            role=d.get("role", ""),
            code=d.get("code", ""),
            model=d.get("model", ""),
            kana=d.get("kana", ""),
            note=d.get("note", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "site": self.site,
            "role": self.role,
            "code": self.code,
            "model": self.model,
            "kana": self.kana,
            "note": self.note,
        }


@dataclass(frozen=True)
class Attachment:
    """記録に添えた書類。修了証や点検成績書の控え。

    ここに持つのは「何が・いつ・誰によって」登録されたかだけで、
    ファイルの中身は持たない。中身の置き場は運用の判断（保存期間・アクセス制御）に
    依るため、台帳の型には混ぜない。
    """

    id: str
    filename: str
    size: int = 0
    uploaded_on: date | None = None
    uploaded_by: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Attachment:
        return cls(
            id=d["id"],
            filename=d["filename"],
            size=d.get("size", 0),
            uploaded_on=_to_date(d.get("uploaded_on")),
            uploaded_by=d.get("uploaded_by", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "size": self.size,
            "uploaded_on": _from_date(self.uploaded_on),
            "uploaded_by": self.uploaded_by,
        }


@dataclass(frozen=True)
class Record:
    """台帳に起きた事実。追記のみで、書き換えない。

    添付はこの記録に属する。台帳全体に 1 つだけ持つと、2021年の修了証と
    2026年の修了証を区別できず、蓄積する意味がなくなるため。

    扱う事実は 2 種類ある。どちらも「いつの事実か」を done_on で持つ。

    - 実施した（受講した・点検した）: done_on だけを持つ。
      期日は前回実施日から周期で決まる。
    - 新しい有効期限を受け取った: done_on に受け取った日、expiry_on にその期限。
      免状や免許のように、期限が外から与えられるもの。

    以前は有効期限を Holding に 1 つだけ持たせ、更新のたびに上書きしていた。
    そうすると、更新した瞬間に過去の判定まで新しい期限で塗り替わり、
    「2026年8月1日時点では切れていた」という事実が台帳から消えていた。
    期限も事実として積む。

    **訂正について。** 実施日を間違えて登録した場合、正しい日付でもう一度
    記録しても直らない。最も新しい日付が採用されるため、誤って実際より
    後の日付を入れていると、そちらが残って期限が実際より先に延びる。
    そこで、訂正は supersedes に「どの記録を置き換えるか」を書いた新しい
    記録として積む。元の記録は消さない。消せば監査のときに信用されない。
    """

    done_on: date
    done_by: str = ""
    memo: str = ""
    attachments: list[Attachment] = field(default_factory=list)

    # 記録そのものを指すための識別子。訂正が「どれを置き換えたか」を
    # 指せなければ、訂正のしようがない。
    id: str = field(default_factory=lambda: uuid4().hex[:12])

    # 有効期限を受け取った記録の場合だけ入る。
    expiry_on: date | None = None

    # この記録が置き換える記録の id。訂正でなければ None。
    supersedes: str | None = None

    @property
    def is_expiry_update(self) -> bool:
        return self.expiry_on is not None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Record:
        # assert は python -O で消える。消えたら None のまま通り、
        # 期限計算の途中で分かりにくい形で落ちる。例外として明示する。
        done_on = _to_date(d.get("done_on"))
        if done_on is None:
            raise LedgerDataError("実施記録に実施日がありません")
        return cls(
            done_on=done_on,
            done_by=d.get("done_by", ""),
            memo=d.get("memo", ""),
            attachments=[Attachment.from_dict(a) for a in d.get("attachments", [])],
            # 古い保存データには id が無い。訂正は id を指すので、
            # 読み込みの時点で必ず持たせる。
            id=d.get("id") or uuid4().hex[:12],
            expiry_on=_to_date(d.get("expiry_on")),
            supersedes=d.get("supersedes") or None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "done_on": _from_date(self.done_on),
            "done_by": self.done_by,
            "memo": self.memo,
            "attachments": [a.to_dict() for a in self.attachments],
            "expiry_on": _from_date(self.expiry_on),
            "supersedes": self.supersedes,
        }


@dataclass
class Holding:
    """Subject × Requirement。期限が発生する単位。

    前回実施日は持たない。records から導出する（core.schedule.latest_done）。
    同じ事実を二箇所に置くと、片方だけ更新されたときに静かに食い違うため。
    """

    id: str
    subject_id: str
    requirement_id: str
    fixed_due_on: date | None = None
    # タプルで持つ。「追記のみ」を決まりごとで守らせるのではなく、型で守らせる。
    # リストのままだと holding.records.clear() ができてしまい、
    # 「消せない」と書いてあるのに消せる、という状態になる。
    # 丸ごと差し替える holding.records = () は __setattr__ で止める。
    records: tuple[Record, ...] = ()
    note: str = ""
    # 受講や点検の予約が取れている場合の予定日。期日の計算には使わない。
    # 「切れているが予約済み」と「切れていて何もしていない」は、対応が違うため
    # 区別できるようにする。予定を実績として扱うと超過が隠れるので、判定には混ぜない。
    planned_on: date | None = None

    def __post_init__(self) -> None:
        # 読み込みや呼び出し側はリストで渡してくるので、ここで揃える。
        if not isinstance(self.records, tuple):
            self._set_records(tuple(self.records))

    def __setattr__(self, name: str, value: Any) -> None:
        """記録の差し替えを外から行えないようにする。

        タプルにしただけでは holding.records.clear() は防げても
        holding.records = () は通ってしまい、「消せない」と書いてあるのに
        履歴を丸ごと落とせる状態が残っていた。
        足すのは add_record、直すのは correct_record を通す。
        """
        if name == "records" and "records" in self.__dict__:
            raise LedgerDataError(
                "記録は差し替えられません。"
                "追加は add_record、訂正は correct_record を使ってください。"
            )
        super().__setattr__(name, value)

    def _set_records(self, records: tuple[Record, ...]) -> None:
        object.__setattr__(self, "records", records)

    @property
    def superseded_ids(self) -> frozenset[str]:
        """訂正によって置き換えられた記録の id。

        置き換えられた記録は残すが、判定には使わない。
        画面では「訂正済み」として見せる。消すと監査で追えなくなる。
        """
        return frozenset(r.supersedes for r in self.records if r.supersedes)

    def effective_records(self, as_of: date | None = None) -> tuple[Record, ...]:
        """判定に使う記録。訂正されたものを除く。

        as_of を渡すと、その日までの事実だけを見る。
        """
        superseded = self.superseded_ids
        return tuple(
            r for r in self.records
            if r.id not in superseded and (as_of is None or r.done_on <= as_of)
        )

    @property
    def last_done_on(self) -> date | None:
        """記録全体の中で最も新しい実施日。表示にだけ使う。

        判定には使わない。判定は基準日を伴うので last_done_on_at を使うこと。
        """
        return latest_done(r.done_on for r in self.effective_records())

    def last_done_on_at(self, as_of: date) -> date | None:
        """基準日までに実施されたものだけを見た、前回実施日。"""
        return latest_done(
            (r.done_on for r in self.effective_records()), as_of=as_of
        )

    def expiry_on_at(self, as_of: date) -> date | None:
        """基準日の時点で分かっていた有効期限。

        免状を更新しても、更新前の日を基準にすれば更新前の期限が返る。
        更新は「新しい証を受け取った」という新しい事実であって、
        過去の証の期限が間違っていたわけではないため。
        """
        updates = [r for r in self.effective_records(as_of) if r.is_expiry_update]
        if updates:
            return max(updates, key=lambda r: r.done_on).expiry_on
        # 更新の記録が無ければ、最初に登録された期限。
        return self.fixed_due_on

    def due_on(self, requirement: Requirement, as_of: date) -> date | None:
        """次回期日。決められない場合は None を返し、推測で埋めない。

        as_of を必ず受け取る。既定値を持たせると、呼び出し側が「いつ時点の
        話か」を書かずに済んでしまい、未来の実施記録を過去の判定に混ぜる
        事故が起きる。
        """
        if requirement.date_mode == "none":
            return None
        if requirement.date_mode == "fixed":
            return next_due(
                last_done_on=None,
                cycle_months=None,
                fixed_due_on=self.expiry_on_at(as_of),
            )
        return next_due(
            last_done_on=self.last_done_on_at(as_of),
            cycle_months=requirement.cycle_months,
        )

    def add_record(self, record: Record) -> None:
        """記録を足す。消す手段は用意しない。

        過去を書き換えられる台帳は、監査のときに信用されない。
        間違えた場合は correct_record で訂正する。
        """
        self._set_records((*self.records, record))

    def correct_record(self, target_id: str, corrected: Record) -> None:
        """既にある記録を、新しい記録で置き換える。

        元の記録は残したまま、「これはもう使わない」と印を付ける形にする。
        正しい日付をただ追記する方式では訂正にならない。誤って実際より後の
        日付を入れていた場合、そちらが最新として残り、期限が実際より
        先へ延びたままになる。
        """
        if not any(r.id == target_id for r in self.records):
            raise LedgerDataError("訂正しようとした記録が見つかりません")
        if target_id in self.superseded_ids:
            raise LedgerDataError("この記録はすでに訂正されています")
        self.add_record(replace(corrected, supersedes=target_id))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Holding:
        return cls(
            id=d["id"],
            subject_id=d["subject_id"],
            requirement_id=d["requirement_id"],
            fixed_due_on=_to_date(d.get("fixed_due_on")),
            records=_checked_records(
                [Record.from_dict(r) for r in d.get("records", [])],
                where=f"保有 {d.get('id', '?')!r}",
            ),
            note=d.get("note", ""),
            planned_on=_to_date(d.get("planned_on")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject_id": self.subject_id,
            "requirement_id": self.requirement_id,
            "fixed_due_on": _from_date(self.fixed_due_on),
            "records": [r.to_dict() for r in self.records],
            "note": self.note,
            "planned_on": _from_date(self.planned_on),
        }


def _checked_records(records: list[Record], *, where: str) -> tuple[Record, ...]:
    """記録どうしの整合を確かめる。

    訂正は記録の id を指すので、id が重複していたり、指した先が
    存在しなかったりすると、どれを置き換えたのか決まらなくなる。
    """
    ids = [r.id for r in records]
    if len(ids) != len(set(ids)):
        raise LedgerDataError(f"{where}: 記録の id が重複しています")

    known = set(ids)
    for r in records:
        if r.supersedes and r.supersedes not in known:
            raise LedgerDataError(
                f"{where}: 訂正の対象 {r.supersedes!r} にあたる記録がありません"
            )
        if r.supersedes == r.id:
            raise LedgerDataError(f"{where}: 記録が自分自身を訂正しています")

    targets = [r.supersedes for r in records if r.supersedes]
    if len(targets) != len(set(targets)):
        raise LedgerDataError(f"{where}: 同じ記録が二重に訂正されています")

    return tuple(records)


def _checked_holdings(holdings: list[Holding]) -> list[Holding]:
    """保有どうしの整合を確かめる。

    同じ対象に同じ種類が 2 件あると、判定が「どちらか片方」になる。
    危ない方が捨てられると、期限切れが表示されないまま通る。
    画面からは作れないが、壊れた保存データや将来の移行で起こりうるので、
    読み込みの時点で止める。
    """
    seen: dict[tuple[str, str], str] = {}
    for h in holdings:
        key = (h.subject_id, h.requirement_id)
        if key in seen:
            raise LedgerDataError(
                f"同じ対象に同じ種類が 2 件あります"
                f"（対象={h.subject_id} 種類={h.requirement_id} "
                f"保有={seen[key]} と {h.id}）"
            )
        seen[key] = h.id

    ids = [h.id for h in holdings]
    if len(ids) != len(set(ids)):
        raise LedgerDataError("保有の id が重複しています")
    return holdings


@dataclass
class Ledger:
    """台帳全体。"""

    requirements: list[Requirement] = field(default_factory=list)
    subjects: list[Subject] = field(default_factory=list)
    holdings: list[Holding] = field(default_factory=list)
    soon_days: int = 30
    upcoming_days: int = 60

    # 拠点と職種は、登録済みの社員から数え上げるだけでは足りない。
    # 数え上げだけだと、その拠点の最後の1人を消した時点で拠点そのものが
    # 選択肢から消え、次の人を登録できなくなる。事業所は人がいなくても
    # 存在するものなので、台帳が持つ一覧として別に持つ。
    site_master: list[str] = field(default_factory=list)
    # 人の職種と道具の種別は別物なので、一つの一覧に混ぜない。
    # 混ぜると、社員の登録画面の職種欄に「絶縁用保護具」が出る。
    role_master: dict[str, list[str]] = field(default_factory=dict)

    def set_soon_days(self, days: int) -> None:
        """『期限間近』とする日数を変える。

        『予告』の日数を追い越すと Thresholds が例外を出すため、ここで一緒に
        引き上げる。この決まりを呼び出す側に持たせると、呼ぶ場所が増えたときに
        揃え忘れて落ちる。不変条件はデータを持っている側で守る。
        """
        if days < 1:
            raise ValueError("『期限間近』とする日数は 1 日以上で指定してください")
        self.soon_days = days
        self.upcoming_days = max(self.upcoming_days, days)

    def requirement(self, requirement_id: str) -> Requirement | None:
        return next((r for r in self.requirements if r.id == requirement_id), None)

    def subject(self, subject_id: str) -> Subject | None:
        return next((s for s in self.subjects if s.id == subject_id), None)

    def holdings_of(self, subject_id: str) -> list[Holding]:
        return [h for h in self.holdings if h.subject_id == subject_id]

    @property
    def sites(self) -> list[str]:
        """選べる拠点。一覧に載っているものと、実際に使われているものの両方。

        一覧だけにすると、一覧に無い拠点の社員が編集画面で別の拠点に
        すり替わる。実際に使われている値は必ず選べるようにする。
        """
        return _merge(self.site_master, (s.site for s in self.subjects))

    def roles(self, kind: str) -> list[str]:
        """選べる職種（道具の場合は種別）。"""
        return _merge(
            self.role_master.get(kind, []),
            (s.role for s in self.subjects if s.kind == kind),
        )

    def add_site(self, name: str) -> None:
        """拠点を一覧に加える。人がいなくても残る。"""
        name = name.strip()
        if not name:
            raise LedgerDataError("拠点の名前を入力してください")
        if name not in self.site_master:
            self.site_master.append(name)

    def add_role(self, name: str, kind: str) -> None:
        name = name.strip()
        if not name:
            raise LedgerDataError("職種の名前を入力してください")
        names = self.role_master.setdefault(kind, [])
        if name not in names:
            names.append(name)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Ledger:
        return cls(
            requirements=[Requirement.from_dict(x) for x in d.get("requirements", [])],
            subjects=[Subject.from_dict(x) for x in d.get("subjects", [])],
            holdings=_checked_holdings(
                [Holding.from_dict(x) for x in d.get("holdings", [])]
            ),
            soon_days=d.get("soon_days", 30),
            upcoming_days=d.get("upcoming_days", 60),
            site_master=list(d.get("site_master", [])),
            role_master={k: list(v) for k, v in d.get("role_master", {}).items()},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirements": [r.to_dict() for r in self.requirements],
            "subjects": [s.to_dict() for s in self.subjects],
            "holdings": [h.to_dict() for h in self.holdings],
            "soon_days": self.soon_days,
            "upcoming_days": self.upcoming_days,
            "site_master": self.site_master,
            "role_master": self.role_master,
        }
