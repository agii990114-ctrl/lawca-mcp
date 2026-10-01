"""인제스트 종단 테스트: 업로드 → (실제 kordoc 파싱·OCR) → 청킹 → (실제 e5 임베딩) → 저장 → 검색/삭제.
느린 테스트(모델 로드·OCR 포함). 워커는 프로세스를 띄우지 않고 process_one을 직접 호출한다."""
import psycopg
import pytest
from psycopg.rows import dict_row

from backend.app import config
from backend.app.ingest import embed, pipeline, storage
from backend.tests.helpers import fake_pdf, make_docx, scan_pdf

READER_DSN = (f"host={config.ENV.get('LAWCA_DB_HOST', '127.0.0.1')} port={config.ENV['LAWCA_DB_PORT']} dbname={config.ENV['LAWCA_DB_NAME']}"
              f" user=lawca_reader password={config.ENV['LAWCA_READER_PASSWORD']}")


def up(api, files, group="T-A", **data):
    payload = [("files", (name, content, "application/octet-stream")) for name, content in files]
    return api.post("/api/uploads", files=payload, data={"access_group": group, **data})


def run_worker(limit=30):
    n = 0
    with pipeline.connect() as conn:
        while n < limit:
            if pipeline.process_one(conn, "pytest-worker") is None:
                break
            n += 1
    return n


def brief_paragraphs(case_no, topic):
    return ["준 비 서 면", f"사 건 {case_no} 대여금", "원 고 테스트갑", "피 고 테스트을", "",
            "1. 상대방 주장의 요지", f"원고는 {topic}을 이유로 대여금 전액의 지급을 구합니다.",
            "2. 인정 및 부인", "가. 대여 사실과 변제기는 인정합니다.", "나. 그 밖의 주장은 모두 부인합니다.",
            "3. 쟁점별 반박", f"가. {topic}에 대한 반박", f"{topic}은 법률상 인정될 수 없고 입증책임은 원고에게 있습니다. 갑 제1호증만으로는 부족합니다.",
            "나. 지연손해금의 범위", "약정이율이 없으므로 법정이율을 초과할 수 없습니다.",
            "4. 입증방법", "을 제1호증 메시지 대화 전문", "5. 결론", "원고의 청구는 기각되어야 합니다.", "2026. 3. 10.", "피고 소송대리인 변호사 홍길동"]


def nearest_docs(user_id, query, k=3):
    """RLS가 적용된 읽기 전용 계정으로 문서 임베딩 최근접 검색(MCP 서버와 같은 경로)"""
    qv = "[" + ",".join(f"{x:.6f}" for x in next(iter(embed._get().query_embed([query])))) + "]"
    with psycopg.connect(READER_DSN, autocommit=True, row_factory=dict_row) as c:
        c.execute("SELECT set_config('app.user_id', %s, false)", (user_id,))
        return [r["doc_id"] for r in c.execute("SELECT doc_id FROM documents ORDER BY embedding <=> %s::vector LIMIT %s", (qv, k))]


