-- 법무법인 AI 업무 지원 시스템: 샘플 스키마 (PostgreSQL 17 + pgvector + pg_trgm)
-- 컨테이너 최초 기동 시 1회 실행된다. 재적용하려면 `docker compose down -v` 후 다시 기동.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------- 사용자·접근 그룹
CREATE TABLE users (
    user_id  text PRIMARY KEY,
    name     text NOT NULL,
    role     text NOT NULL CHECK (role IN ('변호사', '사무직원', '관리자'))
);

-- 접근 그룹 = 사건 단위 ethical wall 경계. 사용자는 소속 그룹의 사건만 볼 수 있다.
CREATE TABLE access_groups (
    group_id     text PRIMARY KEY,
    description  text
);

CREATE TABLE user_groups (
    user_id   text NOT NULL REFERENCES users(user_id),
    group_id  text NOT NULL REFERENCES access_groups(group_id),
    PRIMARY KEY (user_id, group_id)
);

-- ---------------------------------------------------------------- 사건
CREATE TABLE matters (
    matter_id     text PRIMARY KEY,
    case_no       text NOT NULL UNIQUE,          -- 예: 2025가합12345 (연도+부호+번호)
    case_type     text NOT NULL,                 -- 사건명(대여금, 해고무효확인 등)
    court         text,
    plaintiff     text NOT NULL,
    defendant     text NOT NULL,
    our_side      text NOT NULL CHECK (our_side IN ('원고', '피고')),   -- 우리 사무소가 대리하는 쪽
    attorney_name text,                          -- 담당 변호사(표시용)
    result        text NOT NULL CHECK (result IN ('승소', '패소', '일부승소', '진행중')),
    access_group  text NOT NULL REFERENCES access_groups(group_id)
);
CREATE INDEX matters_group_idx ON matters (access_group);
CREATE INDEX matters_type_idx ON matters (case_type);
CREATE INDEX matters_case_no_trgm ON matters USING gin (case_no gin_trgm_ops);

-- ---------------------------------------------------------------- 문서
-- access_group은 matters에서 복사한 값(검색 단계에서 조인 없이 필터링하려는 비정규화).
-- 일치는 적재 스크립트와 아래 트리거가 보장한다.
CREATE TABLE documents (
    doc_id        text PRIMARY KEY,
    matter_id     text NOT NULL REFERENCES matters(matter_id),
    kind          text NOT NULL CHECK (kind IN ('소 장', '답 변 서', '준 비 서 면')),
    round         int  NOT NULL DEFAULT 1,       -- 차수
    author_side   text NOT NULL CHECK (author_side IN ('원고', '피고')),
    filed_date    date,
    version       text NOT NULL DEFAULT '최종본' CHECK (version IN ('초안', '수정본', '최종본')),
    full_text     text NOT NULL,                 -- 원문(마스킹은 get_document 반환 시점에 적용)
    text_norm     text NOT NULL,                 -- 공백 제거본: 띄어쓰기가 깨진 OCR 텍스트에도 매칭되는 키워드 검색용
    access_group  text NOT NULL REFERENCES access_groups(group_id),
    embedding     vector(1024)                   -- 문서 전체 임베딩 (multilingual-e5-large)
);
CREATE INDEX documents_matter_idx ON documents (matter_id);
CREATE INDEX documents_group_idx  ON documents (access_group);
CREATE INDEX documents_kind_idx   ON documents (kind, round);
CREATE INDEX documents_norm_trgm  ON documents USING gin (text_norm gin_trgm_ops);
CREATE INDEX documents_emb_hnsw   ON documents USING hnsw (embedding vector_cosine_ops);

CREATE OR REPLACE FUNCTION documents_sync_group() RETURNS trigger AS $$
BEGIN
    SELECT access_group INTO NEW.access_group FROM matters WHERE matter_id = NEW.matter_id;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER documents_sync_group_trg BEFORE INSERT OR UPDATE OF matter_id ON documents
    FOR EACH ROW EXECUTE FUNCTION documents_sync_group();

