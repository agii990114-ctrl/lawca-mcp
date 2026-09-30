# lawca-mcp

법무법인 내부 데이터(사건 DB, 서면)를 MCP 서버로 노출해, AI 에이전트가 **유사 서면을 검색·조회하고 준비서면 초안을 작성**하도록 지원하는 시스템의 프로토타입.

> **모든 데이터는 가상의 더미입니다.** 사건·당사자·연락처·계좌 등은 전부 지어낸 값이며 실제 인물·사건과 무관합니다. 주민등록번호 형식 값 등은 마스킹 테스트용 가짜입니다.
> 결과물은 변호사가 검토하는 초안이며, 법률 자문이 아닙니다.

기획 배경과 설계 결정은 [기획서_v2.md](기획서_v2.md) 참고.

## 구성

| 경로 | 내용 |
|---|---|
| `server/` | MCP 서버(Python). 도구: `list_matters`, `search_documents`, `get_document`. 하이브리드 검색, 서버 측 마스킹, 감사 로그 |
| `backend/` | 웹 API(FastAPI): 로그인·역할(관리자/변호사/사무원)·접근 그룹 관리, 사건·문서, **문서 업로드(개별 파일·ZIP)와 인제스트 워커**(파싱·OCR → 청킹 → 로컬 임베딩 → 저장) |
| `frontend/` | 웹 화면(React + TypeScript + Vite): 로그인, 업로드, 처리 현황·검토, 사건, 문서, 관리 |
| `db/` | PostgreSQL + pgvector 스키마(행 수준 보안 포함), 더미 적재 스크립트, 검증 스크립트 |
| `dummy-data/` | 가상 사건 32건·문서 64건(준비서면 중심, 쟁점 태그·접근 그룹 포함) |
| `eval/` | Claude를 MCP 클라이언트로 붙인 준비서면 초안 작성 시험(정상·인젝션·근거 없음·권한) |
| `parse-test/` | 사전 실험: kordoc 파싱·OCR, 임베딩·검색·라우팅·리랭커 비교 |
| `docker-compose.yml` | PostgreSQL 17 + pgvector 컨테이너 |

## 빠른 시작

```bash
cp .env.example .env               # 개발용 비밀번호로 수정
docker compose up -d db            # DB만 (127.0.0.1:5433, 개발용 override). 전체 스택은 아래 '컨테이너로 실행'
python -m venv server/.venv && server/.venv/Scripts/python -m pip install -r server/requirements.txt
server/.venv/Scripts/python db/load_dummy.py     # 더미 적재 + 로컬 임베딩(약 7분, 최초 1회 모델 다운로드)
server/.venv/Scripts/python db/verify.py         # 스키마·RLS·권한 검증
server/.venv/Scripts/python server/tests/e2e.py  # MCP 종단 테스트
```

### 웹 화면·업로드 (백엔드 + 프런트엔드)

```bash
python -m venv backend/.venv && backend/.venv/Scripts/python -m pip install -r backend/requirements.txt
(cd backend/tools && npm install)                      # 워커가 쓰는 kordoc(문서 변환·OCR)
(cd frontend && npm install && npm run build)          # frontend/dist 를 API가 같은 오리진으로 서빙
backend/.venv/Scripts/python db/migrate.py             # 백엔드용 스키마·역할 (.env 의 LAWCA_APP/WORKER 비밀번호 필요)
backend/.venv/Scripts/python -m backend.app.manage create-admin --user-id admin --name "관리자"   # 운영: 관리자 생성(비밀번호는 프롬프트)
# 개발용 계정(무작위 비밀번호는 .dev-credentials.txt, git 제외): python -m backend.app.manage seed-dev
backend/.venv/Scripts/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000   # API + 화면
backend/.venv/Scripts/python -m backend.app.worker     # 인제스트 워커(별도 프로세스, 여러 개 가능)
backend/.venv/Scripts/python -m pytest                 # 백엔드 테스트 46+개
```

### 컨테이너로 실행 (운영 권장)

| 컨테이너 | 역할 | 외부 통신 |
|---|---|---|
| `proxy` (Caddy) | 브라우저의 유일한 입구: https, 업로드 상한, 보안 헤더(CSP 등) | 가능 |
| `api` | FastAPI: 로그인·사건·문서·업로드 접수, 빌드된 화면 서빙 | **차단** |
| `worker` | 문서 변환·OCR·청킹·임베딩·저장(원문을 가장 많이 다룸) | **차단** |
| `mcp` | 에이전트(Claude 등)용 MCP 서버. 프록시의 `/mcp`, 웹에서 발급한 **개인 토큰**으로 접속, 발급자의 접근 그룹 권한으로 동작 | **차단** |
| `db` | PostgreSQL + pgvector, 접근 그룹 권한(RLS) 강제 | **차단** |
| `migrate` | (일회성) 스키마 마이그레이션·앱용 DB 계정 생성 — 소유자 비밀번호를 가진 유일한 앱 컨테이너 | 차단 |
| `fetch-models` | (일회성, 최초 1회) 임베딩·OCR 모델을 볼륨에 내려받기 | 가능 |