def test_docx_auto_matched_indexed_searchable_and_isolated(login, new_matter, owner):
    m = new_matter(case_no="2026가합90501")
    a = login("t_lawyer_a")
    docx = make_docx(brief_paragraphs("2026가합90501", "수중익선 시운전 약정을 근거로 한 별도 채권"))
    r = up(a, [("제1차 준비서면.docx", docx)], issue_tags="시효, 승인")           # 사건 미지정: 문서 안 사건번호로 자동 지정
    assert r.status_code == 201, r.text
    job_id = r.json()["jobs"][0]["job_id"]
    run_worker()
    job = a.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done", job
    assert job["matter_id"] == m["matter_id"] and job["doc_kind"] == "준 비 서 면" and job["doc_id"]
    doc_id = job["doc_id"]
    assert set(job["issue_tags"]) == {"시효", "승인"}

    d = owner.execute("SELECT kind, round, author_side, access_group, source_name, vector_dims(embedding) AS dims, filed_date FROM documents WHERE doc_id = %s", (doc_id,)).fetchone()
    assert d["kind"] == "준 비 서 면" and d["access_group"] == "T-A" and d["dims"] == 1024
    assert d["author_side"] == "피고" and str(d["filed_date"]) == "2026-03-10" and d["source_name"].endswith(".docx")
    chunks = owner.execute("SELECT section, vector_dims(embedding) AS dims FROM doc_chunks WHERE doc_id = %s ORDER BY seq", (doc_id,)).fetchall()
    assert len(chunks) >= 5 and all(c["dims"] == 1024 for c in chunks)
    assert {"1. 상대방 주장의 요지", "5. 결론"} <= {c["section"] for c in chunks}
    assert {r["tag"] for r in owner.execute("SELECT tag FROM doc_issue_tags WHERE doc_id = %s", (doc_id,))} == {"시효", "승인"}
    assert owner.execute("SELECT extracted_text FROM ingest_jobs WHERE job_id = %s", (job_id,)).fetchone()["extracted_text"] is None   # 색인 후 사본은 비움

    # 검색: 같은 그룹은 찾고, 다른 그룹은 존재도 볼 수 없다
    assert doc_id in nearest_docs("t_lawyer_a", "수중익선 시운전 약정에 근거한 별도 채권 주장에 대한 반박")
    assert nearest_docs("t_lawyer_b", "수중익선 시운전 약정에 근거한 별도 채권 주장에 대한 반박") == []
    # 웹 API로도 조회·감사
    assert a.get(f"/api/documents/{doc_id}").status_code == 200
    assert login("t_lawyer_b").get(f"/api/documents/{doc_id}").status_code == 404
    assert owner.execute("SELECT count(*) AS n FROM audit_log WHERE user_id = 't_lawyer_a' AND tool = 'api:view_document'").fetchone()["n"] >= 1
    listed = a.get(f"/api/documents?matter_id={m['matter_id']}").json()
    assert listed["total"] == 1 and listed["items"][0]["doc_id"] == doc_id

    # 삭제: 문서·청크·태그·원본 파일이 함께 사라진다
    path = storage.BLOBS / owner.execute("SELECT stored_path FROM uploaded_files WHERE file_id = (SELECT source_file_id FROM documents WHERE doc_id = %s)", (doc_id,)).fetchone()["stored_path"]
    assert path.exists()
    assert login("t_clerk_a").delete(f"/api/documents/{doc_id}").status_code == 403
    r = a.delete(f"/api/documents/{doc_id}")
    assert r.status_code == 200 and r.json()["original_file_removed"] is True
    assert not path.exists()
    assert owner.execute("SELECT count(*) AS n FROM doc_chunks WHERE doc_id = %s", (doc_id,)).fetchone()["n"] == 0
    assert a.get(f"/api/documents/{doc_id}").status_code == 404


