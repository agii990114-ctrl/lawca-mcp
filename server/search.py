"""문서 검색: 식별자 정확 일치 필터 + (문서 임베딩 · 청크 임베딩 · 문자 trigram 키워드) RRF 결합.

실험(parse-test/)에서 확인한 설계를 따른다.
- 사건번호·당사자·금액은 임베딩으로 못 찾으므로 정확 일치 필터로 처리한다.
- 키워드는 공백을 제거한 텍스트(text_norm)에 대해 trigram 유사도로 본다(OCR 띄어쓰기 손상에 무관).
- 권한 필터는 DB의 행 수준 보안(RLS)이 강제한다. 여기서는 별도 권한 판단을 하지 않는다.
"""
import re

RE_CASE_NO = re.compile(r"\d{4}(?:가합|가단|가소|나|다|드단|드합|카합|카단|머|고단|고합|구합|누)\d{2,7}")
RRF_K = 60
_model = None


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def embed_query(text: str) -> str:
    """로컬 임베딩 모델(외부 전송 없음). 최초 호출 시 로드한다."""
    global _model
    if _model is None:
        import os
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name="intfloat/multilingual-e5-large", cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"),
                               local_files_only=os.environ.get("LAWCA_OFFLINE") == "1")
    v = next(iter(_model.query_embed([text])))
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def build_filters(*, case_no=None, party_name=None, amount=None, doc_kind=None, issue_tag=None,
                  our_side=None, result=None):
    """documents d JOIN matters m 에 대한 WHERE 절과 파라미터."""
    where, params = [], []
    if case_no:
        where.append("m.case_no = %s"); params.append(norm(case_no))
    if party_name:
        p = norm(party_name)
        where.append("(replace(m.plaintiff,' ','') ILIKE %s OR replace(m.defendant,' ','') ILIKE %s)")
        params += [f"%{p}%", f"%{p}%"]
    if amount:
        digits = re.sub(r"[^\d]", "", amount)
        if digits:
            where.append("replace(d.text_norm, ',', '') LIKE %s"); params.append(f"%{digits}%")
    if doc_kind:
        where.append("replace(d.kind,' ','') = %s"); params.append(norm(doc_kind))
    if issue_tag:
        where.append("EXISTS (SELECT 1 FROM doc_issue_tags t WHERE t.doc_id = d.doc_id AND t.tag ILIKE %s)")
        params.append(f"%{issue_tag.strip()}%")
    if our_side:
        where.append("m.our_side = %s"); params.append(our_side)
    if result:
        where.append("m.result = %s"); params.append(result)
    return where, params


