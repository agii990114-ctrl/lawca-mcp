"""인제스트 파이프라인: 큐에서 작업을 집어 파싱 → (검토) → 색인한다.

상태: queued → parsing → [needs_review →] ready_to_index → indexing → done | failed
- 워커는 작업을 올린 사용자의 신원(app.user_id)으로 DB에 쓴다. 그래서 접근 그룹을 넘는 저장은 RLS가 막는다.
- 파싱 결과(extracted_text)는 작업 행에 보관해, 사건 지정 후 다시 파싱하지 않고 색인만 한다.
"""
import logging
import traceback

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .. import config
from . import chunking, embed, metadata, parse, storage

log = logging.getLogger("lawca.worker")
STALE_MINUTES = 30


def connect():
    return psycopg.connect(config.WORKER_DSN, row_factory=dict_row)


def _set_user(c, user_id: str):
    c.execute("SELECT set_config('app.user_id', %s, true)", (user_id,))


def claim(conn, worker_id: str) -> dict | None:
    """색인 대기(ready_to_index)를 우선하고, 없으면 파싱 대기(queued)를 집는다. 멈춘 작업(locked 30분 초과)도 회수한다."""
    with conn.transaction():
        job = conn.execute(
            "SELECT * FROM ingest_jobs WHERE status IN ('ready_to_index','queued')"
            " OR (status IN ('parsing','indexing') AND locked_at < now() - make_interval(mins => %s))"
            " ORDER BY (status = 'ready_to_index' OR status = 'indexing') DESC, job_id FOR UPDATE SKIP LOCKED LIMIT 1",
            (STALE_MINUTES,)).fetchone()
        if not job:
            return None
        stage = "index" if job["status"] in ("ready_to_index", "indexing") else "parse"
        if job["attempts"] >= config.MAX_JOB_ATTEMPTS:
            conn.execute("UPDATE ingest_jobs SET status = 'failed', reason = %s, locked_by = NULL, updated_at = now() WHERE job_id = %s",
                         ("처리가 반복해서 중단되어 실패로 처리했습니다.", job["job_id"]))
            return {"job_id": job["job_id"], "stage": "exhausted"}
        conn.execute("UPDATE ingest_jobs SET status = %s, locked_by = %s, locked_at = now(), attempts = attempts + 1,"
                     " updated_at = now() WHERE job_id = %s",
                     ("indexing" if stage == "index" else "parsing", worker_id, job["job_id"]))
        job["stage"] = stage
        job["locked_by"] = worker_id
        return job


def _finish(conn, job_id: int, status: str, reason: str | None = None, keep_lock: str | None = None, **fields):
    """상태를 바꾼다. keep_lock(워커 ID)이 있으면 락을 유지해 다른 워커가 끼어들지 못하게 한다."""
    if keep_lock:
        sets = ["status = %s", "reason = %s", "locked_by = %s", "locked_at = now()", "updated_at = now()"]
        params = [status, reason, keep_lock]
    else:
        sets = ["status = %s", "reason = %s", "locked_by = NULL", "locked_at = NULL", "updated_at = now()"]
        params = [status, reason]
    for k, v in fields.items():
        sets.append(f"{k} = %s"); params.append(Jsonb(v) if k == "parse_meta" else v)
    conn.execute(f"UPDATE ingest_jobs SET {', '.join(sets)} WHERE job_id = %s", (*params, job_id))


def do_parse(conn, job: dict) -> str:
    """파싱 단계. 반환: 다음 상태(indexing: 이어서 색인 / needs_review / failed)"""
    with conn.transaction():
        f = conn.execute("SELECT stored_path, orig_name FROM uploaded_files WHERE file_id = %s", (job["file_id"],)).fetchone()
    if not f:
        with conn.transaction():
            _finish(conn, job["job_id"], "failed", "원본 파일 기록을 찾을 수 없습니다(삭제되었을 수 있습니다).")
        return "failed"
    ext = "." + f["orig_name"].rsplit(".", 1)[-1].lower() if "." in f["orig_name"] else ""
    try:
        text, meta = parse.parse_file(f["stored_path"], ext)
    except parse.ParseError as e:
        with conn.transaction():
            _finish(conn, job["job_id"], "failed", str(e))
        return "failed"
    ok, why = metadata.text_quality(text)
    if not ok and len(metadata._ns(text)) < 50:
        with conn.transaction():
            _finish(conn, job["job_id"], "failed", why, parse_meta=meta)
        return "failed"
    if ok and meta.get("suspect_pages"):
        # 문서 전체로는 글자가 충분해도 일부 쪽이 통째로 누락됐을 수 있다(스캔 OCR). 조용히 색인하지 않고 사람이 확인하게 한다.
        sp = meta["suspect_pages"]
        shown = ", ".join(str(p) for p in sp[:10]) + (" 등" if len(sp) > 10 else "")
        ok, why = False, (f"스캔 문서의 {shown}쪽에서 글자가 거의 인식되지 않았습니다(OCR 누락 가능). 원본의 해당 쪽을 확인하거나 "
                          "더 선명하게 스캔해 다시 올려 주세요. 그대로 진행하면 그 쪽의 내용은 검색되지 않습니다.")
    det = metadata.detect(text)
    meta["detected"] = det
    matter_id, auto = job["matter_id"], False
    with conn.transaction():
        if not matter_id and det.get("case_no"):
            _set_user(conn, job["created_by"])
            row = conn.execute("SELECT matter_id FROM matters WHERE case_no = %s AND access_group = %s",
                               (det["case_no"], job["access_group"])).fetchone()
            if row:
                matter_id, auto = row["matter_id"], True
        meta["auto_matched_matter"] = auto
        upd = dict(extracted_text=text, parse_meta=meta, matter_id=matter_id,
                   doc_kind=job["doc_kind"] or det.get("doc_kind"),
                   round=job["round"] or det.get("round"),
                   author_side=job["author_side"] or det.get("author_side"),
                   filed_date=job["filed_date"] or det.get("filed_date"))
        if not matter_id:
            cands = det.get("case_no_candidates")
            reason = "사건이 지정되지 않았습니다. 사건을 선택해 주세요."
            if det.get("case_no"):
                reason = (f"문서의 사건번호({det['case_no']})와 일치하는 사건을 이 접근 그룹에서 찾지 못했습니다"
                          "(등록되지 않았거나 이 그룹에서 볼 수 없는 사건일 수 있습니다). 사건을 등록하거나 선택해 주세요.")
            elif cands:
                reason = f"문서에 사건번호가 여러 개 있어 자동으로 지정하지 못했습니다({', '.join(cands)}). 사건을 선택해 주세요."
            if not ok:                         # 사건 미지정과 품질 문제가 겹치면 둘 다 알린다(사건만 고르고 넘기며 품질 경고를 놓치지 않도록)
                reason = f"{reason} / {why}"
            _finish(conn, job["job_id"], "needs_review", reason, **upd)
            return "needs_review"
        if not ok:
            _finish(conn, job["job_id"], "needs_review", why, **upd)
            return "needs_review"
        # 락을 유지한 채 곧바로 색인 단계로 넘긴다(ready_to_index로 풀면 다른 워커가 같은 작업을 집어 문서가 중복될 수 있다)
        _finish(conn, job["job_id"], "indexing", None, keep_lock=job["locked_by"], **upd)
    return "indexing"


