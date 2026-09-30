import { useMemo, useRef, useState, type DragEvent } from "react";
import { Link } from "react-router-dom";
import { uploadForm } from "../api";
import { useAuth } from "../auth";
import { MatterPicker } from "../components/MatterPicker";
import { Empty, ErrorNote, Field, bytes } from "../components/ui";
import { DOC_KINDS, STATUS_LABEL, type JobStatus, type UploadResult } from "../types";

const ALLOWED = [".pdf", ".hwp", ".hwpx", ".docx", ".png", ".jpg", ".jpeg", ".webp", ".zip"];
const MAX_FILE = 200 * 1024 * 1024;       // 파일 하나(서버 한도와 동일)
const REQ_FILES = 100;                    // 요청 하나당 파일 수
const REQ_BYTES = 300 * 1024 * 1024;      // 요청 하나당 용량
const ext = (n: string) => (n.lastIndexOf(".") >= 0 ? n.slice(n.lastIndexOf(".")).toLowerCase() : "");

/** 대량 업로드를 여러 요청으로 나눈다. ZIP은 쪼갤 수 없으므로 단독으로 보낸다. */
function plan(files: File[]): File[][] {
  const out: File[][] = [];
  let cur: File[] = [], size = 0;
  const flush = () => { if (cur.length) out.push(cur); cur = []; size = 0; };
  for (const f of files) {
    if (ext(f.name) === ".zip") { flush(); out.push([f]); continue; }
    if (cur.length >= REQ_FILES || size + f.size > REQ_BYTES) flush();
    cur.push(f); size += f.size;
  }
  flush();
  return out;
}

interface Summary {
  batchId: number;
  counts: Partial<Record<JobStatus, number>>;
  problems: { name: string; status: JobStatus; reason: string | null }[];
  skipped: number;
}

