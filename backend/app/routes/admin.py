"""사용자·접근 그룹 관리 (관리자 전용). 사건 내용은 다루지 않는다."""
import re
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db, security
from ..deps import require
from ..permissions import ROLES

router = APIRouter(prefix="/admin", tags=["admin"])
USER_ID_RE = r"^[a-z0-9][a-z0-9_.-]{2,31}$"
GROUP_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$"


class UserCreate(BaseModel):
    user_id: str = Field(pattern=USER_ID_RE)
    name: str = Field(min_length=1, max_length=50)
    role: str
    groups: list[str] = []


class UserPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=50)
    role: str | None = None
    active: bool | None = None


class GroupsIn(BaseModel):
    groups: list[str]


class GroupCreate(BaseModel):
    group_id: str = Field(pattern=GROUP_ID_RE)
    description: str = Field("", max_length=200)


def _temp_password() -> str:
    return secrets.token_urlsafe(10) + "7a"


def _check_role(role: str):
    if role not in ROLES:
        raise HTTPException(400, f"역할은 {', '.join(ROLES)} 중 하나여야 합니다.")


def _user_view(c, user_id: str) -> dict | None:
    u = c.execute("SELECT user_id, name, role, active, created_at FROM users WHERE user_id = %s", (user_id,)).fetchone()
    if not u:
        return None
    u["groups"] = [r["group_id"] for r in c.execute("SELECT group_id FROM user_groups WHERE user_id = %s ORDER BY 1", (user_id,))]
    return u


def _active_admins(c) -> int:
    return c.execute("SELECT count(*) AS n FROM users WHERE role = '관리자' AND active").fetchone()["n"]


@router.get("/users")
def list_users(user: dict = Depends(require("users.manage"))):
    with db.tx() as c:
        users = c.execute("SELECT user_id, name, role, active, created_at FROM users ORDER BY user_id").fetchall()
        groups = {}
        for r in c.execute("SELECT user_id, group_id FROM user_groups ORDER BY 2"):
            groups.setdefault(r["user_id"], []).append(r["group_id"])
    return [{**u, "groups": groups.get(u["user_id"], [])} for u in users]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, user: dict = Depends(require("users.manage"))):
    _check_role(body.role)
    temp = _temp_password()
    with db.tx() as c:
        if c.execute("SELECT 1 FROM users WHERE user_id = %s", (body.user_id,)).fetchone():
            raise HTTPException(409, "이미 존재하는 아이디입니다.")
        known = {r["group_id"] for r in c.execute("SELECT group_id FROM access_groups")}
        unknown = [g for g in body.groups if g not in known]
        if unknown:
            raise HTTPException(400, f"존재하지 않는 접근 그룹: {', '.join(unknown)}")
        c.execute("INSERT INTO users (user_id, name, role) VALUES (%s,%s,%s)", (body.user_id, body.name, body.role))
        for g in set(body.groups):
            c.execute("INSERT INTO user_groups VALUES (%s,%s)", (body.user_id, g))
        c.execute("INSERT INTO user_credentials (user_id, password_hash, must_change) VALUES (%s,%s,true)",
                  (body.user_id, security.hash_password(temp)))
        view = _user_view(c, body.user_id)
    db.audit(user["user_id"], "create_user", {"target": body.user_id, "role": body.role, "groups": body.groups})
    return {"user": view, "temporary_password": temp,
            "note": "임시 비밀번호는 지금 한 번만 표시됩니다. 첫 로그인 때 변경해야 합니다."}


