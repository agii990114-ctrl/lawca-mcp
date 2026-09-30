"""lawca 백엔드 API.  실행: uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

운영 시: https 리버스 프록시 뒤에서 LAWCA_COOKIE_SECURE=1, 프록시에 요청 본문 상한(client_max_body_size)을 함께 설정한다.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .ingest import storage
from .routes import admin, auth, documents, matters, tokens, uploads

log = logging.getLogger("lawca.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.ensure_dirs()
    db.open_pool()
    yield
    db.close_pool()


app = FastAPI(title="lawca", lifespan=lifespan,
              docs_url="/api/docs" if config.ENV.get("LAWCA_DOCS") == "1" else None, redoc_url=None, openapi_url=None
              if config.ENV.get("LAWCA_DOCS") != "1" else "/api/openapi.json")


@app.middleware("http")
async def guard(request: Request, call_next):
    # 요청 본문 크기 사전 차단(Content-Length가 있는 경우). 프록시의 상한이 최종 방어선이다.
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > (config.MAX_REQUEST_MB + 64) * 1024 * 1024:
        return JSONResponse({"detail": f"요청이 너무 큽니다(최대 {config.MAX_REQUEST_MB}MB). 나누어 올려 주세요."}, status_code=413)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "서버 내부 오류가 발생했습니다."}, status_code=500)


@app.get("/api/health")
def health():
    return {"status": "ok"}


for r in (auth.router, admin.router, matters.router, documents.router, uploads.router, tokens.router):
    app.include_router(r, prefix="/api")

# 프론트엔드 빌드 결과가 있으면 같은 오리진으로 서빙한다(쿠키 인증·CSRF를 단순하게 유지)
_dist = config.ROOT / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
