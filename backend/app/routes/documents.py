"""문서 목록·열람·삭제. 열람 범위는 DB의 행 수준 보안(접근 그룹)이 정한다."""
from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..deps import require
from ..ingest import storage

router = APIRouter(tags=["documents"])

LIST_COLS = ("d.doc_id, d.matter_id, m.case_no, m.case_type, d.kind, d.round, d.author_side, d.filed_date, d.version,"
             " d.access_group, d.source_name, length(d.full_text) AS text_length,"
             " coalesce((SELECT array_agg(t.tag ORDER BY t.tag) FROM doc_issue_tags t WHERE t.doc_id = d.doc_id), ARRAY[]::text[]) AS issue_tags")


@router.get("/documents")
def list_documents(matter_id: str | None = None, kind: str | None = None, tag: str | None = None, q: str | None = None,
                   limit: int = 50, offset: int = 0, user: dict = Depends(require("documents.read"))):
    limit = max(1, min(limit, 200))
    where, params = [], []
    if matter_id:
        where.append("d.matter_id = %s"); params.append(matter_id)
    if kind:
        where.append("replace(d.kind,' ','') = %s"); params.append(kind.replace(" ", ""))
    if tag:
        where.append("EXISTS (SELECT 1 FROM doc_issue_tags t WHERE t.doc_id = d.doc_id AND t.tag ILIKE %s)"); params.append(f"%{tag}%")
    if q:
        where.append("(d.source_name ILIKE %s OR m.case_no ILIKE %s OR d.doc_id ILIKE %s)"); params += [f"%{q}%"] * 3
    w = (" WHERE " + " AND ".join(where)) if where else ""
    base = f"FROM documents d JOIN matters m USING (matter_id){w}"
    with db.tx(user["user_id"]) as c:
        total = c.execute(f"SELECT count(*) AS n {base}", params).fetchone()["n"]
        rows = c.execute(f"SELECT {LIST_COLS} {base} ORDER BY d.filed_date DESC NULLS LAST, d.doc_id LIMIT %s OFFSET %s",
                         (*params, limit, offset)).fetchall()
    return {"total": total, "items": rows}


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, user: dict = Depends(require("documents.read"))):
    with db.tx(user["user_id"]) as c:
        row = c.execute(f"SELECT {LIST_COLS}, d.full_text FROM documents d JOIN matters m USING (matter_id) WHERE d.doc_id = %s",
                        (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")   # 권한 밖 문서와 없는 문서를 구분하지 않는다
        row["sections"] = [r["section"] for r in c.execute("SELECT section FROM doc_chunks WHERE doc_id = %s ORDER BY seq", (doc_id,))]
    db.audit(user["user_id"], "view_document", {"doc_id": doc_id}, [doc_id], 1)
    return row


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, user: dict = Depends(require("documents.delete"))):
    """문서와 청크·벡터·태그를 함께 삭제한다. 원본 파일은 다른 참조가 없을 때만 지운다."""
    to_unlink = None
    with db.tx(user["user_id"]) as c:
        row = c.execute("SELECT source_file_id FROM documents WHERE doc_id = %s", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(404, "문서를 찾을 수 없습니다.")
        c.execute("DELETE FROM documents WHERE doc_id = %s", (doc_id,))    # doc_chunks·doc_issue_tags는 CASCADE
        if row["source_file_id"]:
            still = c.execute("SELECT count(*) AS n FROM documents WHERE source_file_id = %s", (row["source_file_id"],)).fetchone()["n"]
            f = c.execute("SELECT stored_path FROM uploaded_files WHERE file_id = %s", (row["source_file_id"],)).fetchone()
            if f and still == 0:
                c.execute("DELETE FROM uploaded_files WHERE file_id = %s", (row["source_file_id"],))
                to_unlink = f["stored_path"]
        orphan = bool(to_unlink) and c.execute(
            "SELECT count(*) AS n FROM uploaded_files WHERE stored_path = %s", (to_unlink,)).fetchone()["n"] == 0
    if orphan:                      # 커밋 이후에 실제 파일을 지운다(트랜잭션이 실패해도 원본이 사라지지 않도록)
        storage.blob_path(to_unlink).unlink(missing_ok=True)
    db.audit(user["user_id"], "delete_document", {"doc_id": doc_id, "file_removed": orphan}, [doc_id], 1)
    return {"deleted": doc_id, "original_file_removed": orphan}
