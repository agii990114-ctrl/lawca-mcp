import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { get, patch, post, qs } from "../api";
import { useAuth } from "../auth";
import { Empty, ErrorNote, Field, Modal, Spinner, useToast, usePolling } from "../components/ui";
import type { Matter } from "../types";

const CASE_NO = /^\d{4}(가합|가단|가소|나|다|라|마|머|드단|드합|르|카합|카단|카기|고단|고합|고정|구합|구단|누)\d{2,7}$/;
const RESULTS = ["진행중", "승소", "패소", "일부승소"];

export default function Matters() {
  const { me, can } = useAuth();
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Matter | null>(null);
  const { data, error, loading, reload } = usePolling<{ total: number; items: Matter[] }>(
    () => get(`/matters${qs({ q, limit: 100 })}`), [q], () => null);

  return (
    <div className="page">
      <div className="row between">
        <h1>사건</h1>
        {can("matters.create") && (me?.groups.length ?? 0) > 0 && <button className="btn btn-primary" onClick={() => setCreating(true)}>사건 등록</button>}
      </div>
      <div className="row">
        <input type="search" className="grow" placeholder="사건번호·당사자·사건명 검색" value={q} onChange={(e) => setQ(e.target.value)} aria-label="사건 검색" />
      </div>
      <ErrorNote error={error} />
      <div className="card flush">
        {loading && !data ? <div className="pad"><Spinner /></div> : (data?.items.length ?? 0) === 0 ? (
          <Empty>{q ? "검색 결과가 없습니다." : "접근할 수 있는 사건이 없습니다."}</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>사건번호</th><th>사건명</th><th>원고</th><th>피고</th><th>우리 측</th><th>결과</th><th>그룹</th><th>관련자</th><th className="num">문서</th></tr></thead>
              <tbody>
                {data!.items.map((m) => (
                  <tr key={m.matter_id}>
                    <td className="nowrap"><strong>{m.case_no}</strong><div className="hint">{m.matter_id}</div></td>
                    <td>{m.case_type}</td><td className="wrap">{m.plaintiff}</td><td className="wrap">{m.defendant}</td>
                    <td>{m.our_side}</td><td>{m.result}</td><td>{m.access_group}</td>
                    <td className="nowrap">
                      {can("matters.create") ? (
                        <button className="btn" onClick={() => setEditing(m)} aria-label={`${m.case_no} 관련자 관리`}>
                          {m.related_names.length > 0 ? `${m.related_names.length}명` : "등록"}
                        </button>
                      ) : (m.related_names.length > 0 ? `${m.related_names.length}명` : "-")}
                    </td>
                    <td className="num">{m.doc_count > 0 ? <Link to={`/documents?matter_id=${m.matter_id}`}>{m.doc_count}</Link> : 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {data && <p className="hint">{data.total}건 중 {data.items.length}건 표시 · 접근 권한이 있는 사건만 보입니다.</p>}
      {creating && <CreateMatter onClose={() => setCreating(false)} onDone={() => { setCreating(false); reload(); }} />}
      {editing && <RelatedNames matter={editing} onClose={() => setEditing(null)} onDone={() => { setEditing(null); reload(); }} />}
    </div>
  );
}

function CreateMatter({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { me } = useAuth();
  const toast = useToast();
  const [f, setF] = useState({ case_no: "", case_type: "", court: "", plaintiff: "", defendant: "", our_side: "원고", attorney_name: "", access_group: me?.groups[0] ?? "", result: "진행중" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((c) => ({ ...c, [k]: e.target.value }));
  const noSpaceCase = f.case_no.replace(/\s+/g, "");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!CASE_NO.test(noSpaceCase)) return setError("사건번호 형식이 올바르지 않습니다. 예: 2025가합12345");
    setBusy(true); setError(null);
    try {
      await post("/matters", { ...f, case_no: noSpaceCase, court: f.court || null, attorney_name: f.attorney_name || null });
      toast("사건을 등록했습니다.");
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "등록하지 못했습니다.");
    } finally { setBusy(false); }
  };

  return (
    <Modal title="사건 등록" onClose={onClose}>
      <form onSubmit={submit}>
        <ErrorNote error={error} />
        <Field label="사건번호" htmlFor="cn" hint="연도+부호+번호. 예: 2025가합12345 (공백은 자동 제거)"><input id="cn" required value={f.case_no} onChange={set("case_no")} placeholder="2025가합12345" /></Field>
        <div className="grid-2">
          <Field label="사건명" htmlFor="ct"><input id="ct" required value={f.case_type} onChange={set("case_type")} placeholder="대여금" /></Field>
          <Field label="법원" htmlFor="co"><input id="co" value={f.court} onChange={set("court")} placeholder="서울중앙지방법원" /></Field>
          <Field label="원고" htmlFor="pl"><input id="pl" required value={f.plaintiff} onChange={set("plaintiff")} /></Field>
          <Field label="피고" htmlFor="df"><input id="df" required value={f.defendant} onChange={set("defendant")} /></Field>
          <Field label="우리 측" htmlFor="os"><select id="os" value={f.our_side} onChange={set("our_side")}><option>원고</option><option>피고</option></select></Field>
          <Field label="담당 변호사" htmlFor="at"><input id="at" value={f.attorney_name} onChange={set("attorney_name")} /></Field>
          <Field label="접근 그룹" htmlFor="ag" hint="이 그룹 구성원만 사건과 문서를 볼 수 있습니다."><select id="ag" value={f.access_group} onChange={set("access_group")}>{me?.groups.map((g) => <option key={g}>{g}</option>)}</select></Field>
          <Field label="결과" htmlFor="rs"><select id="rs" value={f.result} onChange={set("result")}>{RESULTS.map((r) => <option key={r}>{r}</option>)}</select></Field>
        </div>
        <div className="row end">
          <button type="button" className="btn" onClick={onClose}>취소</button>
          <button className="btn btn-primary" disabled={busy}>{busy ? "등록 중…" : "등록"}</button>
        </div>
      </form>
    </Modal>
  );
}

function parseNames(text: string): string[] {
  return [...new Set(text.split(/[\n,]/).map((x) => x.replace(/\s+/g, " ").trim()).filter(Boolean))];
}

function RelatedNames({ matter, onClose, onDone }: { matter: Matter; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [text, setText] = useState(matter.related_names.join("\n"));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const names = parseNames(text);
  const tooShort = names.filter((n) => n.length < 2 || n.length > 30);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (tooShort.length > 0) return setError(`이름은 2~30자여야 합니다: ${tooShort.join(", ")}`);
    if (names.length > 50) return setError("관련자는 50명까지 등록할 수 있습니다.");
    setBusy(true); setError(null);
    try {
      await patch(`/matters/${matter.matter_id}`, { related_names: names });
      toast(names.length > 0 ? `관련자 ${names.length}명을 저장했습니다.` : "관련자를 모두 지웠습니다.");
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "저장하지 못했습니다.");
    } finally { setBusy(false); }
  };

  return (
    <Modal title={`관련자 관리 — ${matter.case_no}`} onClose={onClose}>
      <form onSubmit={submit}>
        <ErrorNote error={error} />
        <p className="muted">
          AI에게 전달되는 참고 서면에서 아래 이름이 <strong>[관련자]</strong>로 가려집니다. 증인, 상대방 대표이사·직원처럼 <strong>당사자가 아닌 사람</strong>의 이름을 한 줄에 하나씩 적으세요.
          당사자와 담당 변호사는 자동으로 가려지므로 적지 않아도 됩니다.
        </p>
        <Field label="관련자 이름" htmlFor="rn" hint={`${names.length}명 · 2자 이상. 쉼표나 줄바꿈으로 구분합니다. 성과 이름이 붙어 있는 표기(예: 김철수)로 적어야 본문에서 찾아 가립니다.`}>
          <textarea id="rn" rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder={"예)\n여은철\n하태율"} />
        </Field>
        <div className="alert alert-warn">이 목록은 같은 이름이 나오는 모든 서면에서 가려집니다. 이름이 흔한 단어와 같으면 의미가 바뀔 수 있으니 이름 전체를 적으세요.</div>
        <div className="row end">
          <button type="button" className="btn" onClick={onClose}>취소</button>
          <button className="btn btn-primary" disabled={busy}>{busy ? "저장 중…" : "저장"}</button>
        </div>
      </form>
    </Modal>
  );
}