export default function Upload() {
  const { me } = useAuth();
  const groups = me?.groups ?? [];
  const [group, setGroup] = useState(groups[0] ?? "");
  const [mode, setMode] = useState<"auto" | "pick">("auto");
  const [matterId, setMatterId] = useState("");
  const [kind, setKind] = useState("");
  const [tags, setTags] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [excluded, setExcluded] = useState<string[]>([]);
  const [drag, setDrag] = useState(false);
  const [phase, setPhase] = useState<"idle" | "uploading" | "done">("idle");
  const [progress, setProgress] = useState({ sent: 0, total: 0, req: 0, reqs: 0 });
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const dirInput = useRef<HTMLInputElement>(null);

  const totalSize = useMemo(() => files.reduce((s, f) => s + f.size, 0), [files]);
  const zipCount = files.filter((f) => ext(f.name) === ".zip").length;

  const addFiles = (list: FileList | File[]) => {
    const ok: File[] = [], bad: string[] = [];
    const have = new Set(files.map((f) => `${f.name}:${f.size}:${f.lastModified}`));
    for (const f of Array.from(list)) {
      if (!ALLOWED.includes(ext(f.name))) bad.push(`${f.name} (지원하지 않는 형식)`);
      else if (f.size > MAX_FILE) bad.push(`${f.name} (${bytes(f.size)}: 파일당 최대 ${bytes(MAX_FILE)})`);
      else if (f.size === 0) bad.push(`${f.name} (빈 파일)`);
      else if (!have.has(`${f.name}:${f.size}:${f.lastModified}`)) ok.push(f);
    }
    setFiles((cur) => [...cur, ...ok]);
    setExcluded((cur) => [...cur, ...bad]);
    setSummary(null);
    setPhase("idle");
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  };

  const start = async () => {
    setError(null);
    setSummary(null);
    const requests = plan(files);
    const total = files.reduce((s, f) => s + f.size, 0);
    setPhase("uploading");
    setProgress({ sent: 0, total, req: 0, reqs: requests.length });
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let batchId: number | null = null, sent = 0, skipped = 0;
    const counts: Summary["counts"] = {};
    const problems: Summary["problems"] = [];
    try {
      for (let i = 0; i < requests.length; i++) {
        const form = new FormData();
        form.set("access_group", group);
        if (mode === "pick" && matterId) form.set("matter_id", matterId);
        if (kind) form.set("doc_kind", kind);
        if (tags.trim()) form.set("issue_tags", tags);
        if (batchId !== null) form.set("batch_id", String(batchId));
        for (const f of requests[i]) form.append("files", f, f.name);
        const reqSize = requests[i].reduce((s, f) => s + f.size, 0);
        const r = await uploadForm<UploadResult>("/uploads", form,
          (loaded, tot) => setProgress({ sent: sent + (reqSize * loaded) / (tot || 1), total, req: i + 1, reqs: requests.length }),
          ctrl.signal);
        batchId = r.batch_id;
        sent += reqSize;
        skipped += r.skipped_entries;
        for (const j of r.jobs) if (j.status === "rejected" || j.status === "duplicate") problems.push({ name: j.name, status: j.status, reason: j.reason });
        // 요청마다 돌아오는 counts는 묶음 전체 누계이므로 마지막 값을 쓴다
        Object.keys(counts).forEach((k) => delete counts[k as JobStatus]);
        Object.assign(counts, r.counts);
      }
      setSummary({ batchId: batchId!, counts, problems, skipped });
      setFiles([]);
      setExcluded([]);
      setPhase("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : "업로드하지 못했습니다.");
      if (batchId !== null) setSummary({ batchId, counts, problems, skipped });
      setPhase("idle");
    } finally {
      abortRef.current = null;
    }
  };

  if (groups.length === 0) {
    return (
      <div className="card">
        <h1>문서 업로드</h1>
        <Empty>소속된 접근 그룹이 없어 업로드할 수 없습니다. 관리자에게 접근 그룹 배정을 요청하세요.</Empty>
      </div>
    );
  }

  const uploading = phase === "uploading";
  const needMatter = mode === "pick" && !matterId;
  const pct = progress.total ? Math.min(100, Math.round((progress.sent / progress.total) * 100)) : 0;

  return (
    <div className="page">
      <h1>문서 업로드</h1>
      <p className="muted">PDF·한글(HWP/HWPX)·워드(DOCX)·스캔 이미지를 낱개로 올리거나 ZIP으로 한 번에 올릴 수 있습니다. 올린 문서는 사내 서버에서 텍스트로 변환·색인되며 외부로 전송되지 않습니다.</p>

      <section className="card">
        <h2>1. 저장 위치</h2>
        <div className="grid-2">
          <Field label="접근 그룹" htmlFor="grp" hint="이 그룹 구성원만 문서를 볼 수 있습니다.">
            <select id="grp" value={group} disabled={uploading} onChange={(e) => { setGroup(e.target.value); setMatterId(""); }}>
              {groups.map((g) => <option key={g} value={g}>{g}</option>)}
            </select>
          </Field>
          <Field label="문서 종류" htmlFor="kind" hint="비워 두면 문서 내용에서 자동으로 판단합니다.">
            <select id="kind" value={kind} disabled={uploading} onChange={(e) => setKind(e.target.value)}>
              <option value="">자동 감지</option>
              {DOC_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </Field>
        </div>
        <fieldset className="radios" disabled={uploading}>
          <legend>사건 지정</legend>
          <label><input type="radio" name="mode" checked={mode === "auto"} onChange={() => setMode("auto")} />
            문서 안의 사건번호로 자동 지정 <span className="muted">(찾지 못한 문서는 ‘검토 필요’로 남아 나중에 지정합니다)</span></label>
          <label><input type="radio" name="mode" checked={mode === "pick"} onChange={() => setMode("pick")} />
            이 사건에 모두 올리기</label>
        </fieldset>
        {mode === "pick" && <Field label="사건" htmlFor="matter"><MatterPicker id="matter" group={group} value={matterId} onChange={(id) => setMatterId(id)} /></Field>}
        <Field label="쟁점 태그 (선택)" htmlFor="tags" hint="쉼표로 구분합니다. 예: 소멸시효, 채무 승인. 올리는 모든 문서에 붙습니다.">
          <input id="tags" value={tags} disabled={uploading} onChange={(e) => setTags(e.target.value)} placeholder="소멸시효, 채무 승인" />
        </Field>
      </section>

      <section className="card">
        <h2>2. 파일 선택</h2>
        <div
          className={`dropzone ${drag ? "drag" : ""}`} role="button" tabIndex={0} aria-label="파일을 끌어다 놓거나 눌러서 선택"
          onClick={() => !uploading && fileInput.current?.click()}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && !uploading && (e.preventDefault(), fileInput.current?.click())}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={onDrop}>
          <strong>여기에 파일을 끌어다 놓으세요</strong>
          <span>또는 눌러서 선택 · 지원: PDF, HWP, HWPX, DOCX, PNG, JPG, WEBP, ZIP</span>
        </div>
        <input ref={fileInput} type="file" multiple hidden accept={ALLOWED.join(",")} data-testid="file-input"
          onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
        <input ref={dirInput} type="file" multiple hidden data-testid="dir-input"
          {...({ webkitdirectory: "" } as Record<string, string>)}
          onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
        <div className="row">
          <button className="btn" onClick={() => fileInput.current?.click()} disabled={uploading}>파일 선택</button>
          <button className="btn" onClick={() => dirInput.current?.click()} disabled={uploading}>폴더 선택</button>
          {files.length > 0 && <button className="btn btn-ghost" onClick={() => { setFiles([]); setExcluded([]); }} disabled={uploading}>목록 비우기</button>}
        </div>

        {excluded.length > 0 && (
          <div className="alert alert-warn" role="alert">
            <strong>{excluded.length}개는 제외했습니다.</strong>
            <ul>{excluded.slice(0, 8).map((x, i) => <li key={i}>{x}</li>)}</ul>
            {excluded.length > 8 && <span>외 {excluded.length - 8}개</span>}
          </div>
        )}

        {files.length > 0 ? (
          <>
            <p className="summary">선택한 파일 <strong>{files.length}</strong>개 · {bytes(totalSize)}{zipCount > 0 && ` · ZIP ${zipCount}개(내부 파일은 서버에서 펼쳐 개별 처리)`}
              {" "}· {plan(files).length}번에 나누어 전송</p>
            <div className="table-wrap short">
              <table>
                <thead><tr><th>파일명</th><th className="num">크기</th><th /></tr></thead>
                <tbody>
                  {files.slice(0, 200).map((f, i) => (
                    <tr key={`${f.name}${i}`}>
                      <td className="wrap">{f.name}</td><td className="num">{bytes(f.size)}</td>
                      <td className="num"><button className="btn btn-ghost" disabled={uploading} aria-label={`${f.name} 제거`}
                        onClick={() => setFiles((cur) => cur.filter((_, j) => j !== i))}>제거</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {files.length > 200 && <div className="hint pad">처음 200개만 표시합니다(전체 {files.length}개가 업로드됩니다).</div>}
            </div>
          </>
        ) : <p className="muted">선택한 파일이 없습니다.</p>}
      </section>

      <ErrorNote error={error} />

      {uploading && (
        <div className="card" aria-live="polite">
          <div className="progress-head"><span>업로드 중… ({progress.req}/{progress.reqs}번째 전송)</span><span>{pct}% · {bytes(progress.sent)} / {bytes(progress.total)}</span></div>
          <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><div style={{ width: `${pct}%` }} /></div>
          <button className="btn" onClick={() => abortRef.current?.abort()}>취소</button>
          <p className="hint">취소해도 이미 전송된 파일은 처리됩니다. 창을 닫지 마세요.</p>
        </div>
      )}

      <div className="row end">
        <button className="btn btn-primary btn-lg" onClick={start} disabled={uploading || files.length === 0 || needMatter || !group}>
          {uploading ? "업로드 중…" : `업로드 시작 (${files.length}개)`}
        </button>
      </div>
      {needMatter && files.length > 0 && <p className="hint end">사건을 선택해야 업로드할 수 있습니다.</p>}

      {summary && (
        <section className="card result" aria-live="polite">
          <h2>{phase === "done" ? "업로드를 접수했습니다" : "일부만 접수되었습니다"}</h2>
          <p>묶음 번호 <strong>#{summary.batchId}</strong> — 변환·색인은 백그라운드에서 진행됩니다.</p>
          <ul className="chips">
            {(Object.entries(summary.counts) as [JobStatus, number][]).map(([s, n]) => <li key={s} className={`badge badge-${s}`}>{STATUS_LABEL[s]} {n}</li>)}
            {summary.skipped > 0 && <li className="badge badge-duplicate">폴더·시스템 파일 {summary.skipped}개 건너뜀</li>}
          </ul>
          {summary.problems.length > 0 && (
            <details open={summary.problems.length <= 10}>
              <summary>거부·중복 {summary.problems.length}건 사유 보기</summary>
              <ul className="problems">{summary.problems.slice(0, 200).map((p, i) => <li key={i}><strong>{p.name}</strong> — {p.reason}</li>)}</ul>
            </details>
          )}
          <Link className="btn btn-primary" to={`/jobs?batch_id=${summary.batchId}`}>이 묶음의 처리 현황 보기</Link>
        </section>
      )}
    </div>
  );
}
