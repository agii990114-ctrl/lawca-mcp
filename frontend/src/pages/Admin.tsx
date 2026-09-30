import { useState, type FormEvent } from "react";
import { get, patch, post, put } from "../api";
import { useAuth } from "../auth";
import { ConfirmModal, ErrorNote, Field, Modal, Spinner, useToast, usePolling } from "../components/ui";
import { ROLES, type GroupRow, type Role, type UserRow } from "../types";

export default function Admin() {
  const [tab, setTab] = useState<"users" | "groups">("users");
  return (
    <div className="page">
      <h1>관리</h1>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "users"} className={tab === "users" ? "tab-on" : ""} onClick={() => setTab("users")}>사용자</button>
        <button role="tab" aria-selected={tab === "groups"} className={tab === "groups" ? "tab-on" : ""} onClick={() => setTab("groups")}>접근 그룹</button>
      </div>
      <div className="alert alert-info">관리자는 사용자와 접근 그룹을 관리하지만, 사건·문서 내용은 <strong>자신이 속한 접근 그룹 안에서만</strong> 볼 수 있습니다(이해충돌 방지벽 유지).</div>
      {tab === "users" ? <Users /> : <Groups />}
    </div>
  );
}

function TempPassword({ userId, password, onClose }: { userId: string; password: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Modal title="임시 비밀번호" onClose={onClose} footer={<button className="btn btn-primary" onClick={onClose}>확인했습니다</button>}>
      <p><strong>{userId}</strong> 님의 임시 비밀번호입니다. <strong>이 창을 닫으면 다시 볼 수 없습니다.</strong> 안전한 방법으로 전달하세요. 첫 로그인 때 변경하게 됩니다.</p>
      <div className="secret"><code aria-label="임시 비밀번호">{password}</code>
        <button className="btn" onClick={async () => { await navigator.clipboard?.writeText(password); setCopied(true); }}>{copied ? "복사됨" : "복사"}</button></div>
    </Modal>
  );
}

