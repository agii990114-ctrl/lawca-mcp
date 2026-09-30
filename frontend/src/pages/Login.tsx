import { useState, type FormEvent } from "react";
import { ApiError } from "../api";
import { useAuth } from "../auth";
import { ErrorNote } from "../components/ui";

export default function Login() {
  const { login } = useAuth();
  const [userId, setUserId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(userId.trim(), password);
    } catch (err) {
      setPassword("");
      setError(err instanceof ApiError || err instanceof Error ? err.message : "로그인하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login-wrap">
      <form className="card login-card" onSubmit={submit} aria-labelledby="login-title">
        <h1 id="login-title">법무 문서 관리</h1>
        <p className="muted">사무소에서 발급받은 계정으로 로그인하세요.</p>
        <ErrorNote error={error} />
        <div className="field">
          <label htmlFor="user_id">아이디</label>
          <input id="user_id" autoComplete="username" autoFocus required value={userId} onChange={(e) => setUserId(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="password">비밀번호</label>
          <input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <button className="btn btn-primary btn-block" type="submit" disabled={busy || !userId || !password}>
          {busy ? "확인 중…" : "로그인"}
        </button>
        <p className="hint">비밀번호를 잊었거나 계정이 잠기면 관리자에게 초기화를 요청하세요.</p>
      </form>
    </main>
  );
}