@router.patch("/users/{user_id}")
def patch_user(user_id: str, body: UserPatch, user: dict = Depends(require("users.manage"))):
    if body.role is not None:
        _check_role(body.role)
    with db.tx() as c:
        target = _user_view(c, user_id)
        if not target:
            raise HTTPException(404, "사용자를 찾을 수 없습니다.")
        demoting = (body.role is not None and body.role != "관리자") or body.active is False
        if user_id == user["user_id"] and demoting:
            raise HTTPException(400, "자신의 관리자 권한·활성 상태는 바꿀 수 없습니다.")
        if target["role"] == "관리자" and target["active"] and demoting and _active_admins(c) <= 1:
            raise HTTPException(400, "마지막 관리자는 강등하거나 비활성화할 수 없습니다.")
        if body.name is not None:
            c.execute("UPDATE users SET name = %s WHERE user_id = %s", (body.name, user_id))
        if body.role is not None:
            c.execute("UPDATE users SET role = %s WHERE user_id = %s", (body.role, user_id))
        if body.active is not None:
            c.execute("UPDATE users SET active = %s WHERE user_id = %s", (body.active, user_id))
            if body.active is False:
                c.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
                c.execute("UPDATE api_tokens SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (user_id,))
        view = _user_view(c, user_id)
    db.audit(user["user_id"], "update_user", {"target": user_id, **body.model_dump(exclude_none=True)})
    return view


@router.put("/users/{user_id}/groups")
def set_groups(user_id: str, body: GroupsIn, user: dict = Depends(require("users.manage"))):
    with db.tx() as c:
        if not _user_view(c, user_id):
            raise HTTPException(404, "사용자를 찾을 수 없습니다.")
        known = {r["group_id"] for r in c.execute("SELECT group_id FROM access_groups")}
        unknown = [g for g in body.groups if g not in known]
        if unknown:
            raise HTTPException(400, f"존재하지 않는 접근 그룹: {', '.join(unknown)}")
        current = {r["group_id"] for r in c.execute("SELECT group_id FROM user_groups WHERE user_id = %s", (user_id,))}
        want = set(body.groups)
        for g in current - want:
            c.execute("DELETE FROM user_groups WHERE user_id = %s AND group_id = %s", (user_id, g))
        for g in want - current:
            c.execute("INSERT INTO user_groups VALUES (%s,%s)", (user_id, g))
        view = _user_view(c, user_id)
    db.audit(user["user_id"], "set_groups", {"target": user_id, "added": sorted(want - current), "removed": sorted(current - want)})
    return view


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: str, user: dict = Depends(require("users.manage"))):
    temp = _temp_password()
    with db.tx() as c:
        if not _user_view(c, user_id):
            raise HTTPException(404, "사용자를 찾을 수 없습니다.")
        c.execute("INSERT INTO user_credentials (user_id, password_hash, must_change) VALUES (%s,%s,true)"
                  " ON CONFLICT (user_id) DO UPDATE SET password_hash = EXCLUDED.password_hash, must_change = true, updated_at = now()",
                  (user_id, security.hash_password(temp)))
        c.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
        c.execute("UPDATE api_tokens SET revoked_at = now() WHERE user_id = %s AND revoked_at IS NULL", (user_id,))   # 계정 탈취가 의심되는 상황이므로 에이전트 토큰도 함께 폐기
    db.audit(user["user_id"], "reset_password", {"target": user_id})
    return {"user_id": user_id, "temporary_password": temp, "note": "임시 비밀번호는 지금 한 번만 표시됩니다."}


@router.get("/groups")
def list_groups(user: dict = Depends(require("groups.manage"))):
    with db.tx() as c:
        return c.execute(
            "SELECT g.group_id, g.description, (SELECT count(*) FROM user_groups ug WHERE ug.group_id = g.group_id) AS members"
            " FROM access_groups g ORDER BY 1").fetchall()


@router.post("/groups", status_code=201)
def create_group(body: GroupCreate, user: dict = Depends(require("groups.manage"))):
    with db.tx() as c:
        if c.execute("SELECT 1 FROM access_groups WHERE group_id = %s", (body.group_id,)).fetchone():
            raise HTTPException(409, "이미 존재하는 접근 그룹입니다.")
        c.execute("INSERT INTO access_groups VALUES (%s,%s)", (body.group_id, body.description))
    db.audit(user["user_id"], "create_group", {"group": body.group_id})
    return {"group_id": body.group_id, "description": body.description, "members": 0}
