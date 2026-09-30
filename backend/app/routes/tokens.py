"""MCP 접속 토큰(개인용) 발급·목록·폐기.

- 토큰 원문은 발급 응답에서 한 번만 돌려주고 DB에는 SHA-256 해시만 저장한다.
- 토큰은 발급자의 권한(접근 그룹)으로만 동작하며, 만료·폐기·계정 비활성화 시 즉시 무효가 된다.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..deps import require

router = APIRouter(tags=["tokens"])
MAX_ACTIVE = 5
DEFAULT_DAYS = 30
MAX_DAYS = 90
PREFIX = "lawca_"


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    days: int = Field(DEFAULT_DAYS, ge=1, le=MAX_DAYS)


COLS = "token_id, name, prefix, created_at, expires_at, last_used_at, revoked_at"


@router.get("/tokens")
def list_tokens(user: dict = Depends(require("mcp.token"))):
    with db.tx() as c:
        return c.execute(f"SELECT {COLS} FROM api_tokens WHERE user_id = %s ORDER BY token_id DESC LIMIT 50", (user["user_id"],)).fetchall()


@router.post("/tokens", status_code=201)
def create_token(body: TokenCreate, user: dict = Depends(require("mcp.token"))):
    raw = PREFIX + secrets.token_urlsafe(32)
    th = hashlib.sha256(raw.encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(days=body.days)
    with db.tx() as c:
        active = c.execute("SELECT count(*) AS n FROM api_tokens WHERE user_id = %s AND revoked_at IS NULL AND expires_at > now()",
                           (user["user_id"],)).fetchone()["n"]
        if active >= MAX_ACTIVE:
            raise HTTPException(409, f"사용 중인 토큰은 최대 {MAX_ACTIVE}개입니다. 쓰지 않는 토큰을 먼저 폐기해 주세요.")
        row = c.execute(f"INSERT INTO api_tokens (user_id, name, token_hash, prefix, expires_at) VALUES (%s,%s,%s,%s,%s) RETURNING {COLS}",
                        (user["user_id"], body.name.strip(), th, raw[:len(PREFIX) + 4], expires)).fetchone()
    db.audit(user["user_id"], "create_token", {"token_id": row["token_id"], "name": body.name.strip(), "days": body.days})
    return {**row, "token": raw, "note": "토큰은 지금 한 번만 표시됩니다. 안전한 곳에 보관하세요."}


@router.delete("/tokens/{token_id}")
def revoke_token(token_id: int, user: dict = Depends(require("mcp.token"))):
    with db.tx() as c:
        cur = c.execute("UPDATE api_tokens SET revoked_at = now() WHERE token_id = %s AND user_id = %s AND revoked_at IS NULL",
                        (token_id, user["user_id"]))
        if cur.rowcount == 0:
            raise HTTPException(404, "토큰을 찾을 수 없거나 이미 폐기되었습니다.")
    db.audit(user["user_id"], "revoke_token", {"token_id": token_id})
    return {"revoked": token_id}