def test_needs_review_then_resolve_then_indexed(login, new_matter, owner):
    m = new_matter(case_no="2026가합90502")
    other_group = new_matter(case_no="2026가합90503", group="T-B", user="t_lawyer_b")
    a = login("t_lawyer_a")
    paras = ["의견서", "본 의견서는 임대차 종료 후 보증금 반환 시기에 관한 검토 결과를 담고 있습니다.", "",
             "1. 검토 배경", "임차인은 계약 종료일에 목적물을 인도하였고 임대인은 보증금 반환을 지연하고 있습니다. " * 6,
             "2. 결론", "보증금 반환 의무와 목적물 인도 의무는 동시이행 관계에 있습니다. " * 6]
    r = up(a, [("의견서.docx", make_docx(paras))])
    job_id = r.json()["jobs"][0]["job_id"]
    run_worker()
    job = a.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "needs_review" and "사건" in job["reason"]              # 사건번호가 없어 사람이 지정해야 한다
    assert "보증금 반환" in job["preview"] and job["doc_kind"] == "의견서"
    assert owner.execute("SELECT count(*) AS n FROM documents WHERE source_name = '의견서.docx'").fetchone()["n"] == 0   # 검토 전에는 색인하지 않는다

    # 다른 그룹의 사건으로는 지정할 수 없고(사건 조회 자체가 안 되거나 그룹 불일치), 같은 그룹 사건은 된다
    res = a.post("/api/jobs/resolve", json={"job_ids": [job_id], "matter_id": other_group["matter_id"]})
    assert res.status_code == 404
    res = a.post("/api/jobs/resolve", json={"job_ids": [job_id, 999999], "matter_id": m["matter_id"], "issue_tags": ["동시이행"]})
    assert res.status_code == 200 and res.json()["resolved"] == [job_id] and len(res.json()["skipped"]) == 1
    assert a.get(f"/api/jobs/{job_id}").json()["status"] == "ready_to_index"
    run_worker()
    job = a.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done" and job["matter_id"] == m["matter_id"]
    assert owner.execute("SELECT kind FROM documents WHERE doc_id = %s", (job["doc_id"],)).fetchone()["kind"] == "의견서"
    # 해결할 수 없는 상태의 작업은 다시 지정할 수 없다
    assert a.post("/api/jobs/resolve", json={"job_ids": [job_id], "matter_id": m["matter_id"]}).json()["resolved"] == []


@pytest.mark.skipif(scan_pdf(["x"]) is None, reason="한글 폰트가 없어 스캔 PDF를 만들 수 없음")
def test_scanned_pdf_goes_through_ocr(login, new_matter, owner):
    m = new_matter()
    a = login("t_lawyer_a")
    pdf = scan_pdf(["답 변 서", "사 건 2026가단90511 대여금", "원고의 청구를 기각한다.", "소송비용은 원고가 부담한다.",
                    "피고는 소멸시효 완성을 원용한다.", "원고 주장의 대여 사실은 증거가 없습니다.", "연락처 010-1234-5678"])
    r = up(a, [("스캔본.pdf", pdf)], matter_id=m["matter_id"])
    job_id = r.json()["jobs"][0]["job_id"]
    run_worker()
    job = a.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done", job
    text = owner.execute("SELECT full_text FROM documents WHERE doc_id = %s", (job["doc_id"],)).fetchone()["full_text"]
    flat = "".join(text.split())
    assert "소멸시효" in flat and "010-1234-5678" in flat           # OCR 결과(띄어쓰기는 깨져도 글자와 숫자는 정확)
    assert job["parse_meta"]["parser"] == "kordoc"


def test_unreadable_file_fails_with_reason_and_can_be_retried(login):
    a = login("t_lawyer_a")
    r = up(a, [("망가진.pdf", fake_pdf(b"\x00\x01\x02 not a real pdf body " * 40))])
    job_id = r.json()["jobs"][0]["job_id"]
    run_worker()
    job = a.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "failed" and job["reason"], job
    assert "{" not in job["reason"] and "success" not in job["reason"], job["reason"]      # 변환 도구의 원시 오류는 노출하지 않는다
    assert login("t_clerk_a").post(f"/api/jobs/{job_id}/retry").status_code == 200        # 사무원도 재시도 가능(jobs.manage)
    assert a.get(f"/api/jobs/{job_id}").json()["status"] == "queued"
    assert a.post(f"/api/jobs/{job_id}/retry").status_code == 409                          # 실패 상태가 아니면 재시도 불가
    run_worker()
    assert a.get(f"/api/jobs/{job_id}").json()["status"] == "failed"                       # 원인이 그대로라 다시 실패(무한 반복 없음)


