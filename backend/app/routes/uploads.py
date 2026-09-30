"""업로드(개별 파일·ZIP)와 인제스트 작업 조회·재시도·사건 지정."""
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import config, db
from ..deps import require
from ..ingest import intake as intake_mod
from ..ingest import storage

router = APIRouter(tags=["uploads"])
DOC_KINDS = ("소 장", "답 변 서", "준 비 서 면", "판결문", "계약서", "의견서", "기타")
JOB_COLS = ("j.job_id, j.batch_id, j.orig_name, j.status, j.reason, j.attempts, j.matter_id, j.doc_kind, j.round,"
            " j.author_side, j.filed_date, j.issue_tags, j.doc_id, j.created_by, j.access_group, j.created_at, j.updated_at")


class ResolveIn(BaseModel):
    job_ids: list[int] = Field(min_length=1, max_length=2000)
    matter_id: str
    doc_kind: str | None = None
    round: int | None = Field(None, ge=1, le=50)
    author_side: str | None = None
    filed_date: date | None = None
    issue_tags: list[str] | None = None


def _norm_kind(k: str | None) -> str | None:
    if not k:
        return None
    k2 = k.replace(" ", "")
    for cand in DOC_KINDS:
        if cand.replace(" ", "") == k2:
            return cand
    raise HTTPException(400, f"문서 종류는 {', '.join(c.replace(' ', '') for c in DOC_KINDS)} 중 하나여야 합니다.")


def _tags(s: str | list[str] | None) -> list[str]:
    if not s:
        return []
    parts = s.split(",") if isinstance(s, str) else s
    return sorted({p.strip()[:40] for p in parts if p.strip()})[:20]


def _counts(c, where: str, params) -> dict:
    return {r["status"]: r["n"] for r in c.execute(f"SELECT j.status, count(*) AS n FROM ingest_jobs j WHERE {where} GROUP BY 1", params)}


