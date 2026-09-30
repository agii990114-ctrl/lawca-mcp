#!/bin/bash
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  -- 검색·조회용 읽기 전용 계정 (행 수준 보안 적용 대상, 쓰기 불가, 질의 시간 제한)
  CREATE ROLE lawca_reader LOGIN PASSWORD '${LAWCA_READER_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  ALTER ROLE lawca_reader SET default_transaction_read_only = on;
  ALTER ROLE lawca_reader SET statement_timeout = '10s';
  -- 감사 로그 기록 전용 계정 (audit_log INSERT만 가능)
  CREATE ROLE lawca_audit LOGIN PASSWORD '${LAWCA_AUDIT_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
EOSQL
