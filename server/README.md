# lawca MCP 서버

가상의 더미 사건·서면 데이터를 PostgreSQL(+pgvector)에서 조회하는 MCP 서버. 도구 7개: `list_matters`, `search_documents`, `get_document`(긴 서면은 `section`·`part`로 나누어 읽기), `check_citation`, 공개 판례·조문 조회 `get_case`·`get_statute`·`search_cases`.

## 접속 방식

| 방식 | 용도 | 신원 |
|---|---|---|
| **HTTP (운영)** | 컨테이너 `mcp`, 프록시의 `/mcp` 경로. 변호사가 웹의 **AI 연결** 화면에서 발급한 개인 토큰(`Authorization: Bearer …`)으로 접속 | **요청마다 토큰이 가리키는 사용자**. 폐기·만료·비활성 계정은 즉시 무효 |
| stdio (개발) | 개인 PC에서 시험 | 서버를 띄우는 프로세스의 `LAWCA_USER_ID` (신원을 마음대로 정할 수 있어 **운영에 쓰지 않는다**) |

Claude Code 연결: `claude mcp add --transport http lawca https://<서버>/mcp --header "Authorization: Bearer <토큰>"`

다른 AI 프로그램도 같은 서버·같은 토큰으로 연결한다(웹 화면의 **AI 연결 → 토큰 발급** 창에 프로그램별 설정 예시가 나온다). 설정 방식은 각 프로그램의 공개 문서를 근거로 작성했고 **Claude Code 외에는 실제 연결을 시험하지 못했다**.

| 프로그램 | 설정 |
|---|---|
| Claude Code | `claude mcp add --transport http …` 또는 `.mcp.json` (`type: http`, `url`, `headers`) |
| Cursor | `~/.cursor/mcp.json` 또는 `.cursor/mcp.json` (`url`, `headers`) |
| Gemini CLI | `~/.gemini/settings.json`의 `mcpServers` (`httpUrl`, `headers`) |
| Codex CLI(OpenAI) | `~/.codex/config.toml`의 `[mcp_servers.lawca]` (`url`, `bearer_token_env_var`) — 토큰은 환경변수로 |
| ChatGPT·claude.ai 등 **웹 앱 커넥터** | **미지원.** 사용자의 PC가 아니라 회사 서버에서 접속하므로 서버를 인터넷에 공개하고 OAuth를 구현해야 한다(사무소 정책 결정 필요) |

클라이언트마다 서버 지침(`instructions`)·MCP 프롬프트를 반영하는 정도가 다르므로 핵심 규칙을 도구 설명에도 넣어 두었다. 모델별 행동(인젝션·판례 지어내기·증거 단정)은 Claude 기준으로만 시험했다.
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
- **마스킹**: 참고 서면 본문은 기본 마스킹한다. 정규식(번호·금액·주소·상호) + 사건 DB의 당사자·담당 변호사·등록 관련자(`related_names`, 웹 사건 화면에서 등록) 이름 + 역할어·호칭이 붙은 제3자 인명(`소외 ○○○`, `○○○ 씨`). 역할어 없는 제3자 인명·건물명만 있는 주소는 탐지하지 못한다(`residual_risk` 참고).
- **프롬프트 인젝션 대응**: 본문은 데이터일 뿐이라는 안내를 서버 지침과 반환값에 포함(모델이 따르는지는 별도 검증 필요).
- **HTTP 모드의 방어**: 토큰은 DB에 해시로만 저장되고 MCP 서버(읽기 전용 계정)는 검증 함수만 호출할 수 있다. 신원 위조 헤더는 무시하고, 환경변수 신원은 쓰지 않으며, 사용자당 분당 120회로 호출 속도를 제한한다(서버 프로세스별 메모리 기준). 감사 로그에 어떤 토큰으로 호출했는지 남는다.
- **공개 판례·조문 조회**(법제처 Open API): `get_case`(사건번호 → 판시사항·판결요지·참조조문·본문, 긴 본문은 `part`), `get_statute`(법령명+조문번호 → 현행 조문·시행일), `search_cases`(법률 용어 검색어 → 판례 목록). 서버가 법제처를 직접 부르며(`korean-law-mcp`를 쓰지 않음) 인터넷이 막힌 `mcp` 대신 **`law-gateway` 컨테이너**가 조회한다. 외부로 나가는 값은 검증된 사건번호, 법령명·조문번호, 검색어뿐이다. **검색어는 `query_guard`가 검사한다**: 마스킹 규칙에 걸리는 값(번호·금액·주소·상호·역할어가 붙은 이름·호칭)·50자 초과·줄바꿈이면 거부하고 사유와 재작성 안내를 돌려주며(AI가 고쳐 다시 요청), mcp와 중계 양쪽에서 검사한다. 검색어는 감사 로그에 그대로 남는다. 한계: 이름만 있는 검색어나 법률 용어로 풀어 쓴 사건 특징은 규칙으로 못 잡고, 그 부분은 도구 설명이 막는다.
- **인용 확인(`check_citation`)**: 초안 속 판례 사건번호·조문을 뽑아 (1) 사무소 등록부 `verified_citations` → (2) 법제처 조회 순서로 대조한다. 판례는 존재와 선고일 일치(하급심 표기 `서울고등법원-2023-누-36796`도 정규화해 비교), 조문은 그 법령에 그 조문·항이 있는지와 시행일을 확인한다. 판례는 법제처의 판시사항·판결요지(`official`)를 함께 돌려주고, 서면이 그 판례의 요지로 쓴 문장이 판례 내용과 거의 겹치지 않으면 `holding_warning`을 붙인다(글자 2개 단위 겹침 휴리스틱: 전혀 다른 쟁점은 잡지만 같은 분야의 조금 다른 요지는 못 잡는다). 확인되면 `ok_to_cite`, 아니면 `unverified`/`not_found`/`date_mismatch`/`inconsistent`로 돌려 에이전트가 본문에서 빼게 한다. 요지의 정확성과 개정 이력은 변호사가 원문으로 대조해야 한다. `law-gateway`는 DB 접근이 없고, 사건번호·법령명·조문·검사를 통과한 검색어만 받으며 법제처 한 곳에만 질의한다(응답은 필요한 필드만 반환, 24시간 캐시, 분당 60회 제한). 인증값은 `.env`의 `LAWCA_LAW_API_OC`이며 중계에만 전달된다. 법제처 DB에 없다고 해서 존재하지 않는다는 뜻은 아니다. 등록부 관리 화면은 아직 없다(`python db/seed_citations.py --file 항목.json`).
- 테스트: `server/tests/e2e.py`(stdio, 39개), `server/tests/test_citations.py`(인용 추출·판정 단위 시험), `server/tests/http_e2e.py`(컨테이너 스택·토큰 인증, 24개). 실제 Claude 연결 확인은 `eval/claude_http_check.py`. 서면 초안 반복 시험(요청 3종 × N회, 자동 채점)은 `python eval/brief_eval.py --runs 6 --scenario with04|without04`(모델 사용량이 든다).
