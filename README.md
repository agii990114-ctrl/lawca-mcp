# lawca-mcp

법무법인 내부 데이터(사건 DB, 서면)를 MCP 서버로 노출해, AI 에이전트가 **유사 서면을 검색·조회하고 준비서면 초안을 작성**하도록 지원하는 시스템의 프로토타입.

> **모든 데이터는 가상의 더미입니다.** 사건·당사자·연락처·계좌 등은 전부 지어낸 값이며 실제 인물·사건과 무관합니다. 주민등록번호 형식 값 등은 마스킹 테스트용 가짜입니다.
> 결과물은 변호사가 검토하는 초안이며, 법률 자문이 아닙니다.

기획 배경과 설계 결정은 [기획서_v2.md](기획서_v2.md) 참고.

## 구성

| 경로 | 내용 |
|---|---|
| `server/` | MCP 서버(Python). 도구: `list_matters`, `search_documents`, `get_document`. 하이브리드 검색, 서버 측 마스킹, 감사 로그 |
| `db/` | PostgreSQL + pgvector 스키마(행 수준 보안 포함), 더미 적재 스크립트, 검증 스크립트 |
| `dummy-data/` | 가상 사건 32건·문서 64건(준비서면 중심, 쟁점 태그·접근 그룹 포함) |
| `eval/` | Claude를 MCP 클라이언트로 붙인 준비서면 초안 작성 시험(정상·인젝션·근거 없음·권한) |
| `parse-test/` | 사전 실험: kordoc 파싱·OCR, 임베딩·검색·라우팅·리랭커 비교 |
| `docker-compose.yml` | PostgreSQL 17 + pgvector 컨테이너 |

## 빠른 시작

```bash
cp .env.example .env               # 개발용 비밀번호로 수정
docker compose up -d               # DB (127.0.0.1:5433)
python -m venv server/.venv && server/.venv/Scripts/python -m pip install -r server/requirements.txt
server/.venv/Scripts/python db/load_dummy.py     # 더미 적재 + 로컬 임베딩(약 7분, 최초 1회 모델 다운로드)
server/.venv/Scripts/python db/verify.py         # 스키마·RLS·권한 검증
server/.venv/Scripts/python server/tests/e2e.py  # MCP 종단 테스트
```

MCP 클라이언트 연결은 `.mcp.json.example` 참고. 사용자 신원은 `LAWCA_USER_ID`(테스트 사용자 u_kim, u_lee, u_park, u_admin)로 지정한다. 자세한 내용은 [server/README.md](server/README.md).

## 설계 요점

- **권한은 DB가 강제**: 읽기 전용 계정 + 행 수준 보안(RLS). 사용자 미지정은 거부(fail closed).
- **외부로 나가는 경계는 LLM 호출 하나**: 임베딩·OCR·검색은 전부 로컬.
- **식별자(사건번호·당사자·금액)는 정확 일치**, 의미 검색은 문서·청크 임베딩 + 문자 trigram 키워드 결합.
- **참고 서면은 마스킹**해서 전달, 본문은 데이터일 뿐이라는 안내로 프롬프트 인젝션 대응.
- **근거 없으면 '근거 없음'**, 출처(doc_id·섹션) 표기 강제.

## 현재 상태와 한계

프로토타입 단계이며 합성 데이터와 소규모 실험에 근거한 결론입니다. 알려진 한계:
- 마스킹은 형식이 정해진 값과 사건 DB의 당사자·변호사명만 처리하며, 본문 속 제3자 인명 등은 탐지하지 못함.
- `list_matters`·`search_documents`가 사건번호·당사자명을 그대로 반환함(개선 예정).
- 프롬프트 인젝션 방어는 1회 시험만 통과했으며 충분히 검증되지 않음.
- `check_citation`, `search_cases` 미구현. 스캔(OCR) 경로는 서버에 미연결.
- 사용자 신원은 환경변수 주입 방식(프로토타입). 실제 배포에는 인증 필요.
- 실제 한컴 HWP/HWPX·법원 서식은 아직 검증하지 못함.
