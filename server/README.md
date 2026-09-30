# lawca MCP 서버

가상의 더미 사건·서면 데이터를 PostgreSQL(+pgvector)에서 조회하는 MCP 서버. 도구 3개: `list_matters`, `search_documents`, `get_document`.

## 접속 방식

| 방식 | 용도 | 신원 |
|---|---|---|
| **HTTP (운영)** | 컨테이너 `mcp`, 프록시의 `/mcp` 경로. 변호사가 웹의 **AI 연결** 화면에서 발급한 개인 토큰(`Authorization: Bearer …`)으로 접속 | **요청마다 토큰이 가리키는 사용자**. 폐기·만료·비활성 계정은 즉시 무효 |
| stdio (개발) | 개인 PC에서 시험 | 서버를 띄우는 프로세스의 `LAWCA_USER_ID` (신원을 마음대로 정할 수 있어 **운영에 쓰지 않는다**) |

Claude Code 연결: `claude mcp add --transport http lawca https://<서버>/mcp --header "Authorization: Bearer <토큰>"`
(토큰 발급·폐기: 웹 로그인 → **AI 연결**. 변호사만 발급할 수 있고 사용 중인 토큰은 5개까지, 유효기간 최대 90일.)

## 실행 (개발용 stdio)

```bash
docker compose up -d db                                # DB (프로젝트 루트, 개발용 override로 127.0.0.1:5433)
parse-test/.venv/Scripts/python db/load_dummy.py       # 더미 적재 + 임베딩 (약 7분, 최초 1회)
LAWCA_USER_ID=u_kim server/.venv/Scripts/python server/lawca_server.py   # stdio MCP 서버
server/.venv/Scripts/python server/tests/e2e.py        # 종단 테스트
```

stdio 모드는 클라이언트 설정에서 `LAWCA_USER_ID` 환경변수로 사용자를 지정한다(예: `claude mcp add lawca -e LAWCA_USER_ID=u_kim -- <python 경로> server/lawca_server.py`). 운영 접속은 위의 HTTP 방식이며 `.mcp.json.example`이 그 형식이다.

## 테스트 사용자 (접근 그룹)

| user_id | 접근 그룹 |
|---|---|
| u_kim | G-A, G-B |
| u_lee | G-A |
| u_park | G-C (이해충돌 방지 테스트용 격리 그룹) |
| u_admin | G-A, G-B, G-C |

## 구조와 원칙

- **권한**: 서버가 신원(HTTP: 토큰의 주인, stdio: `LAWCA_USER_ID`)을 세션 변수(`app.user_id`)로 DB에 전달하고, 읽기 전용 계정(`lawca_reader`)에 걸린 행 수준 보안(RLS)이 접근 범위를 강제한다. 미설정·미등록 사용자는 거부(fail closed).
- **감사 로그**: 모든 호출을 `audit_log`에 기록(전용 계정, 기록 실패 시 호출도 실패). 마스킹 없는 원문 조회는 `mask=false`로 구분.
- **검색**: 식별자(사건번호·당사자·금액)는 전용 인자로 정확 일치 필터, 자연어는 문서 임베딩 + 청크 임베딩 + 문자 trigram 키워드를 RRF로 결합. 임베딩은 로컬 모델(multilingual-e5-large).
- **마스킹**: 참고 서면 본문은 기본 마스킹(정규식 + 사건 DB의 당사자·변호사명). 제3자 인명 등은 탐지하지 못한다(`residual_risk` 참고).
- **프롬프트 인젝션 대응**: 본문은 데이터일 뿐이라는 안내를 서버 지침과 반환값에 포함(모델이 따르는지는 별도 검증 필요).
- **HTTP 모드의 방어**: 토큰은 DB에 해시로만 저장되고 MCP 서버(읽기 전용 계정)는 검증 함수만 호출할 수 있다. 신원 위조 헤더는 무시하고, 환경변수 신원은 쓰지 않으며, 사용자당 분당 120회로 호출 속도를 제한한다(서버 프로세스별 메모리 기준). 감사 로그에 어떤 토큰으로 호출했는지 남는다.
- 테스트: `server/tests/e2e.py`(stdio, 30개), `server/tests/http_e2e.py`(컨테이너 스택·토큰 인증, 24개). 실제 Claude 연결 확인은 `eval/claude_http_check.py`.