function Users() {
  const { me } = useAuth();
  const toast = useToast();
  const users = usePolling<UserRow[]>(() => get("/admin/users"), [], () => null);
  const groups = usePolling<GroupRow[]>(() => get("/admin/groups"), [], () => null);
  const [creating, setCreating] = useState(false);
  const [editGroups, setEditGroups] = useState<UserRow | null>(null);
  const [resetFor, setResetFor] = useState<UserRow | null>(null);
  const [temp, setTemp] = useState<{ userId: string; password: string } | null>(null);

  const change = async (u: UserRow, body: Record<string, unknown>) => {
    try { await patch(`/admin/users/${u.user_id}`, body); toast("변경했습니다."); users.reload(); }
    catch (e) { toast(e instanceof Error ? e.message : "변경하지 못했습니다.", "error"); users.reload(); }
  };

  return (
    <>
      <div className="row end"><button className="btn btn-primary" onClick={() => setCreating(true)}>사용자 추가</button></div>
      <ErrorNote error={users.error} />
      <div className="card flush">
        {!users.data ? <div className="pad"><Spinner /></div> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>아이디</th><th>이름</th><th>역할</th><th>접근 그룹</th><th>상태</th><th /></tr></thead>
              <tbody>
                {users.data.map((u) => {
                  const self = u.user_id === me?.user_id;
                  return (
                    <tr key={u.user_id} className={u.active ? "" : "inactive"}>
                      <td><strong>{u.user_id}</strong>{self && <span className="tag">나</span>}</td>
                      <td>{u.name}</td>
                      <td><select value={u.role} disabled={self} aria-label={`${u.user_id} 역할`} onChange={(e) => change(u, { role: e.target.value })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></td>
                      <td className="wrap">{u.groups.length ? u.groups.map((g) => <span key={g} className="tag">{g}</span>) : <span className="muted">없음</span>}</td>
                      <td>{u.active ? "사용 중" : "비활성"}</td>
                      <td className="nowrap num">
                        <button className="btn" onClick={() => setEditGroups(u)}>그룹</button>
                        <button className="btn" onClick={() => setResetFor(u)}>비밀번호 초기화</button>
                        <button className="btn btn-ghost" disabled={self} onClick={() => change(u, { active: !u.active })}>{u.active ? "비활성화" : "활성화"}</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {creating && <CreateUser groups={groups.data ?? []} onClose={() => setCreating(false)}
        onDone={(userId, password) => { setCreating(false); users.reload(); setTemp({ userId, password }); }} />}
      {editGroups && <EditGroups user={editGroups} groups={groups.data ?? []} onClose={() => setEditGroups(null)} onDone={() => { setEditGroups(null); users.reload(); groups.reload(); toast("접근 그룹을 변경했습니다. 즉시 적용됩니다."); }} />}
      {resetFor && <ConfirmModal title="비밀번호를 초기화할까요?" confirmLabel="초기화" danger onClose={() => setResetFor(null)}
        message={`${resetFor.user_id} 님의 비밀번호를 임시 비밀번호로 바꾸고 모든 로그인을 종료합니다.`}
        onConfirm={async () => {
          try { const r = await post<{ temporary_password: string }>(`/admin/users/${resetFor.user_id}/reset-password`); setTemp({ userId: resetFor.user_id, password: r.temporary_password }); }
          catch (e) { toast(e instanceof Error ? e.message : "초기화하지 못했습니다.", "error"); }
          setResetFor(null);
        }} />}
      {temp && <TempPassword userId={temp.userId} password={temp.password} onClose={() => setTemp(null)} />}
    </>
  );
}

function GroupChecks({ groups, value, onChange }: { groups: GroupRow[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <fieldset className="radios"><legend>접근 그룹</legend>
      {groups.length === 0 && <span className="muted">접근 그룹이 없습니다. 먼저 ‘접근 그룹’ 탭에서 만드세요.</span>}
      {groups.map((g) => (
        <label key={g.group_id}><input type="checkbox" aria-label={`접근 그룹 ${g.group_id}`} checked={value.includes(g.group_id)}
          onChange={(e) => onChange(e.target.checked ? [...value, g.group_id] : value.filter((x) => x !== g.group_id))} />
          {g.group_id} {g.description && <span className="muted">— {g.description}</span>}</label>
      ))}
    </fieldset>
  );
}

function CreateUser({ groups, onClose, onDone }: { groups: GroupRow[]; onClose: () => void; onDone: (userId: string, password: string) => void }) {
  const [f, setF] = useState({ user_id: "", name: "", role: "변호사" as Role });
  const [sel, setSel] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      const r = await post<{ temporary_password: string }>("/admin/users", { ...f, groups: sel });
      onDone(f.user_id, r.temporary_password);
    } catch (err) { setError(err instanceof Error ? err.message : "추가하지 못했습니다."); } finally { setBusy(false); }
  };
  return (
    <Modal title="사용자 추가" onClose={onClose}>
      <form onSubmit={submit}>
        <ErrorNote error={error} />
        <Field label="아이디" htmlFor="nu" hint="영문 소문자·숫자·._- 3~32자"><input id="nu" required pattern="[a-z0-9][a-z0-9_.\-]{2,31}" value={f.user_id} onChange={(e) => setF({ ...f, user_id: e.target.value })} /></Field>
        <Field label="이름" htmlFor="nn"><input id="nn" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="역할" htmlFor="nr" hint="관리자: 사용자·그룹 관리 / 변호사: 조회·업로드·삭제 / 사무원: 조회·업로드">
          <select id="nr" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as Role })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></Field>
        <GroupChecks groups={groups} value={sel} onChange={setSel} />
        <div className="row end"><button type="button" className="btn" onClick={onClose}>취소</button><button className="btn btn-primary" disabled={busy}>{busy ? "추가 중…" : "추가"}</button></div>
      </form>
    </Modal>
  );
}

function EditGroups({ user, groups, onClose, onDone }: { user: UserRow; groups: GroupRow[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [sel, setSel] = useState<string[]>(user.groups);
  const [busy, setBusy] = useState(false);
  return (
    <Modal title={`${user.user_id} 접근 그룹`} onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>취소</button>
      <button className="btn btn-primary" disabled={busy} onClick={async () => {
        setBusy(true);
        try { await put(`/admin/users/${user.user_id}/groups`, { groups: sel }); onDone(); }
        catch (e) { toast(e instanceof Error ? e.message : "변경하지 못했습니다.", "error"); setBusy(false); }
      }}>저장</button></>}>
      <p className="muted">그룹에서 빼면 해당 그룹의 사건·문서를 즉시 볼 수 없게 됩니다. 이해충돌 검토 후 변경하세요.</p>
      <GroupChecks groups={groups} value={sel} onChange={setSel} />
    </Modal>
  );
}

function Groups() {
  const toast = useToast();
  const groups = usePolling<GroupRow[]>(() => get("/admin/groups"), [], () => null);
  const [id, setId] = useState("");
  const [desc, setDesc] = useState("");
  const [error, setError] = useState<string | null>(null);
  const add = async (e: FormEvent) => {
    e.preventDefault(); setError(null);
    try { await post("/admin/groups", { group_id: id, description: desc }); toast("접근 그룹을 만들었습니다."); setId(""); setDesc(""); groups.reload(); }
    catch (err) { setError(err instanceof Error ? err.message : "만들지 못했습니다."); }
  };
  return (
    <>
      <form className="card row wrap-row" onSubmit={add}>
        <Field label="그룹 ID" htmlFor="gi" hint="영문·숫자·-_ 최대 32자"><input id="gi" required pattern="[A-Za-z0-9][A-Za-z0-9_\-]{0,31}" value={id} onChange={(e) => setId(e.target.value)} /></Field>
        <Field label="설명" htmlFor="gd"><input id="gd" value={desc} onChange={(e) => setDesc(e.target.value)} maxLength={200} /></Field>
        <button className="btn btn-primary self-end">그룹 만들기</button>
      </form>
      <ErrorNote error={error ?? groups.error} />
      <div className="card flush">
        {!groups.data ? <div className="pad"><Spinner /></div> : (
          <div className="table-wrap"><table>
            <thead><tr><th>그룹 ID</th><th>설명</th><th className="num">구성원</th></tr></thead>
            <tbody>{groups.data.map((g) => <tr key={g.group_id}><td><strong>{g.group_id}</strong></td><td>{g.description}</td><td className="num">{g.members}</td></tr>)}</tbody>
          </table></div>
        )}
      </div>
    </>
  );
}
