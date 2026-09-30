-- 004: MCP(에이전트) 접속용 개인 토큰
-- 토큰 원문은 저장하지 않고 SHA-256 해시만 둔다. MCP 서버(읽기 전용 계정)는 이 테이블을 직접 읽지 못하고
-- 검증 함수(verify_api_token)만 호출할 수 있다.

CREATE TABLE api_tokens (
    token_id      bigserial PRIMARY KEY,
    user_id       text NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    name          text NOT NULL,
    token_hash    text NOT NULL UNIQUE,
    prefix        text NOT NULL,                 -- 화면에서 토큰을 구분하기 위한 앞 8글자(원문 복원 불가)
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    last_used_at  timestamptz,
    revoked_at    timestamptz
);
CREATE INDEX api_tokens_user_idx ON api_tokens (user_id);

-- 유효한 토큰이면 (token_id, user_id, role), 아니면 0행. 폐기·만료·비활성 사용자는 모두 무효.
CREATE FUNCTION verify_api_token(p_hash text) RETURNS TABLE (token_id bigint, user_id text, role text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT t.token_id, u.user_id, u.role
      FROM api_tokens t JOIN users u ON u.user_id = t.user_id
     WHERE t.token_hash = p_hash AND t.revoked_at IS NULL AND t.expires_at > now() AND u.active
$$;

-- 마지막 사용 시각 기록(쓰기이므로 읽기 전용인 reader 가 아니라 감사 계정이 호출한다). 1분에 한 번만 갱신.
CREATE FUNCTION touch_api_token(p_id bigint) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
    UPDATE api_tokens SET last_used_at = now()
     WHERE token_id = p_id AND (last_used_at IS NULL OR last_used_at < now() - interval '1 minute')
$$;

REVOKE ALL ON FUNCTION verify_api_token(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION touch_api_token(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION verify_api_token(text) TO lawca_reader;
GRANT EXECUTE ON FUNCTION touch_api_token(bigint) TO lawca_audit;

-- 웹 API(발급·목록·폐기)
GRANT SELECT, INSERT, UPDATE ON api_tokens TO lawca_app;
GRANT USAGE ON SEQUENCE api_tokens_token_id_seq TO lawca_app;