@router.post("/uploads", status_code=201)
def upload(files: list[UploadFile] = File(...), access_group: str = Form(...), matter_id: str | None = Form(None),
           doc_kind: str | None = Form(None), issue_tags: str = Form(""), note: str | None = Form(None),
           batch_id: int | None = Form(None), user: dict = Depends(require("documents.upload"))):
    """PDF·HWP·HWPX·DOCX·이미지를 개별로, 또는 ZIP으로 올린다. 큰 묶음은 batch_id로 여러 요청에 나누어 올릴 수 있다."""
    kind = _norm_kind(doc_kind)
    tags = _tags(issue_tags)
    # 1) 권한·대상 검증
    with db.tx(user["user_id"]) as c:
        mine = {r["group_id"] for r in c.execute("SELECT app_visible_groups() AS group_id")}
        if access_group not in mine:
            raise HTTPException(403, "소속되지 않은 접근 그룹에는 업로드할 수 없습니다.")
        if matter_id:
            m = c.execute("SELECT access_group FROM matters WHERE matter_id = %s", (matter_id,)).fetchone()
            if not m:
                raise HTTPException(404, "사건을 찾을 수 없습니다.")
            if m["access_group"] != access_group:
                raise HTTPException(400, "선택한 사건은 다른 접근 그룹에 속해 있습니다.")
        if batch_id is not None:
            b = c.execute("SELECT created_by, access_group FROM ingest_batches WHERE batch_id = %s", (batch_id,)).fetchone()
            if not b or b["created_by"] != user["user_id"] or b["access_group"] != access_group:
                raise HTTPException(404, "묶음(batch)을 찾을 수 없습니다.")
    # 2) 검증·저장 (DB 트랜잭션 밖: 큰 파일이 DB 연결을 오래 잡지 않도록)
    try:
        intake = intake_mod.receive([(f.filename or "이름없음", f.file) for f in files], access_group)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 3) 작업 등록
    with db.tx(user["user_id"]) as c:
        if batch_id is None:
            source = "zip" if intake.saw_zip and not intake.saw_plain else "files" if not intake.saw_zip else "mixed"
            batch_id = c.execute(
                "INSERT INTO ingest_batches (created_by, access_group, matter_id, source_kind, note) VALUES (%s,%s,%s,%s,%s) RETURNING batch_id",
                (user["user_id"], access_group, matter_id, source, (note or "")[:500] or None)).fetchone()["batch_id"]
        jobs = []
        for it in intake.items:
            status, reason, file_id = "queued", None, None
            if it.reject:
                status, reason = "rejected", it.reject
                if it.stored_path:
                    storage.unlink_if_orphan(c, it.stored_path)
            else:
                dup = c.execute(
                    "SELECT j.job_id, j.file_id FROM ingest_jobs j JOIN uploaded_files f USING (file_id)"
                    " WHERE f.sha256 = %s AND j.access_group = %s AND j.status NOT IN ('failed','rejected','duplicate')"
                    " AND j.matter_id IS NOT DISTINCT FROM %s LIMIT 1", (it.sha256, access_group, matter_id)).fetchone()
                if dup:
                    status, reason, file_id = "duplicate", f"이미 업로드된 파일입니다(작업 #{dup['job_id']})", dup["file_id"]
                else:
                    file_id = c.execute(
                        "INSERT INTO uploaded_files (sha256, size_bytes, orig_name, stored_path, uploaded_by, access_group)"
                        " VALUES (%s,%s,%s,%s,%s,%s) RETURNING file_id",
                        (it.sha256, it.size, it.name[:500], it.stored_path, user["user_id"], access_group)).fetchone()["file_id"]
            row = c.execute(
                "INSERT INTO ingest_jobs (batch_id, file_id, orig_name, status, reason, created_by, access_group, matter_id, doc_kind, issue_tags)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING job_id",
                (batch_id, file_id, it.name[:500], status, reason, user["user_id"], access_group, matter_id, kind, tags)).fetchone()
            jobs.append({"job_id": row["job_id"], "name": it.name, "status": status, "reason": reason})
        counts = _counts(c, "j.batch_id = %s", (batch_id,))
    db.audit(user["user_id"], "upload", {"batch_id": batch_id, "access_group": access_group, "matter_id": matter_id,
                                        "files": len(files), "items": len(jobs), "skipped": intake.skipped, "counts": counts})
    return {"batch_id": batch_id, "counts": counts, "skipped_entries": intake.skipped,
            "jobs": jobs[:300], "jobs_truncated": len(jobs) > 300}


@router.get("/uploads")
def list_batches(limit: int = 30, offset: int = 0, user: dict = Depends(require("documents.read"))):
    limit = max(1, min(limit, 100))
    with db.tx(user["user_id"]) as c:
        rows = c.execute(
            "SELECT b.batch_id, b.created_by, b.access_group, b.matter_id, b.source_kind, b.note, b.created_at,"
            " (SELECT count(*) FROM ingest_jobs j WHERE j.batch_id = b.batch_id) AS jobs"
            " FROM ingest_batches b ORDER BY b.batch_id DESC LIMIT %s OFFSET %s", (limit, offset)).fetchall()
        for r in rows:
            r["counts"] = _counts(c, "j.batch_id = %s", (r["batch_id"],))
    return rows


@router.get("/uploads/{batch_id}")
def get_batch(batch_id: int, user: dict = Depends(require("documents.read"))):
    with db.tx(user["user_id"]) as c:
        b = c.execute("SELECT b.batch_id, b.created_by, b.access_group, b.matter_id, b.source_kind, b.note, b.created_at"
                      " FROM ingest_batches b WHERE b.batch_id = %s", (batch_id,)).fetchone()
        if not b:
            raise HTTPException(404, "묶음을 찾을 수 없습니다.")
        b["counts"] = _counts(c, "j.batch_id = %s", (batch_id,))
    return b


