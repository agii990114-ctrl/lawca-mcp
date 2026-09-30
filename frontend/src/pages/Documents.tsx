import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { del, get, qs } from "../api";
import { useAuth } from "../auth";
import { ConfirmModal, Empty, ErrorNote, Modal, Spinner, useToast, usePolling } from "../components/ui";
import { DOC_KINDS, kindLabel, type DocItem } from "../types";

export default function Documents() {
  const [sp, setSp] = useSearchParams();
  const matterId = sp.get("matter_id") ?? "";
  const openDoc = sp.get("doc") ?? "";
  const [kind, setKind] = useState("");
  const [tag, setTag] = useState("");
  const [q, setQ] = useState("");
  const { data, error, loading, reload } = usePolling<{ total: number; items: DocItem[] }>(
    () => get(`/documents${qs({ matter_id: matterId, kind, tag, q, limit: 100 })}`), [matterId, kind, tag, q], () => null);

  const setParam = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v); else n.delete(k);
    setSp(n, { replace: true });
  };

  return (
    <div className="page">
      <h1>문서</h1>
      <div className="row wrap-row">
        <input type="search" className="grow" placeholder="파일명·사건번호·문서번호 검색" value={q} onChange={(e) => setQ(e.target.value)} aria-label="문서 검색" />
        <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="문서 종류"><option value="">모든 종류</option>{DOC_KINDS.map((k) => <option key={k}>{k}</option>)}</select>
        <input type="search" placeholder="쟁점 태그" value={tag} onChange={(e) => setTag(e.target.value)} aria-label="쟁점 태그" />
        {matterId && <button className="chip chip-on" onClick={() => setParam("matter_id", "")}>사건 {matterId} ✕</button>}
      </div>
      <ErrorNote error={error} />
      <div className="card flush">
        {loading && !data ? <div className="pad"><Spinner /></div> : (data?.items.length ?? 0) === 0 ? (
          <Empty>표시할 문서가 없습니다. <Link to="/upload">문서를 업로드</Link>하면 여기에 나타납니다.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>문서</th><th>사건번호</th><th>종류</th><th>차수</th><th>쟁점</th><th>제출일</th><th>그룹</th></tr></thead>
              <tbody>
                {data!.items.map((d) => (
                  <tr key={d.doc_id} className="clickable" onClick={() => setParam("doc", d.doc_id)} tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && setParam("doc", d.doc_id)}>
                    <td className="wrap"><strong>{d.source_name ?? d.doc_id}</strong><div className="hint">{d.doc_id} · {d.text_length.toLocaleString()}자</div></td>
                    <td className="nowrap">{d.case_no}<div className="hint">{d.case_type}</div></td>
                    <td className="nowrap">{kindLabel(d.kind)}</td><td>{d.round}</td>
                    <td className="wrap">{d.issue_tags.length ? d.issue_tags.map((t) => <span key={t} className="tag">{t}</span>) : "—"}</td>
                    <td className="nowrap">{d.filed_date ?? "—"}</td><td>{d.access_group}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {data && <p className="hint">{data.total}건 중 {data.items.length}건 표시</p>}
      {openDoc && <DocViewer docId={openDoc} onClose={() => setParam("doc", "")} onDeleted={() => { setParam("doc", ""); reload(); }} />}
    </div>
  );
}

function DocViewer({ docId, onClose, onDeleted }: { docId: string; onClose: () => void; onDeleted: () => void }) {
  const { can } = useAuth();
  const toast = useToast();
  const [doc, setDoc] = useState<DocItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);

  useEffect(() => {
    setDoc(null); setError(null);
    get<DocItem>(`/documents/${docId}`).then(setDoc).catch((e) => setError(e instanceof Error ? e.message : "불러오지 못했습니다."));
  }, [docId]);

  return (
    <>
      <Modal title={doc?.source_name ?? docId} onClose={onClose} wide
        footer={<>{can("documents.delete") && doc && <button className="btn btn-danger" onClick={() => setConfirm(true)}>문서 삭제</button>}<button className="btn" onClick={onClose}>닫기</button></>}>
        <ErrorNote error={error} />
        {!doc && !error && <Spinner />}
        {doc && (
          <>
            <dl className="meta">
              <div><dt>사건</dt><dd>{doc.case_no} · {doc.case_type}</dd></div>
              <div><dt>종류</dt><dd>{kindLabel(doc.kind)} {doc.round}차 · {doc.author_side}</dd></div>
              <div><dt>제출일</dt><dd>{doc.filed_date ?? "—"}</dd></div>
              <div><dt>접근 그룹</dt><dd>{doc.access_group}</dd></div>
              <div><dt>쟁점</dt><dd>{doc.issue_tags.length ? doc.issue_tags.map((t) => <span key={t} className="tag">{t}</span>) : "—"}</dd></div>
              <div><dt>문서번호</dt><dd>{doc.doc_id}</dd></div>
            </dl>
            {doc.sections && doc.sections.length > 0 && <p className="hint">색인 단위: {Array.from(new Set(doc.sections)).join(" · ")}</p>}
            <pre className="fulltext" tabIndex={0} aria-label="문서 본문">{doc.full_text}</pre>
            <p className="hint">문서를 열람한 기록은 감사 로그에 남습니다.</p>
          </>
        )}
      </Modal>
      {confirm && (
        <ConfirmModal title="문서를 삭제할까요?" danger confirmLabel="삭제" onClose={() => setConfirm(false)}
          message="문서 본문, 검색 색인(벡터), 쟁점 태그와 원본 파일이 함께 삭제되며 되돌릴 수 없습니다."
          onConfirm={async () => {
            try { await del(`/documents/${docId}`); toast("문서를 삭제했습니다."); onDeleted(); }
            catch (e) { toast(e instanceof Error ? e.message : "삭제하지 못했습니다.", "error"); setConfirm(false); }
          }} />
      )}
    </>
  );
}
