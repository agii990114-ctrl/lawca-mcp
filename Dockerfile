# syntax=docker/dockerfile:1.7
# lawca 앱 이미지: API·워커·마이그레이션·모델 준비가 이 이미지 하나를 공유하고 명령만 다르다.

# ---- 1) 화면 빌드
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- 2) 문서 변환·OCR 도구(kordoc)
FROM node:22-slim AS tools
WORKDIR /tools
COPY backend/tools/package.json backend/tools/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

# ---- 3) 실행 이미지
FROM python:3.12-slim-bookworm AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# kordoc 실행용 node 런타임만 가져온다
COPY --from=tools /usr/local/bin/node /usr/local/bin/node
# 비root 사용자. /data(업로드 원본)와 ~/.cache(모델)는 볼륨으로 마운트되므로 미리 소유권을 맞춰 둔다.
RUN groupadd -g 10001 app && useradd -u 10001 -g app -m -d /home/app app \
 && mkdir -p /data /home/app/.cache && chown -R app:app /data /home/app \
 && node --version
WORKDIR /srv
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY backend/__init__.py backend/__init__.py
COPY backend/app backend/app
COPY backend/tools/package.json backend/tools/package.json
COPY --from=tools /tools/node_modules backend/tools/node_modules
COPY db/migrate.py db/migrate.py
COPY db/migrations db/migrations
COPY --from=web /web/dist frontend/dist

ENV HOME=/home/app LAWCA_STORAGE_DIR=/data FASTEMBED_CACHE_PATH=/home/app/.cache/fastembed
USER app
EXPOSE 8000
# 프록시(Caddy) 뒤에서만 접근되므로 X-Forwarded-* 를 신뢰한다(내부 네트워크에는 프록시만 붙어 있다)
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