def do_index(conn, job: dict) -> str:
    """색인 단계: 청킹 → 임베딩 → documents/doc_chunks/doc_issue_tags 저장 (작업자 신원으로 RLS 적용)"""
    with conn.transaction():
        j = conn.execute("SELECT * FROM ingest_jobs WHERE job_id = %s", (job["job_id"],)).fetchone()
        _set_user(conn, j["created_by"])
        m = conn.execute("SELECT access_group FROM matters WHERE matter_id = %s", (j["matter_id"],)).fetchone()
    if not m or m["access_group"] != j["access_group"]:
        with conn.transaction():
            _finish(conn, job["job_id"], "failed", "지정된 사건을 찾을 수 없거나 접근 그룹이 업로드와 다릅니다.")
        return "failed"
    text = j["extracted_text"] or ""
    chunks = chunking.chunk_text(text)
    if not chunks:
        with conn.transaction():
            _finish(conn, job["job_id"], "failed", "색인할 내용이 없습니다.")
        return "failed"
    vectors = embed.embed_passages([t for _, t in chunks])
    doc_vec = embed.mean_vector(vectors)
    with conn.transaction():
        _set_user(conn, j["created_by"])
        doc_id = "U" + str(conn.execute("SELECT nextval('doc_id_seq') AS n").fetchone()["n"]).zfill(6)
        conn.execute(
            "INSERT INTO documents (doc_id, matter_id, kind, round, author_side, filed_date, full_text, text_norm, access_group,"
            " embedding, source_file_id, source_name) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s,%s)",
            (doc_id, j["matter_id"], j["doc_kind"] or "기타", j["round"] or 1, j["author_side"] or "미상", j["filed_date"],
             text, "".join(text.split()), j["access_group"], embed.vec_literal(doc_vec), j["file_id"], j["orig_name"]))
        for i, ((label, ctext), v) in enumerate(zip(chunks, vectors)):
            conn.execute(
                "INSERT INTO doc_chunks (doc_id, seq, section, text, text_norm, access_group, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s::vector)",
                (doc_id, i, label, ctext, "".join(ctext.split()), j["access_group"], embed.vec_literal(v)))
        for tag in j["issue_tags"] or []:
            conn.execute("INSERT INTO doc_issue_tags VALUES (%s,%s) ON CONFLICT DO NOTHING", (doc_id, tag))
        _finish(conn, job["job_id"], "done", None, doc_id=doc_id, extracted_text=None)   # 색인 후에는 작업 행의 본문 사본을 비운다
    return "done"


def process_one(conn, worker_id: str) -> str | None:
    """작업 하나를 처리한다. 처리할 작업이 없으면 None."""
    job = claim(conn, worker_id)
    if job is None:
        return None
    if job.get("stage") == "exhausted":
        return "failed"
    jid = job["job_id"]
    try:
        if job["stage"] == "parse":
            nxt = do_parse(conn, job)
            if nxt != "indexing":
                return nxt
            job = conn.execute("SELECT * FROM ingest_jobs WHERE job_id = %s", (jid,)).fetchone()
            conn.commit()
        return do_index(conn, job)
    except Exception as e:          # 예기치 못한 오류: 사유를 남기고 실패 처리(재시도 가능)
        log.error("job %s failed: %s\n%s", jid, e, traceback.format_exc())
        conn.rollback()
        with conn.transaction():
            _finish(conn, jid, "failed", f"처리 중 내부 오류가 발생했습니다: {type(e).__name__}")
        return "failed"
