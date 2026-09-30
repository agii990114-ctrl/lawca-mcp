-- 002: 백엔드(인증·역할·업로드·인제스트 큐) 스키마
-- 러너(db/migrate.py)가 하나의 트랜잭션으로 실행하고, 역할(lawca_app, lawca_worker)은 러너가 먼저 만든다.

-- ------------------------------------------------------------------ 사용자·역할
DO $$
DECLARE c text;
BEGIN
    FOR c IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'users'::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%role%'
    LOOP EXECUTE format('ALTER TABLE users DROP CONSTRAINT %I', c); END LOOP;
END $$;
UPDATE users SET role = '사무원' WHERE role = '사무직원';
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('변호사', '사무원', '관리자'));
ALTER TABLE users ADD COLUMN IF NOT EXISTS active     boolean     NOT NULL DEFAULT true;
ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();

-- 비활성 사용자는 어떤 그룹도 볼 수 없다
CREATE OR REPLACE FUNCTION app_visible_groups() RETURNS SETOF text AS $$
    SELECT ug.group_id FROM user_groups ug JOIN users u USING (user_id)
     WHERE ug.user_id = NULLIF(current_setting('app.user_id', true), '') AND u.active
$$ LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public;

CREATE TABLE user_credentials (
    user_id       text PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    password_hash text NOT NULL,                       -- scrypt$n$r$p$salt$hash
    must_change   boolean NOT NULL DEFAULT false,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    token_hash  text PRIMARY KEY,                      -- 쿠키 토큰의 SHA-256 (토큰 원문은 저장하지 않음)
    user_id     text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    csrf_token  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    ip          text,
    user_agent  text
);
CREATE INDEX sessions_user_idx ON sessions (user_id);