```bash
docker compose build
docker compose --profile setup run --rm fetch-models         # 최초 1회, 인터넷 필요(임베딩 모델 약 2.2GB + OCR 모델)
docker compose -f docker-compose.yml up -d                   # 운영: https 만 공개(127.0.0.1:8443). 개발은 -f 없이 up -d
docker compose -f docker-compose.yml run --rm migrate python -m backend.app.manage create-admin --user-id admin --name "관리자"
```

- 워커·API·DB는 `internal` 네트워크에만 붙어 **인터넷에 나갈 수 없다**(문서 원문이 사내 밖으로 나가지 않는다는 원칙을 인프라로 강제). 모델은 위 준비 단계에서 미리 받아 둔다.
- 모든 앱 컨테이너는 비root(uid 10001)·읽기 전용 루트 파일시스템·`cap_drop: ALL`. 컨테이너마다 필요한 DB 비밀번호만 받는다(API는 소유자·워커 비밀번호가 없다).
- 개발용 `docker-compose.override.yml`(자동 적용)은 DB 포트(127.0.0.1:5433)와 평문 HTTP(127.0.0.1:8081)를 추가로 연다. **운영에서는 `-f docker-compose.yml` 로 이 파일을 쓰지 않는다.**
- https 인증서: 기본은 Caddy 내부 CA(`tls internal`, 브라우저에 신뢰 등록 필요). 실제 도메인·사내 인증서는 `LAWCA_HOST`와 `deploy/Caddyfile`의 `tls` 줄을 바꾼다. 외부에 공개하려면 `LAWCA_BIND=0.0.0.0`.
- 볼륨: `lawca_pgdata`(DB), `lawca_uploads`(업로드 원본), `lawca_models`(모델), `caddy_data`. **백업 대상은 앞의 두 개**이며 함께 백업해야 일관된다.
- 백엔드 테스트는 워커 컨테이너가 같은 DB의 업로드 작업을 집어 가지 않도록 `docker compose stop worker` 후 실행하는 것이 확실하다.
- MCP 서버는 컨테이너 `mcp`로 함께 뜬다. 변호사가 웹의 **AI 연결**에서 토큰을 발급해 Claude에 등록한다(연결 방법은 [server/README.md](server/README.md)).

프런트 개발 서버는 `npm run dev`(포트 5173, `/api`를 8000으로 프록시). 운영에서는 **https 리버스 프록시 뒤에서** `LAWCA_COOKIE_SECURE=1`로 실행하고, 프록시에 요청 본문 상한(`client_max_body_size`)을 설정하세요.

MCP 연결은 [server/README.md](server/README.md)와 `.mcp.json.example` 참고(운영: 웹에서 발급한 토큰, 개발: stdio + `LAWCA_USER_ID`).

## 설계 요점

- **권한은 DB가 강제**: 읽기 전용 계정 + 행 수준 보안(RLS). 사용자 미지정은 거부(fail closed).
- **외부로 나가는 경계는 LLM 호출 하나**: 임베딩·OCR·검색은 전부 로컬.
- **식별자(사건번호·당사자·금액)는 정확 일치**, 의미 검색은 문서·청크 임베딩 + 문자 trigram 키워드 결합.
- **참고 서면은 마스킹**해서 전달, 본문은 데이터일 뿐이라는 안내로 프롬프트 인젝션 대응.
- **근거 없으면 '근거 없음'**, 출처(doc_id·섹션) 표기 강제.

## 현재 상태와 한계

프로토타입 단계이며 합성 데이터와 소규모 실험에 근거한 결론입니다. 알려진 한계:
- 마스킹은 형식이 정해진 값과 사건 DB의 당사자·변호사명만 처리하며, 본문 속 제3자 인명 등은 탐지하지 못함.
- `list_matters`·`search_documents`는 사건번호·당사자명을 기본으로 가리며(`include_identifiers=true`로 열면 감사 로그에 별도 기록), 본문 속 제3자 인명은 여전히 걸러내지 못함.
- 프롬프트 인젝션 방어는 1회 시험만 통과했으며 충분히 검증되지 않음.
- `check_citation`, `search_cases` 미구현.
- MCP 토큰은 탈취되면 만료·폐기 전까지 그대로 쓸 수 있는 베어러 비밀이다(IP·기기 고정, MFA 없음). 호출 속도 제한은 서버 프로세스별 메모리 기준이라 여러 대로 늘리면 공유되지 않는다. stdio 모드의 환경변수 신원은 개발 전용이다.
- 웹: 업로드 원본의 저장 시 암호화·바이러스 검사 없음(볼륨 암호화·별도 검사 전제), MFA·IP 기반 제한 없음, 로그인 잠금은 아이디 기준이라 남의 계정을 잠그는 공격 가능, 프런트엔드 자동화 테스트 없음(수동 확인만).
- 임베딩은 CPU에서 약 1~1.5 청크/초라 대량 색인(수만 건)에는 GPU 또는 다중 워커가 필요함.
- 업로드 시험은 DOCX·합성 스캔 PDF로만 했고 실제 한컴(HWP/HWPX)·법원 서식은 아직 검증하지 못함.