def test_worker_cannot_index_outside_uploaders_groups(new_matter, owner):
    """방어선: 작업 행이 조작돼 업로더가 접근할 수 없는 그룹의 사건을 가리켜도, RLS 때문에 색인되지 않는다."""
    m = new_matter()                                                 # T-A 사건
    owner.execute("INSERT INTO ingest_batches (batch_id, created_by, access_group, matter_id, source_kind) VALUES (990001, 't_lawyer_b', 'T-A', %s, 'files')", (m["matter_id"],))
    owner.execute("INSERT INTO uploaded_files (file_id, sha256, size_bytes, orig_name, stored_path, uploaded_by, access_group)"
                  " VALUES (990001, 'x', 1, 'x.pdf', 'T-A/xx/x', 't_lawyer_a', 'T-A')")
    owner.execute("INSERT INTO ingest_jobs (job_id, batch_id, file_id, orig_name, status, created_by, access_group, matter_id, extracted_text)"
                  " VALUES (990001, 990001, 990001, 'x.pdf', 'ready_to_index', 't_lawyer_b', 'T-A', %s, %s)", (m["matter_id"], "충분히 긴 본문 " * 20))
    run_worker()
    job = owner.execute("SELECT status, reason, doc_id FROM ingest_jobs WHERE job_id = 990001").fetchone()
    assert job["status"] == "failed" and job["doc_id"] is None
    assert owner.execute("SELECT count(*) AS n FROM documents WHERE source_file_id = 990001").fetchone()["n"] == 0


def test_two_workers_never_process_the_same_job(login, owner):
    """SKIP LOCKED: 동시에 집으면 서로 다른 작업을 받는다."""
    a = login("t_lawyer_a")
    up(a, [("c1.pdf", fake_pdf(b"c1" * 200)), ("c2.pdf", fake_pdf(b"c2" * 200))])
    with pipeline.connect() as c1, pipeline.connect() as c2:
        j1 = pipeline.claim(c1, "w1")
        j2 = pipeline.claim(c2, "w2")
        assert j1 and j2 and j1["job_id"] != j2["job_id"]
        assert owner.execute("SELECT locked_by FROM ingest_jobs WHERE job_id = %s", (j1["job_id"],)).fetchone()["locked_by"] == "w1"
        for j in (j1, j2):     # 정리: 실패 처리
            with c1.transaction():
                pipeline._finish(c1, j["job_id"], "failed", "테스트 정리")


def _scan_pages():
    body = ["피고는 소멸시효 완성을 원용하고 원고 주장의 대여 사실은 증거가 없다고 다툽니다." for _ in range(16)]
    return [["준 비 서 면"] + body, ["2. 인정 및 부인"] + body, ["3. 쟁점별 반박"] + body, ["4. 입증방법"] + body]


@pytest.mark.skipif(scan_pdf(["x"]) is None, reason="한글 폰트가 없어 스캔 PDF를 만들 수 없음")
def test_scan_with_an_unreadable_page_is_sent_to_review_not_silently_indexed(login, new_matter):
    """문서 전체 글자 수는 충분해도 한 쪽이 통째로 읽히지 않으면 조용히 색인하지 않고 확인 대기로 보낸다(트러블슈팅 23번)."""
    from backend.tests.helpers import scan_pdf_pages
    m = new_matter()
    a = login("t_lawyer_a")
    r = up(a, [("누락쪽.pdf", scan_pdf_pages(_scan_pages(), faint_pages=(3,)))], matter_id=m["matter_id"])
    job_id = r.json()["jobs"][0]["job_id"]
    run_worker()
    job = a.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "needs_review", job
    assert "3쪽" in job["reason"] and "OCR" in job["reason"], job["reason"]
    assert job["parse_meta"]["suspect_pages"] == [3] and job["parse_meta"]["pages"] == 4


@pytest.mark.skipif(scan_pdf(["x"]) is None, reason="한글 폰트가 없어 스캔 PDF를 만들 수 없음")
def test_multi_page_scan_with_all_pages_readable_is_indexed(login, new_matter):
    from backend.tests.helpers import scan_pdf_pages
    m = new_matter()
    a = login("t_lawyer_a")
    r = up(a, [("정상쪽.pdf", scan_pdf_pages(_scan_pages()))], matter_id=m["matter_id"])
    job_id = r.json()["jobs"][0]["job_id"]
    run_worker()
    job = a.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done", job
    assert job["parse_meta"]["suspect_pages"] == []