CREATE TABLE login_attempts (
    id      bigserial PRIMARY KEY,
    user_id text NOT NULL,
    ip      text,
    ok      boolean NOT NULL,
    ts      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX login_attempts_user_ts_idx ON login_attempts (user_id, ts DESC);

-- ------------------------------------------------------------------ 문서 종류 확장 (업로드되는 과거 문서는 준비서면만이 아니다)
DO $$
DECLARE c text;
BEGIN
    FOR c IN SELECT conname FROM pg_constraint
             WHERE conrelid = 'documents'::regclass AND contype = 'c'
               AND (pg_get_constraintdef(oid) LIKE '%kind%' OR pg_get_constraintdef(oid) LIKE '%author_side%')
    LOOP EXECUTE format('ALTER TABLE documents DROP CONSTRAINT %I', c); END LOOP;
END $$;
ALTER TABLE documents ADD CONSTRAINT documents_kind_check
    CHECK (kind IN ('소 장', '답 변 서', '준 비 서 면', '판결문', '계약서', '의견서', '기타'));
ALTER TABLE documents ADD CONSTRAINT documents_author_side_check CHECK (author_side IN ('원고', '피고', '미상'));
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_file_id bigint;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_name    text;

CREATE SEQUENCE IF NOT EXISTS doc_id_seq;
CREATE SEQUENCE IF NOT EXISTS matter_id_seq;

-- ------------------------------------------------------------------ 업로드·인제스트
CREATE TABLE uploaded_files (
    file_id       bigserial PRIMARY KEY,
    sha256        text NOT NULL,
    size_bytes    bigint NOT NULL,
    orig_name     text NOT NULL,                       -- 표시용 이름(ZIP 내부 경로 포함). 저장 경로에는 쓰지 않는다
    stored_path   text NOT NULL,                       -- 저장소 내부의 해시 기반 경로
    uploaded_by   text NOT NULL REFERENCES users(user_id),
    access_group  text NOT NULL REFERENCES access_groups(group_id),
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX uploaded_files_sha_idx ON uploaded_files (sha256);

CREATE TABLE ingest_batches (
    batch_id      bigserial PRIMARY KEY,
    created_by    text NOT NULL REFERENCES users(user_id),
    access_group  text NOT NULL REFERENCES access_groups(group_id),
    matter_id     text REFERENCES matters(matter_id),
    source_kind   text NOT NULL CHECK (source_kind IN ('files', 'zip', 'mixed')),
    note          text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ingest_jobs (
    job_id          bigserial PRIMARY KEY,
    batch_id        bigint NOT NULL REFERENCES ingest_batches(batch_id) ON DELETE CASCADE,
    file_id         bigint REFERENCES uploaded_files(file_id),
    orig_name       text NOT NULL,
    status          text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'parsing', 'needs_review', 'ready_to_index', 'indexing', 'done', 'failed', 'duplicate', 'rejected')),
    reason          text,                              -- 실패·검토 필요·거부 사유(사용자에게 표시)
    attempts        int NOT NULL DEFAULT 0,
    created_by      text NOT NULL REFERENCES users(user_id),
    access_group    text NOT NULL REFERENCES access_groups(group_id),
    matter_id       text REFERENCES matters(matter_id),
    doc_kind        text,
    round           int,
    author_side     text,
    filed_date      date,
    issue_tags      text[] NOT NULL DEFAULT ARRAY[]::text[],
    extracted_text  text,                              -- 파싱 결과(검토 후 색인). 접근 그룹 RLS 대상
    parse_meta      jsonb,
    doc_id          text,                              -- 완료 시 생성된 문서
    locked_by       text,
    locked_at       timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ingest_jobs_status_idx ON ingest_jobs (status, job_id);
CREATE INDEX ingest_jobs_batch_idx  ON ingest_jobs (batch_id);
CREATE INDEX ingest_jobs_user_idx   ON ingest_jobs (created_by, job_id DESC);

-- ------------------------------------------------------------------ 행 수준 보안
-- 사건·문서·청크·태그: API(lawca_app)와 워커(lawca_worker)도 접근 그룹 RLS를 받는다.
-- 워커는 작업을 올린 사용자의 신원(app.user_id)으로 저장하므로 접근 그룹을 넘는 저장은 DB가 막는다.
CREATE POLICY matters_app_all ON matters FOR ALL TO lawca_app, lawca_worker
    USING (access_group IN (SELECT app_visible_groups()))
    WITH CHECK (access_group IN (SELECT app_visible_groups()));
CREATE POLICY documents_app_all ON documents FOR ALL TO lawca_app, lawca_worker
    USING (access_group IN (SELECT app_visible_groups()))
    WITH CHECK (access_group IN (SELECT app_visible_groups()));
CREATE POLICY chunks_app_all ON doc_chunks FOR ALL TO lawca_app, lawca_worker
    USING (access_group IN (SELECT app_visible_groups()))
    WITH CHECK (access_group IN (SELECT app_visible_groups()));
CREATE POLICY tags_app_all ON doc_issue_tags FOR ALL TO lawca_app, lawca_worker
    USING (EXISTS (SELECT 1 FROM documents d WHERE d.doc_id = doc_issue_tags.doc_id))
    WITH CHECK (EXISTS (SELECT 1 FROM documents d WHERE d.doc_id = doc_issue_tags.doc_id));

-- 업로드·작업: API는 본인이 올렸거나 접근 그룹에 속한 것만, 워커는 큐 전체(신원 없이 작업을 집는다)
ALTER TABLE uploaded_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingest_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingest_jobs    ENABLE ROW LEVEL SECURITY;

CREATE POLICY files_app ON uploaded_files FOR ALL TO lawca_app
    USING (access_group IN (SELECT app_visible_groups()))
    WITH CHECK (access_group IN (SELECT app_visible_groups()));
CREATE POLICY batches_app ON ingest_batches FOR ALL TO lawca_app
    USING (access_group IN (SELECT app_visible_groups()))
    WITH CHECK (access_group IN (SELECT app_visible_groups()));
CREATE POLICY jobs_app ON ingest_jobs FOR ALL TO lawca_app
    USING (access_group IN (SELECT app_visible_groups()))
    WITH CHECK (access_group IN (SELECT app_visible_groups()));
CREATE POLICY files_worker   ON uploaded_files FOR ALL TO lawca_worker USING (true) WITH CHECK (true);
CREATE POLICY batches_worker ON ingest_batches FOR ALL TO lawca_worker USING (true) WITH CHECK (true);
CREATE POLICY jobs_worker    ON ingest_jobs    FOR ALL TO lawca_worker USING (true) WITH CHECK (true);

-- ------------------------------------------------------------------ 최소 권한
GRANT USAGE ON SCHEMA public TO lawca_app, lawca_worker;
GRANT EXECUTE ON FUNCTION app_visible_groups() TO lawca_app, lawca_worker;

-- API
GRANT SELECT ON users, user_groups, access_groups, matters, documents, doc_chunks, doc_issue_tags,
                uploaded_files, ingest_batches, ingest_jobs TO lawca_app;
GRANT INSERT, UPDATE ON users TO lawca_app;
GRANT INSERT, DELETE ON user_groups TO lawca_app;
GRANT INSERT ON access_groups TO lawca_app;
GRANT SELECT, INSERT, UPDATE ON user_credentials TO lawca_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON sessions TO lawca_app;
GRANT SELECT, INSERT ON login_attempts TO lawca_app;
GRANT INSERT, UPDATE ON matters TO lawca_app;
GRANT DELETE ON documents TO lawca_app;
GRANT INSERT ON uploaded_files, ingest_batches, ingest_jobs TO lawca_app;
GRANT UPDATE ON ingest_jobs TO lawca_app;
GRANT USAGE ON SEQUENCE uploaded_files_file_id_seq, ingest_batches_batch_id_seq, ingest_jobs_job_id_seq,
                        login_attempts_id_seq, matter_id_seq TO lawca_app;

-- 워커
GRANT SELECT ON users, user_groups, access_groups, matters, uploaded_files, ingest_batches TO lawca_worker;
GRANT SELECT, UPDATE ON ingest_jobs TO lawca_worker;
GRANT SELECT, INSERT ON documents, doc_chunks, doc_issue_tags TO lawca_worker;
GRANT USAGE ON SEQUENCE doc_id_seq, doc_chunks_chunk_id_seq TO lawca_worker;
