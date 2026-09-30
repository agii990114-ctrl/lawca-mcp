import { useState, type FormEvent } from "react";
import { del, get, post } from "../api";
import { ConfirmModal, Empty, ErrorNote, Field, Modal, Spinner, fmtTime, useToast, usePolling } from "../components/ui";

interface Token {
  token_id: number;
  name: string;
  prefix: string;
  created_at: string;
  expires_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}
interface Created extends Token {
  token: string;
}

function state(t: Token): { label: string; cls: string } {
  if (t.revoked_at) return { label: "폐기됨", cls: "badge-rejected" };
  if (new Date(t.expires_at).getTime() <= Date.now()) return { label: "만료됨", cls: "badge-duplicate" };
  return { label: "사용 중", cls: "badge-done" };
}

export default function AiAccess() {
  const toast = useToast();
  const list = usePolling<Token[]>(() => get("/tokens"), [], () => null);
  const [name, setName] = useState("");
  const [days, setDays] = useState("30");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Created | null>(null);
  const [revoke, setRevoke] = useState<Token | null>(null);
  const url = `${window.location.origin}/mcp`;

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const t = await post<Created>("/tokens", { name: name.trim(), days: Number(days) });
      setCreated(t); setName(""); list.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "발급하지 못했습니다.");
    } finally { setBusy(false); }
  };

  return (
    <div className="page">
      <h1>AI 연결</h1>
      <p className="muted">
        Claude 같은 AI가 <strong>내 권한으로</strong> 사내 서면을 검색·조회해 준비서면 초안 작성을 돕게 하려면 여기서 개인 토큰을 발급해 AI 프로그램에 등록합니다.
        AI는 내가 볼 수 있는 접근 그룹의 문서만 다루고, 참고 서면의 당사자·금액·사건번호는 가려진 상태로 전달됩니다. 모든 조회는 감사 로그에 남습니다.
      </p>
      <div className="alert alert-warn">
        토큰은 <strong>내 계정의 열쇠</strong>입니다. 다른 사람과 공유하거나 메신저·이메일에 붙여 넣지 마세요. 유출이 의심되면 바로 폐기하세요.
        비밀번호를 초기화하거나 계정이 비활성화되면 모든 토큰이 자동으로 폐기됩니다.
      </div>

      <form className="card" onSubmit={create}>
        <h2>새 토큰 발급</h2>
        <ErrorNote error={error} />
        <div className="grid-2">
          <Field label="이름" htmlFor="tn" hint="어디에 쓰는 토큰인지 알아볼 수 있게. 예: 내 노트북 Claude Code"><input id="tn" required maxLength={60} value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="유효 기간" htmlFor="td" hint="만료되면 새로 발급받아야 합니다. 최대 90일.">
            <select id="td" value={days} onChange={(e) => setDays(e.target.value)}><option value="7">7일</option><option value="30">30일</option><option value="90">90일</option></select>
          </Field>
        </div>
        <div className="row end"><button className="btn btn-primary" disabled={busy || !name.trim()}>{busy ? "발급 중…" : "토큰 발급"}</button></div>
      </form>

      <div className="card flush">
        {!list.data ? <div className="pad"><Spinner /></div> : list.data.length === 0 ? <Empty>발급한 토큰이 없습니다.</Empty> : (
          <div className="table-wrap"><table>
            <thead><tr><th>이름</th><th>토큰</th><th>발급</th><th>만료</th><th>마지막 사용</th><th>상태</th><th /></tr></thead>
            <tbody>{list.data.map((t) => {
              const s = state(t);
              return (
                <tr key={t.token_id}>
                  <td className="wrap"><strong>{t.name}</strong></td>
                  <td className="nowrap"><code>{t.prefix}…</code></td>
                  <td className="nowrap">{fmtTime(t.created_at)}</td>
                  <td className="nowrap">{fmtTime(t.expires_at)}</td>
                  <td className="nowrap">{t.last_used_at ? fmtTime(t.last_used_at) : "사용 기록 없음"}</td>
                  <td><span className={`badge ${s.cls}`}>{s.label}</span></td>
                  <td className="num">{s.label === "사용 중" && <button className="btn" onClick={() => setRevoke(t)}>폐기</button>}</td>
                </tr>
              );
            })}</tbody>
          </table></div>
        )}
      </div>
      <ErrorNote error={list.error} />

      {created && <CreatedModal t={created} url={url} onClose={() => setCreated(null)} />}
      {revoke && (
        <ConfirmModal title="토큰을 폐기할까요?" danger confirmLabel="폐기" onClose={() => setRevoke(null)}
          message={`‘${revoke.name}’ 토큰을 폐기하면 이 토큰을 쓰는 AI 프로그램은 즉시 접속할 수 없게 됩니다.`}
          onConfirm={async () => {
            try { await del(`/tokens/${revoke.token_id}`); toast("토큰을 폐기했습니다."); list.reload(); }
            catch (e) { toast(e instanceof Error ? e.message : "폐기하지 못했습니다.", "error"); }
            setRevoke(null);
          }} />
      )}
    </div>
  );
}

function CopyBox({ label, text }: { label: string; text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="field">
      <label>{label}</label>
      <div className="secret">
        <code aria-label={label}>{text}</code>
        <button type="button" className="btn" onClick={async () => { await navigator.clipboard?.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 2000); }}>{copied ? "복사됨" : "복사"}</button>
      </div>
    </div>
  );
}

function CreatedModal({ t, url, onClose }: { t: Created; url: string; onClose: () => void }) {
  const cli = `claude mcp add --transport http lawca ${url} --header "Authorization: Bearer ${t.token}"`;
  const json = JSON.stringify({ mcpServers: { lawca: { type: "http", url, headers: { Authorization: `Bearer ${t.token}` } } } }, null, 2);
  return (
    <Modal title="토큰이 발급되었습니다" onClose={onClose} wide footer={<button className="btn btn-primary" onClick={onClose}>안전한 곳에 저장했습니다</button>}>
      <div className="alert alert-warn"><strong>이 창을 닫으면 토큰을 다시 볼 수 없습니다.</strong> 잃어버리면 새로 발급받으세요.</div>
      <CopyBox label="토큰" text={t.token} />
      <h3>Claude Code에 등록</h3>
      <CopyBox label="터미널에서 실행" text={cli} />
      <h3>설정 파일(.mcp.json)로 등록</h3>
      <pre className="preview" tabIndex={0} aria-label=".mcp.json 예시">{json}</pre>
      <p className="hint">연결되면 AI에게 “내 사건 목록을 보여줘”, “대여금 소멸시효 항변에 대응한 준비서면을 찾아줘”처럼 요청할 수 있습니다. AI가 쓴 글은 항상 변호사가 검토해야 하는 초안입니다.</p>
    </Modal>
  );
}
