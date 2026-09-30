"""비밀번호 해시(표준 라이브러리 scrypt), 세션, 로그인 시도 제한."""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from . import config

_N, _R, _P = 2 ** 14, 8, 1
_DUMMY_HASH = None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32, maxmem=64 * 1024 * 1024)
    b = lambda x: base64.b64encode(x).decode()
    return f"scrypt${_N}${_R}${_P}${b(salt)}${b(h)}"


def verify_password(password: str, stored: str | None) -> bool:
    """저장된 해시가 없어도(사용자 없음) 같은 비용을 들여 응답 시간으로 사용자 존재를 추측하지 못하게 한다."""
    global _DUMMY_HASH
    if not stored:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = hash_password(secrets.token_urlsafe(12))
        stored = _DUMMY_HASH
        dummy = True
    else:
        dummy = False
    try:
        _, n, r, p, salt, h = stored.split("$")
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              dklen=32, maxmem=64 * 1024 * 1024)
        ok = hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False
    return ok and not dummy


def password_problem(password: str, user_id: str = "") -> str | None:
    if len(password) < config.MIN_PASSWORD_LEN:
        return f"비밀번호는 {config.MIN_PASSWORD_LEN}자 이상이어야 합니다."
    if user_id and user_id.lower() in password.lower():
        return "비밀번호에 아이디를 포함할 수 없습니다."
    if len(set(password)) < 5:
        return "너무 단순한 비밀번호입니다."
    classes = sum(any(f(c) for c in password) for f in (str.islower, str.isupper, str.isdigit, lambda c: not c.isalnum()))
    if classes < 2:
        return "영문 대소문자·숫자·기호 중 2종류 이상을 섞어 주세요."
    return None


def new_token() -> tuple[str, str]:
    """(쿠키에 담을 토큰, DB에 저장할 해시)"""
    t = secrets.token_urlsafe(32)
    return t, token_hash(t)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_csrf() -> str:
    return secrets.token_urlsafe(24)


def session_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=config.SESSION_ABSOLUTE_HOURS)
