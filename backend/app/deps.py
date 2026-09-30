"""FastAPI 의존성: 세션 확인, 권한 확인, CSRF 확인."""
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request

from . import config, db
from .permissions import can
from .security import token_hash

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def current_user(request: Request) -> dict:
    token = request.cookies.get(config.COOKIE_NAME)
    if not token:
        raise HTTPException(401, "로그인이 필요합니다.")
    now = datetime.now(timezone.utc)
    stale = False
    with db.tx() as c:
        row = c.execute(
            "SELECT s.token_hash, s.csrf_token, s.expires_at, s.last_seen, u.user_id, u.name, u.role, u.active"
            " FROM sessions s JOIN users u USING (user_id) WHERE s.token_hash = %s", (token_hash(token),)).fetchone()
        if (row is None or not row["active"] or row["expires_at"] <= now
                or row["last_seen"] < now - timedelta(minutes=config.SESSION_IDLE_MINUTES)):
            stale = True
            if row is not None:   # 예외는 트랜잭션 밖에서 던진다(안에서 던지면 이 DELETE가 롤백된다)
                c.execute("DELETE FROM sessions WHERE token_hash = %s", (row["token_hash"],))
        else:
            if now - row["last_seen"] > timedelta(seconds=60):
                c.execute("UPDATE sessions SET last_seen = %s WHERE token_hash = %s", (now, row["token_hash"]))
            must_change = c.execute("SELECT must_change FROM user_credentials WHERE user_id = %s", (row["user_id"],)).fetchone()
    if stale:
        raise HTTPException(401, "세션이 만료되었습니다. 다시 로그인해 주세요.")
    return {"user_id": row["user_id"], "name": row["name"], "role": row["role"], "csrf": row["csrf_token"],
            "must_change": bool(must_change and must_change["must_change"])}


def _check_csrf(request: Request, user: dict):
    if request.method in UNSAFE:
        sent = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(sent, user["csrf"]):
            raise HTTPException(403, "CSRF 검증에 실패했습니다.")


def require(permission: str | None = None, *, allow_must_change: bool = False):
    """로그인 + (선택) 권한 + 변경 요청이면 CSRF 토큰까지 확인하는 의존성을 만든다."""
    def dep(request: Request, user: dict = Depends(current_user)) -> dict:
        _check_csrf(request, user)
        if user["must_change"] and not allow_must_change:
            raise HTTPException(403, "비밀번호를 먼저 변경해야 합니다.")
        if permission and not can(user["role"], permission):
            raise HTTPException(403, "이 작업을 수행할 권한이 없습니다.")
        return user
    return dep
