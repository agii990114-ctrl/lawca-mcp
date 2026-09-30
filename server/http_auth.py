"""MCP HTTP 서버용 인증 미들웨어(순수 ASGI).

- Authorization: Bearer <토큰> 을 요청마다 검증한다. 유효한 토큰이면 발급자의 신원을 요청 컨텍스트에 넣는다.
- 무효(없음·형식 오류·폐기·만료·비활성 사용자)는 모두 같은 401, 허용되지 않은 역할은 403.
- 사용자별 호출 속도를 제한한다(토큰 유출 시 대량 수집 방지).
"""
import json
import logging
import time
from collections import defaultdict, deque

import anyio.to_thread

import db

log = logging.getLogger("lawca.mcp")
# 웹 백엔드의 권한표(backend/app/permissions.py)에서 mcp.token 을 가진 역할과 같아야 한다(백엔드 테스트가 일치를 검증한다).
MCP_ROLES = {"변호사"}


class TokenAuthMiddleware:
    def __init__(self, app, *, per_minute: int = 120):
        self.app = app
        self.per_minute = per_minute
        self._hits: dict[str, deque] = defaultdict(deque)

    async def _send_json(self, send, status: int, body: dict, extra_headers=()):
        payload = json.dumps(body, ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(payload)).encode()),
                                (b"cache-control", b"no-store"), *extra_headers]})
        await send({"type": "http.response.body", "body": payload})

    def _limited(self, user_id: str) -> bool:
        now = time.monotonic()
        q = self._hits[user_id]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return True
        q.append(now)
        return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":                      # lifespan 등은 그대로 통과
            return await self.app(scope, receive, send)
        if scope["path"] == "/healthz":
            return await self._send_json(send, 200, {"status": "ok"})
        auth = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        scheme, _, token = auth.partition(" ")
        challenge = [(b"www-authenticate", b'Bearer realm="lawca"')]
        if scheme.lower() != "bearer" or not token.strip():
            return await self._send_json(send, 401, {"error": "invalid_token", "message": "인증 토큰이 필요합니다."}, challenge)
        try:
            row = await anyio.to_thread.run_sync(db.verify_token, token.strip())
        except Exception:                                # DB 장애 등: 인증 실패로 취급(fail closed)하되 사유는 로그에만 남긴다
            log.exception("token verification failed")
            return await self._send_json(send, 503, {"error": "unavailable", "message": "인증 서비스를 사용할 수 없습니다."})
        if not row:
            return await self._send_json(send, 401, {"error": "invalid_token", "message": "토큰이 유효하지 않거나 만료·폐기되었습니다."}, challenge)
        if row["role"] not in MCP_ROLES:
            return await self._send_json(send, 403, {"error": "forbidden", "message": "이 계정의 역할로는 MCP를 사용할 수 없습니다."})
        if self._limited(row["user_id"]):
            return await self._send_json(send, 429, {"error": "rate_limited", "message": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."},
                                         [(b"retry-after", b"30")])
        try:
            await anyio.to_thread.run_sync(db.touch_token, row["token_id"])
        except Exception:
            log.warning("touch_token failed", exc_info=True)            # 사용 시각 기록 실패는 호출을 막지 않는다
        ctx = db.bind_request(row["user_id"], row["token_id"])
        try:
            await self.app(scope, receive, send)
        finally:
            db.reset_request(ctx)