def search(conn, *, query=None, limit=5, **filters):
    """반환: (결과 목록, 적용된 필터 설명, 참고 메모 목록)"""
    notes = []
    applied = {k: v for k, v in filters.items() if v}
    if query and not applied.get("case_no"):
        m = RE_CASE_NO.search(query.replace(" ", ""))
        if m:  # 안전망: 질의문에 사건번호가 있으면 정확 일치 필터로 승격
            filters["case_no"] = m.group(0)
            applied["case_no"] = m.group(0)
            notes.append(f"질의문에서 사건번호 {m.group(0)}를 발견해 정확 일치 필터로 적용했습니다.")
    where, params = build_filters(**filters)
    if not query and not where:
        raise ValueError("query 또는 필터(case_no, party_name, amount, doc_kind, issue_tag, ...) 중 하나는 필요합니다.")

    # ---- 1) 후보 문서 (필터 + 권한(RLS))
    sql = ("SELECT d.doc_id FROM documents d JOIN matters m USING (matter_id)"
           + (" WHERE " + " AND ".join(where) if where else ""))
    cand = [r["doc_id"] for r in conn.execute(sql, params).fetchall()]
    if not cand:
        if any(applied.get(k) for k in ("case_no", "party_name", "amount")):
            notes.append("식별자(사건번호·당사자·금액)와 정확히 일치하는 접근 가능한 문서가 없습니다. "
                         "금액은 OCR 오류로 원문과 다르게 저장됐을 수 있으니 query로 다시 시도해 보세요.")
        return [], applied, notes

    # ---- 2) 순위 계산
    ranks = {}          # doc_id -> {신호: 순위}
    best_chunk = {}     # doc_id -> (section, text)
    if query:
        qv = embed_query(query)
        qn = norm(query)
        signals = {}
        signals["doc_vec"] = [r["doc_id"] for r in conn.execute(
            "SELECT doc_id FROM documents WHERE doc_id = ANY(%s) ORDER BY embedding <=> %s::vector LIMIT 30",
            (cand, qv)).fetchall()]
        rows = conn.execute(
            "SELECT doc_id, section, text, d FROM ("
            " SELECT DISTINCT ON (doc_id) doc_id, section, text, embedding <=> %s::vector AS d"
            " FROM doc_chunks WHERE doc_id = ANY(%s) ORDER BY doc_id, d) x ORDER BY d LIMIT 30",
            (qv, cand)).fetchall()
        signals["chunk_vec"] = [r["doc_id"] for r in rows]
        for r in rows:
            best_chunk[r["doc_id"]] = (r["section"], r["text"])
        rows = conn.execute(
            "SELECT doc_id, section, text, ws FROM ("
            " SELECT DISTINCT ON (doc_id) doc_id, section, text, word_similarity(%s, text_norm) AS ws"
            " FROM doc_chunks WHERE doc_id = ANY(%s) ORDER BY doc_id, ws DESC) x ORDER BY ws DESC LIMIT 30",
            (qn, cand)).fetchall()
        signals["keyword"] = [r["doc_id"] for r in rows]
        for r in rows:
            best_chunk.setdefault(r["doc_id"], (r["section"], r["text"]))
        score = {}
        for name, lst in signals.items():
            for rank, d in enumerate(lst):
                score[d] = score.get(d, 0.0) + 1.0 / (RRF_K + rank + 1)
                ranks.setdefault(d, {})[name] = rank + 1
        ordered = sorted(score, key=lambda d: -score[d])[:limit]
        scores = score
    else:
        # 식별자·필터만 주어진 경우: 최신 제출일 순
        rows = conn.execute(
            "SELECT doc_id FROM documents WHERE doc_id = ANY(%s) ORDER BY filed_date DESC NULLS LAST, doc_id LIMIT %s",
            (cand, limit)).fetchall()
        ordered = [r["doc_id"] for r in rows]
        scores = {}
        for d in ordered:
            row = conn.execute("SELECT section, text FROM doc_chunks WHERE doc_id=%s ORDER BY seq LIMIT 1", (d,)).fetchone()
            if row:
                best_chunk[d] = (row["section"], row["text"])

    # ---- 3) 메타데이터 결합
    meta = {r["doc_id"]: r for r in conn.execute(
        "SELECT d.doc_id, d.kind, d.round, d.filed_date, d.author_side, d.version, m.matter_id, m.case_no, m.case_type,"
        " m.court, m.plaintiff, m.defendant, m.attorney_name, m.our_side, m.result, m.related_names,"
        " coalesce((SELECT array_agg(tag ORDER BY tag) FROM doc_issue_tags t WHERE t.doc_id = d.doc_id), ARRAY[]::text[]) AS tags"
        " FROM documents d JOIN matters m USING (matter_id) WHERE d.doc_id = ANY(%s)", (ordered,)).fetchall()}
    results = []
    for d in ordered:
        r = meta[d]
        sec, txt = best_chunk.get(d, (None, ""))
        results.append({
            "doc_id": d, "matter_id": r["matter_id"], "case_no": r["case_no"], "case_type": r["case_type"],
            "kind": r["kind"], "round": r["round"], "author_side": r["author_side"], "version": r["version"],
            "filed_date": str(r["filed_date"]) if r["filed_date"] else None,
            "our_side": r["our_side"], "result": r["result"], "issue_tags": r["tags"],
            "score": round(scores.get(d, 0.0), 4) if scores else None,
            "matched_signals": ranks.get(d),
            "best_section": sec, "_snippet_raw": txt,
            "_mask": dict(plaintiff=r["plaintiff"], defendant=r["defendant"], attorney=r["attorney_name"], case_no=r["case_no"],
                          related=r["related_names"]),
        })
    return results, applied, notes
