from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .. import config, db, security
from ..deps import current_user, require
from ..permissions import PERMISSIONS

router = APIRouter(tags=["auth"])
GENERIC_LOGIN_ERROR = "아이디 또는 비밀번호가 올바르지 않습니다."


class LoginIn(BaseModel):
    user_id: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


def _me(c, user: dict) -> dict:
    groups = [r["group_id"] for r in c.execute("SELECT group_id FROM user_groups WHERE user_id = %s ORDER BY 1", (user["user_id"],))]
    return {"user_id": user["user_id"], "name": user["name"], "role": user["role"], "groups": groups,
            "permissions": sorted(PERMISSIONS[user["role"]]), "must_change_password": user["must_change"]}


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    ip = request.client.host if request.client else None
    ua = (request.headers.get("user-agent") or "")[:200]
    outcome, payload = None, None
    with db.tx() as c:
        # 마지막 성공 이후의 실패 횟수가 임계치를 넘으면 잠금(성공하면 초기화된다)
        fails = c.execute(
            "SELECT count(*) AS n FROM login_attempts WHERE user_id = %s AND NOT ok"
            " AND ts > now() - make_interval(mins => %s)"
            " AND ts > coalesce((SELECT max(ts) FROM login_attempts WHERE user_id = %s AND ok), '-infinity')",
            (body.user_id, config.LOGIN_LOCK_MINUTES, body.user_id)).fetchone()["n"]
        if fails >= config.LOGIN_MAX_FAILURES:
            outcome = "locked"
        else:
            row = c.execute(
                "SELECT u.user_id, u.name, u.role, u.active, k.password_hash, k.must_change"
                " FROM users u LEFT JOIN user_credentials k USING (user_id) WHERE u.user_id = %s", (body.user_id,)).fetchone()
            ok = security.verify_password(body.password, row["password_hash"] if row else None) and bool(row and row["active"])
            c.execute("INSERT INTO login_attempts (user_id, ip, ok) VALUES (%s,%s,%s)", (body.user_id[:64], ip, ok))
            if not ok:
                outcome = "bad"
            else:
                token, th = security.new_token()
                csrf = security.new_csrf()
                c.execute("INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at, ip, user_agent) VALUES (%s,%s,%s,%s,%s,%s)",
                          (th, row["user_id"], csrf, security.session_expiry(), ip, ua))
                # 오래된 만료 세션 정리
                c.execute("DELETE FROM sessions WHERE expires_at < now()")
                user = {"user_id": row["user_id"], "name": row["name"], "role": row["role"], "must_change": row["must_change"] or False}
                payload = {"user": _me(c, user), "csrf_token": csrf}
                outcome = ("ok", token)
    # 예외는 트랜잭션 밖에서 던진다(시도 기록이 롤백되지 않도록)
    if outcome == "locked":
        db.audit(body.user_id[:64], "login_locked", {"ip": ip})
        raise HTTPException(429, f"로그인 실패가 반복되어 {config.LOGIN_LOCK_MINUTES}분간 잠겼습니다.")
    if outcome == "bad":
        db.audit(body.user_id[:64], "login_failed", {"ip": ip})
        raise HTTPException(401, GENERIC_LOGIN_ERROR)
    _, token = outcome
    response.set_cookie(config.COOKIE_NAME, token, httponly=True, samesite="lax", secure=config.COOKIE_SECURE,
                        path="/", max_age=config.SESSION_ABSOLUTE_HOURS * 3600)
    db.audit(body.user_id, "login", {"ip": ip})
    return payload


@router.post("/auth/logout")
def logout(request: Request, response: Response, user: dict = Depends(require(allow_must_change=True))):
    with db.tx() as c:
        c.execute("DELETE FROM sessions WHERE token_hash = %s", (security.token_hash(request.cookies.get(config.COOKIE_NAME, "")),))
    response.delete_cookie(config.COOKIE_NAME, path="/")
    db.audit(user["user_id"], "logout")
    return {"ok": True}


@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    """새로고침 후에도 화면이 CSRF 토큰을 다시 받을 수 있게 함께 돌려준다(같은 오리진 정책상 다른 사이트는 읽을 수 없다)."""
    with db.tx() as c:
        return {**_me(c, user), "csrf_token": user["csrf"]}


@router.post("/auth/change-password")
def change_password(body: ChangePasswordIn, request: Request, user: dict = Depends(require(allow_must_change=True))):
    problem = security.password_problem(body.new_password, user["user_id"])
    if problem:
        raise HTTPException(400, problem)
    bad = False
    with db.tx() as c:
        row = c.execute("SELECT password_hash FROM user_credentials WHERE user_id = %s", (user["user_id"],)).fetchone()
        if not security.verify_password(body.current_password, row["password_hash"] if row else None):
            bad = True
        else:
            c.execute("UPDATE user_credentials SET password_hash = %s, must_change = false, updated_at = now() WHERE user_id = %s",
                      (security.hash_password(body.new_password), user["user_id"]))
            # 다른 기기의 세션은 모두 종료(현재 세션은 유지)
            c.execute("DELETE FROM sessions WHERE user_id = %s AND token_hash <> %s",
                      (user["user_id"], security.token_hash(request.cookies.get(config.COOKIE_NAME, ""))))
    if bad:
        raise HTTPException(400, "현재 비밀번호가 올바르지 않습니다.")
    db.audit(user["user_id"], "change_password")
    return {"ok": True}
