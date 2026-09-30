import re

from fastapi import APIRouter, Depends, HTTPException
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, Field

from .. import db
from ..deps import require

router = APIRouter(tags=["matters"])
CASE_NO_RE = r"^\d{4}(가합|가단|가소|나|다|라|마|머|드단|드합|르|카합|카단|카기|고단|고합|고정|구합|구단|누)\d{2,7}$"
RESULTS = ("승소", "패소", "일부승소", "진행중")

COLUMNS = ("m.matter_id, m.case_no, m.case_type, m.court, m.plaintiff, m.defendant, m.our_side, m.attorney_name, m.result,"
           " m.access_group, (SELECT count(*) FROM documents d WHERE d.matter_id = m.matter_id) AS doc_count")


class MatterCreate(BaseModel):
    case_no: str = Field(pattern=CASE_NO_RE)
    case_type: str = Field(min_length=1, max_length=100)
    court: str | None = Field(None, max_length=100)
    plaintiff: str = Field(min_length=1, max_length=100)
    defendant: str = Field(min_length=1, max_length=100)
    our_side: str
    attorney_name: str | None = Field(None, max_length=50)
    access_group: str
    result: str = "진행중"


class MatterPatch(BaseModel):
    court: str | None = Field(None, max_length=100)
    attorney_name: str | None = Field(None, max_length=50)
    result: str | None = None


def _norm_case_no(s: str) -> str:
    return re.sub(r"\s+", "", s)


@router.get("/matters")
def list_matters(q: str | None = None, case_type: str | None = None, access_group: str | None = None,
                 limit: int = 50, offset: int = 0, user: dict = Depends(require("matters.read"))):
    limit = max(1, min(limit, 200))
    where, params = [], []
    if q:
        where.append("(m.case_no ILIKE %s OR m.plaintiff ILIKE %s OR m.defendant ILIKE %s OR m.case_type ILIKE %s)")
        params += [f"%{q}%"] * 4
    if case_type:
        where.append("m.case_type ILIKE %s"); params.append(f"%{case_type}%")
    if access_group:
        where.append("m.access_group = %s"); params.append(access_group)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    with db.tx(user["user_id"]) as c:
        total = c.execute(f"SELECT count(*) AS n FROM matters m{w}", params).fetchone()["n"]
        rows = c.execute(f"SELECT {COLUMNS} FROM matters m{w} ORDER BY m.case_no LIMIT %s OFFSET %s", (*params, limit, offset)).fetchall()
    return {"total": total, "items": rows}


@router.get("/matters/{matter_id}")
def get_matter(matter_id: str, user: dict = Depends(require("matters.read"))):
    with db.tx(user["user_id"]) as c:
        row = c.execute(f"SELECT {COLUMNS} FROM matters m WHERE m.matter_id = %s", (matter_id,)).fetchone()
    if not row:
        raise HTTPException(404, "사건을 찾을 수 없습니다.")   # 권한 밖 사건과 없는 사건을 구분하지 않는다
    return row


@router.post("/matters", status_code=201)
def create_matter(body: MatterCreate, user: dict = Depends(require("matters.create"))):
    if body.our_side not in ("원고", "피고"):
        raise HTTPException(400, "our_side는 원고 또는 피고여야 합니다.")
    if body.result not in RESULTS:
        raise HTTPException(400, f"result는 {', '.join(RESULTS)} 중 하나여야 합니다.")
    case_no = _norm_case_no(body.case_no)
    with db.tx(user["user_id"]) as c:
        mine = {r["group_id"] for r in c.execute("SELECT app_visible_groups() AS group_id")}
        if body.access_group not in mine:
            raise HTTPException(403, "소속되지 않은 접근 그룹에는 사건을 등록할 수 없습니다.")
        mid = "MX" + str(c.execute("SELECT nextval('matter_id_seq') AS n").fetchone()["n"]).zfill(6)
        try:
            c.execute("INSERT INTO matters (matter_id, case_no, case_type, court, plaintiff, defendant, our_side, attorney_name, result, access_group)"
                      " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                      (mid, case_no, body.case_type, body.court, body.plaintiff, body.defendant, body.our_side,
                       body.attorney_name, body.result, body.access_group))
        except UniqueViolation:
            # 사건번호는 전체에서 유일하다. 접근 권한 밖 사건과 겹쳐도 같은 응답이라 존재 여부가 드러나는 한계가 있다.
            raise HTTPException(409, "이미 등록된 사건번호입니다.")
        row = c.execute(f"SELECT {COLUMNS} FROM matters m WHERE m.matter_id = %s", (mid,)).fetchone()
    db.audit(user["user_id"], "create_matter", {"matter_id": mid, "access_group": body.access_group})
    return row


@router.patch("/matters/{matter_id}")
def patch_matter(matter_id: str, body: MatterPatch, user: dict = Depends(require("matters.create"))):
    if body.result is not None and body.result not in RESULTS:
        raise HTTPException(400, f"result는 {', '.join(RESULTS)} 중 하나여야 합니다.")
    sets, params = [], []
    for k, v in body.model_dump(exclude_none=True).items():
        sets.append(f"{k} = %s"); params.append(v)
    if not sets:
        raise HTTPException(400, "변경할 내용이 없습니다.")
    with db.tx(user["user_id"]) as c:
        cur = c.execute(f"UPDATE matters SET {', '.join(sets)} WHERE matter_id = %s", (*params, matter_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "사건을 찾을 수 없습니다.")
        row = c.execute(f"SELECT {COLUMNS} FROM matters m WHERE m.matter_id = %s", (matter_id,)).fetchone()
    db.audit(user["user_id"], "update_matter", {"matter_id": matter_id, **body.model_dump(exclude_none=True)})
    return row
