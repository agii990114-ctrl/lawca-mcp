-- 003: 문서 삭제 시 원본 파일 기록도 함께 정리할 수 있게 한다
-- (작업 이력은 남기고 파일 참조만 끊는다)
ALTER TABLE ingest_jobs DROP CONSTRAINT IF EXISTS ingest_jobs_file_id_fkey;
ALTER TABLE ingest_jobs ADD CONSTRAINT ingest_jobs_file_id_fkey
    FOREIGN KEY (file_id) REFERENCES uploaded_files(file_id) ON DELETE SET NULL;
GRANT DELETE ON uploaded_files TO lawca_app;