-- 쟁점 태그 (준비서면 검색의 필터 축: "같은 쟁점을 다룬 서면")
CREATE TABLE doc_issue_tags (
    doc_id  text NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    tag     text NOT NULL,
    PRIMARY KEY (doc_id, tag)
);
CREATE INDEX doc_issue_tags_tag_idx ON doc_issue_tags (tag);

-- 청크: 준비서면은 섹션(상대방 주장 요지/인정·부인/쟁점별 반박/입증방법/결론) 및 반박 소항목 단위
CREATE TABLE doc_chunks (
    chunk_id      bigserial PRIMARY KEY,
    doc_id        text NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    seq           int  NOT NULL,
    section       text,                          -- 예: "3. 쟁점별 반박 > 가. 소멸시효의 완성"
    text          text NOT NULL,
    text_norm     text NOT NULL,
    access_group  text NOT NULL REFERENCES access_groups(group_id),
    embedding     vector(1024),
    UNIQUE (doc_id, seq)
);
CREATE INDEX doc_chunks_group_idx ON doc_chunks (access_group);
CREATE INDEX doc_chunks_norm_trgm ON doc_chunks USING gin (text_norm gin_trgm_ops);
CREATE INDEX doc_chunks_emb_hnsw  ON doc_chunks USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------- 감사 로그·평가 세트
CREATE TABLE audit_log (
    id            bigserial PRIMARY KEY,
    ts            timestamptz NOT NULL DEFAULT now(),
    user_id       text NOT NULL,
    tool          text NOT NULL,                 -- list_matters / search_documents / get_document ...
    args          jsonb,                         -- 호출 인자(질의 문자열 등, 본문은 저장하지 않음)
    doc_ids       text[],                        -- 반환된 문서 ID
    result_count  int
);
CREATE INDEX audit_log_user_ts_idx ON audit_log (user_id, ts DESC);

CREATE TABLE eval_queries (
    query_id     serial PRIMARY KEY,
    query        text NOT NULL,
    qtype        text NOT NULL,                  -- counter / detail / case_no / party / amount
    gold_doc_id  text NOT NULL REFERENCES documents(doc_id),
    gold_docs    text[]                          -- 정답이 여럿인 질의(같은 쟁점 등)
);

-- ---------------------------------------------------------------- 행 수준 보안 (RLS)
-- 세션 변수 app.user_id 를 MCP 서버가 요청마다 설정한다. 미설정이면 아무 행도 보이지 않는다(fail closed).
-- 서버 코드의 권한 검사가 뚫려도 DB가 한 번 더 막는 이중 방어선이다.
CREATE OR REPLACE FUNCTION app_visible_groups() RETURNS SETOF text AS $$
    SELECT group_id FROM user_groups
     WHERE user_id = NULLIF(current_setting('app.user_id', true), '')
$$ LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public;

ALTER TABLE matters         ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents       ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_chunks      ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_issue_tags  ENABLE ROW LEVEL SECURITY;

CREATE POLICY matters_visible ON matters FOR SELECT TO lawca_reader
    USING (access_group IN (SELECT app_visible_groups()));
CREATE POLICY documents_visible ON documents FOR SELECT TO lawca_reader
    USING (access_group IN (SELECT app_visible_groups()));
CREATE POLICY chunks_visible ON doc_chunks FOR SELECT TO lawca_reader
    USING (access_group IN (SELECT app_visible_groups()));
CREATE POLICY tags_visible ON doc_issue_tags FOR SELECT TO lawca_reader
    USING (EXISTS (SELECT 1 FROM documents d WHERE d.doc_id = doc_issue_tags.doc_id));

-- ---------------------------------------------------------------- 권한 부여 (최소 권한)
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO lawca_reader, lawca_audit;
GRANT SELECT ON matters, documents, doc_chunks, doc_issue_tags TO lawca_reader;
GRANT SELECT ON users, user_groups, access_groups TO lawca_reader;   -- 사용자 확인용(목록 조회 도구에는 노출하지 않음)
GRANT EXECUTE ON FUNCTION app_visible_groups() TO lawca_reader;
GRANT INSERT ON audit_log TO lawca_audit;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO lawca_audit;
-- eval_queries: 서버 계정에는 노출하지 않는다(평가 하네스는 owner 계정 사용)
