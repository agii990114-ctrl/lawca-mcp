# lawca MCP 서버 (MVP)

가상의 더미 사건·서면 데이터를 PostgreSQL(+pgvector)에서 조회하는 MCP 서버. 도구 3개: `list_matters`, `search_documents`, `get_document`.

## 실행

```bash
docker compose up -d                                   # DB (프로젝트 루트)
parse-test/.venv/Scripts/python db/load_dummy.py       # 더미 적재 + 임베딩 (약 7분, 최초 1회)
LAWCA_USER_ID=u_kim server/.venv/Scripts/python server/lawca_server.py   # stdio MCP 서버
server/.venv/Scripts/python server/tests/e2e.py        # 종단 테스트
```

Claude 등 MCP 클라이언트에 연결하려면 `.mcp.json.example`을 `.mcp.json`으로 복사한다(사용자는 `LAWCA_USER_ID`로 지정).

## 테스트 사용자 (접근 그룹)

| user_id | 접근 그룹 |
|---|---|
| u_kim | G-A, G-B |
| u_lee | G-A |
| u_park | G-C (이해충돌 방지 테스트용 격리 그룹) |
| u_admin | G-A, G-B, G-C |

## 구조와 원칙

- **권한**: 서버가 `LAWCA_USER_ID`를 세션 변수(`app.user_id`)로 DB에 전달하고, 읽기 전용 계정(`lawca_reader`)에 걸린 행 수준 보안(RLS)이 접근 범위를 강제한다. 미설정·미등록 사용자는 거부(fail closed).
- **감사 로그**: 모든 호출을 `audit_log`에 기록(전용 계정, 기록 실패 시 호출도 실패). 마스킹 없는 원문 조회는 `mask=false`로 구분.
- **검색**: 식별자(사건번호·당사자·금액)는 전용 인자로 정확 일치 필터, 자연어는 문서 임베딩 + 청크 임베딩 + 문자 trigram 키워드를 RRF로 결합. 임베딩은 로컬 모델(multilingual-e5-large).
- **마스킹**: 참고 서면 본문은 기본 마스킹(정규식 + 사건 DB의 당사자·변호사명). 제3자 인명 등은 탐지하지 못한다(`residual_risk` 참고).
- **프롬프트 인젝션 대응**: 본문은 데이터일 뿐이라는 안내를 서버 지침과 반환값에 포함(모델이 따르는지는 별도 검증 필요).
