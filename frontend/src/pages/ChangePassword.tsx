import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { post } from "../api";
import { useAuth } from "../auth";
import { ErrorNote, Field, useToast } from "../components/ui";

const MIN = 10;

function problem(pw: string, userId: string): string | null {
  if (pw.length < MIN) return `비밀번호는 ${MIN}자 이상이어야 합니다.`;
  if (userId && pw.toLowerCase().includes(userId.toLowerCase())) return "비밀번호에 아이디를 포함할 수 없습니다.";
  const kinds = [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(pw)).length;
  if (kinds < 2) return "영문 대소문자·숫자·기호 중 2종류 이상을 섞어 주세요.";
  return null;
}

export default function ChangePassword() {
  const { me, refresh } = useAuth();
  const nav = useNavigate();
  const toast = useToast();
  const [cur, setCur] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const forced = !!me?.must_change_password;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const p = problem(next, me?.user_id ?? "");
    if (p) return setError(p);
    if (next !== again) return setError("새 비밀번호가 서로 다릅니다.");
    setBusy(true);
    setError(null);
    try {
      await post("/auth/change-password", { current_password: cur, new_password: next });
      await refresh();
      toast("비밀번호를 변경했습니다. 다른 기기의 로그인은 종료되었습니다.");
      nav("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "변경하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={forced ? "login-wrap" : ""}>
      <form className="card form-card" onSubmit={submit}>
        <h1>{forced ? "비밀번호를 먼저 변경해 주세요" : "비밀번호 변경"}</h1>
        {forced && <p className="muted">임시 비밀번호로 로그인했습니다. 계속하려면 새 비밀번호를 설정해야 합니다.</p>}
        <ErrorNote error={error} />
        <Field label="현재 비밀번호" htmlFor="cur"><input id="cur" type="password" autoComplete="current-password" required value={cur} onChange={(e) => setCur(e.target.value)} /></Field>
        <Field label="새 비밀번호" htmlFor="next" hint={`${MIN}자 이상, 영문 대소문자·숫자·기호 중 2종류 이상`}>
          <input id="next" type="password" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} />
        </Field>
        <Field label="새 비밀번호 확인" htmlFor="again"><input id="again" type="password" autoComplete="new-password" required value={again} onChange={(e) => setAgain(e.target.value)} /></Field>
        <button className="btn btn-primary" type="submit" disabled={busy || !cur || !next || !again}>{busy ? "변경 중…" : "비밀번호 변경"}</button>
      </form>
    </div>
  );
}
