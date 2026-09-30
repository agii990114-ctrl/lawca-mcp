import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { get, post, qs } from "../api";
import { useAuth } from "../auth";
import { MatterPicker } from "../components/MatterPicker";
import { Empty, ErrorNote, Field, Modal, Spinner, StatusBadge, fmtTime, useToast, usePolling } from "../components/ui";
import { DOC_KINDS, IN_PROGRESS, STATUS_LABEL, kindLabel, type Job, type JobList, type JobStatus } from "../types";

const ORDER: JobStatus[] = ["needs_review", "failed", "queued", "parsing", "ready_to_index", "indexing", "done", "duplicate", "rejected"];

export default function Jobs() {
  const { can } = useAuth();
  const toast = useToast();
  const [sp, setSp] = useSearchParams();
  const batchId = sp.get("batch_id") ?? "";
  const status = sp.get("status") ?? "";
  const [mine, setMine] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [review, setReview] = useState<Job[] | null>(null);

  const { data, error, loading, reload } = usePolling<JobList>(
    () => get<JobList>(`/jobs${qs({ status, batch_id: batchId, mine, limit: 300 })}`),
    [status, batchId, mine],
    (d) => (d && IN_PROGRESS.some((s) => (d.counts[s] ?? 0) > 0) ? 3000 : 15000),
  );

  useEffect(() => setSelected(new Set()), [status, batchId, mine]);
  const counts = data?.counts ?? {};
  const total = Object.values(counts).reduce((a, b) => a + (b ?? 0), 0);
  const active = IN_PROGRESS.reduce((a, s) => a + (counts[s] ?? 0), 0);
  const reviewable = useMemo(() => (data?.items ?? []).filter((j) => j.status === "needs_review"), [data]);
  const selectedReview = reviewable.filter((j) => selected.has(j.job_id));

  const setFilter = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v); else n.delete(k);
    setSp(n, { replace: true });
  };

  const retry = async (j: Job) => {
    try {
      await post(`/jobs/${j.job_id}/retry`);
      toast("다시 시도하도록 대기열에 넣었습니다.");
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : "다시 시도하지 못했습니다.", "error");
    }
  };

  return (
    <div className="page">
      <div className="row between">
        <h1>처리 현황</h1>
        <div className="row">
          {batchId && <button className="chip chip-on" onClick={() => setFilter("batch_id", "")}>묶음 #{batchId} ✕</button>}
          <label className="check"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> 내가 올린 것만</label>
          <button className="btn" onClick={reload}>새로고침</button>
        </div>
      </div>
      <p className="muted" aria-live="polite">
        {active > 0 ? <><Spinner /> 처리 중인 작업 {active}건 — 자동으로 갱신됩니다.</> : "진행 중인 작업이 없습니다."}
        {(counts.needs_review ?? 0) > 0 && <strong> · 사람이 확인해야 할 문서 {counts.needs_review}건</strong>}
      </p>

      <div className="chips" role="tablist" aria-label="상태 필터">
        <button className={`chip ${status === "" ? "chip-on" : ""}`} onClick={() => setFilter("status", "")}>전체 {total}</button>
        {ORDER.filter((s) => (counts[s] ?? 0) > 0).map((s) => (
          <button key={s} className={`chip ${status === s ? "chip-on" : ""}`} onClick={() => setFilter("status", s)}>{STATUS_LABEL[s]} {counts[s]}</button>
        ))}
      </div>

      <ErrorNote error={error} />
      {selectedReview.length > 0 && can("jobs.manage") && (
        <div className="bar">
          <strong>{selectedReview.length}건 선택됨</strong>
          <button className="btn btn-primary" onClick={() => setReview(selectedReview)}>선택한 문서에 사건 지정</button>
          <button className="btn btn-ghost" onClick={() => setSelected(new Set())}>선택 해제</button>
        </div>
      )}

      <div className="card flush">
        {loading && !data ? <div className="pad"><Spinner /></div> : (data?.items.length ?? 0) === 0 ? (
          <Empty>표시할 작업이 없습니다. <Link to="/upload">문서를 업로드</Link>해 보세요.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="check-col">
                    {reviewable.length > 0 && can("jobs.manage") && (
                      <input type="checkbox" aria-label="검토 필요 문서 모두 선택" checked={reviewable.every((j) => selected.has(j.job_id))}
                        onChange={(e) => setSelected(e.target.checked ? new Set(reviewable.map((j) => j.job_id)) : new Set())} />)}
                  </th>
                  <th>파일</th><th>상태</th><th>사유·메모</th><th>사건</th><th>종류</th><th>시각</th><th />
                </tr>
              </thead>
              <tbody>
                {data!.items.map((j) => (
                  <tr key={j.job_id}>
                    <td className="check-col">
                      {j.status === "needs_review" && can("jobs.manage") && (
                        <input type="checkbox" aria-label={`${j.orig_name} 선택`} checked={selected.has(j.job_id)}
                          onChange={(e) => setSelected((cur) => { const n = new Set(cur); e.target.checked ? n.add(j.job_id) : n.delete(j.job_id); return n; })} />)}
                    </td>
                    <td className="wrap"><span title={j.orig_name}>{j.orig_name}</span><div className="hint">#{j.job_id} · 묶음 {j.batch_id} · {j.created_by}</div></td>
                    <td><StatusBadge status={j.status} /></td>
                    <td className="wrap reason">{j.reason ?? (j.status === "done" ? `문서 ${j.doc_id}` : "")}</td>
                    <td>{j.matter_id ?? "—"}</td>
                    <td className="nowrap">{kindLabel(j.doc_kind)}</td>
                    <td className="nowrap">{fmtTime(j.updated_at)}</td>
                    <td className="nowrap num">
                      {j.status === "needs_review" && can("jobs.manage") && <button className="btn" onClick={() => setReview([j])}>검토</button>}
                      {j.status === "failed" && can("jobs.manage") && <button className="btn" onClick={() => retry(j)}>다시 시도</button>}
                      {j.status === "done" && j.doc_id && <Link className="btn btn-ghost" to={`/documents?doc=${j.doc_id}`}>문서 보기</Link>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {(data?.items.length ?? 0) >= 300 && <p className="hint">최근 300건만 표시합니다. 상태 필터나 묶음 번호로 좁혀 보세요.</p>}

      {review && <ReviewModal jobs={review} onClose={() => setReview(null)} onDone={() => { setReview(null); setSelected(new Set()); reload(); }} />}
    </div>
  );
}

function ReviewModal({ jobs, onClose, onDone }: { jobs: Job[]; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const first = jobs[0];
  const group = first.access_group;
  const sameGroup = jobs.every((j) => j.access_group === group);
  const [detail, setDetail] = useState<Job | null>(null);
  const [matterId, setMatterId] = useState("");
  const [kind, setKind] = useState(first.doc_kind?.replace(/ /g, "") ?? "");
  const [round, setRound] = useState(first.round ? String(first.round) : "");
  const [side, setSide] = useState(first.author_side ?? "");
  const [filed, setFiled] = useState(first.filed_date ?? "");
  const [tags, setTags] = useState(first.issue_tags.join(", "));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const bulk = jobs.length > 1;

  useEffect(() => { void get<Job>(`/jobs/${first.job_id}`).then(setDetail).catch(() => undefined); }, [first.job_id]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const body: Record<string, unknown> = { job_ids: jobs.map((j) => j.job_id), matter_id: matterId };
      if (kind) body.doc_kind = kind;
      if (round) body.round = Number(round);
      if (side) body.author_side = side;
      if (filed) body.filed_date = filed;
      if (tags.trim() || !bulk) body.issue_tags = tags.split(",").map((t) => t.trim()).filter(Boolean);
      const r = await post<{ resolved: number[]; skipped: { job_id: number; reason: string }[] }>("/jobs/resolve", body);
      toast(`${r.resolved.length}건을 색인 단계로 넘겼습니다.${r.skipped.length ? ` (${r.skipped.length}건 제외)` : ""}`, r.skipped.length ? "info" : "ok");
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "지정하지 못했습니다.");
    } finally { setBusy(false); }
  };

  return (
    <Modal title={bulk ? `${jobs.length}건에 사건 지정` : "문서 검토"} onClose={onClose} wide>
      <form onSubmit={submit} className="review">
        {!bulk && (
          <div className="review-preview">
            <h3>{first.orig_name}</h3>
            <p className="reason-note">{first.reason}</p>
            <pre className="preview" tabIndex={0} aria-label="추출된 텍스트 미리보기">{detail?.preview ?? "미리보기를 불러오는 중…"}</pre>
            {detail?.text_length ? <div className="hint">전체 {detail.text_length.toLocaleString()}자 중 앞부분만 표시합니다. 스캔 문서는 글자 인식이 부정확할 수 있으니 원본과 대조하세요.</div> : null}
          </div>
        )}
        <div className="review-form">
          {bulk && !sameGroup && <div className="alert alert-error">서로 다른 접근 그룹의 문서가 섞여 있습니다. 그룹별로 나누어 지정해 주세요.</div>}
          <ErrorNote error={error} />
          <Field label={`사건 (접근 그룹 ${group})`} htmlFor="rm"><MatterPicker id="rm" group={group} value={matterId} onChange={(id) => setMatterId(id)} /></Field>
          <div className="grid-2">
            <Field label="문서 종류" htmlFor="rk"><select id="rk" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">그대로</option>{DOC_KINDS.map((k) => <option key={k}>{k}</option>)}</select></Field>
            <Field label="차수" htmlFor="rr"><input id="rr" type="number" min={1} max={50} value={round} onChange={(e) => setRound(e.target.value)} placeholder="예: 1" /></Field>
            <Field label="작성 측" htmlFor="rs"><select id="rs" value={side} onChange={(e) => setSide(e.target.value)}><option value="">그대로</option><option>원고</option><option>피고</option><option>미상</option></select></Field>
            <Field label="제출일" htmlFor="rf"><input id="rf" type="date" value={filed} onChange={(e) => setFiled(e.target.value)} /></Field>
          </div>
          <Field label="쟁점 태그" htmlFor="rt" hint={bulk ? "비워 두면 기존 태그를 유지합니다. 쉼표로 구분." : "쉼표로 구분"}><input id="rt" value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
          <div className="row end">
            <button type="button" className="btn" onClick={onClose}>취소</button>
            <button className="btn btn-primary" disabled={!matterId || busy || !sameGroup}>{busy ? "처리 중…" : "사건 지정하고 색인"}</button>
          </div>
        </div>
      </form>
    </Modal>
  );
}