@router.get("/jobs")
def list_jobs(status: str | None = None, batch_id: int | None = None, mine: bool = False, limit: int = 100, offset: int = 0,
              user: dict = Depends(require("documents.read"))):
    limit = max(1, min(limit, 500))
    where, params = ["true"], []
    if status:
        sts = [s.strip() for s in status.split(",") if s.strip()]
        where.append("j.status = ANY(%s)"); params.append(sts)
    if batch_id is not None:
        where.append("j.batch_id = %s"); params.append(batch_id)
    if mine:
        where.append("j.created_by = %s"); params.append(user["user_id"])
    w = " AND ".join(where)
    with db.tx(user["user_id"]) as c:
        counts = _counts(c, w, params)
        rows = c.execute(f"SELECT {JOB_COLS} FROM ingest_jobs j WHERE {w} ORDER BY j.job_id DESC LIMIT %s OFFSET %s",
                         (*params, limit, offset)).fetchall()
    return {"counts": counts, "items": rows}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, user: dict = Depends(require("documents.read"))):
    with db.tx(user["user_id"]) as c:
        row = c.execute(f"SELECT {JOB_COLS}, j.parse_meta, left(j.extracted_text, 3000) AS preview, "
                        "length(j.extracted_text) AS text_length FROM ingest_jobs j WHERE j.job_id = %s", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return row


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, user: dict = Depends(require("jobs.manage"))):
    with db.tx(user["user_id"]) as c:
        row = c.execute("SELECT status FROM ingest_jobs WHERE job_id = %s", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "작업을 찾을 수 없습니다.")
        if row["status"] != "failed":
            raise HTTPException(409, "실패한 작업만 다시 시도할 수 있습니다.")
        c.execute("UPDATE ingest_jobs SET status = 'queued', reason = NULL, attempts = 0, locked_by = NULL, locked_at = NULL,"
                  " updated_at = now() WHERE job_id = %s", (job_id,))
    db.audit(user["user_id"], "retry_job", {"job_id": job_id})
    return {"job_id": job_id, "status": "queued"}


@router.post("/jobs/resolve")
def resolve_jobs(body: ResolveIn, user: dict = Depends(require("jobs.manage"))):
    """'검토 필요' 작업에 사건·문서 정보를 지정해 색인 단계로 넘긴다(여러 건 한 번에 가능)."""
    kind = _norm_kind(body.doc_kind)
    if body.author_side is not None and body.author_side not in ("원고", "피고", "미상"):
        raise HTTPException(400, "author_side는 원고/피고/미상 중 하나여야 합니다.")
    tags = _tags(body.issue_tags) if body.issue_tags is not None else None
    moved, skipped = [], []
    with db.tx(user["user_id"]) as c:
        m = c.execute("SELECT access_group FROM matters WHERE matter_id = %s", (body.matter_id,)).fetchone()
        if not m:
            raise HTTPException(404, "사건을 찾을 수 없습니다.")
        for jid in body.job_ids:
            j = c.execute("SELECT status, access_group FROM ingest_jobs WHERE job_id = %s", (jid,)).fetchone()
            if not j or j["status"] != "needs_review":
                skipped.append({"job_id": jid, "reason": "검토 필요 상태가 아니거나 찾을 수 없음"}); continue
            if j["access_group"] != m["access_group"]:
                skipped.append({"job_id": jid, "reason": "사건과 업로드의 접근 그룹이 다릅니다"}); continue
            c.execute(
                "UPDATE ingest_jobs SET status = 'ready_to_index', reason = NULL, matter_id = %s,"
                " doc_kind = coalesce(%s, doc_kind), round = coalesce(%s, round), author_side = coalesce(%s, author_side),"
                " filed_date = coalesce(%s, filed_date), issue_tags = coalesce(%s, issue_tags), updated_at = now() WHERE job_id = %s",
                (body.matter_id, kind, body.round, body.author_side, body.filed_date, tags, jid))
            moved.append(jid)
    db.audit(user["user_id"], "resolve_jobs", {"matter_id": body.matter_id, "moved": len(moved), "skipped": len(skipped)})
    return {"resolved": moved, "skipped": skipped}
