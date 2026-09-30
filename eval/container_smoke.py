"""컨테이너 스택 스모크 테스트: 프록시(HTTPS) 경유 로그인 → 사건 등록 → DOCX 업로드 → 오프라인 워커 처리 → 열람 → 삭제.

사용:  backend/.venv/Scripts/python eval/container_smoke.py [https://localhost:8443]
- 개발용 계정(u_kim)의 비밀번호는 .dev-credentials.txt 에서 읽는다(출력하지 않음).
- 시험 데이터(사건·문서·업로드·작업)는 끝에 지운다.
"""
import sys
import time
from pathlib import Path

import httpx
import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import config  # noqa: E402
from backend.tests.helpers import make_docx  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "https://localhost:8443"
CASE_NO = "2026가합99101"
pw = next(l.split("\t")[2].strip() for l in (ROOT / ".dev-credentials.txt").read_text(encoding="utf-8").splitlines() if l.startswith("u_kim\t"))
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name, detail, flush=True)


def paragraphs():
    return ["준 비 서 면", f"사 건 {CASE_NO} 대여금", "원 고 컨테이너갑", "피 고 컨테이너을", "",
            "1. 상대방 주장의 요지", "원고는 컨테이너 스택 검증용 별도 약정에 따른 대여금을 청구합니다.",
            "2. 인정 및 부인", "가. 대여 사실은 인정합니다.", "나. 그 밖의 주장은 부인합니다.",
            "3. 쟁점별 반박", "가. 약정 성립에 대한 반박", "원고가 주장하는 약정은 서면으로 확인되지 않고 입증책임은 원고에게 있습니다.",
            "4. 입증방법", "을 제1호증 대화 기록", "5. 결론", "원고의 청구는 기각되어야 합니다.", "2026. 4. 1.", "피고 소송대리인 변호사 홍길동"]


with httpx.Client(base_url=BASE, verify=False, timeout=60) as c:
    r = c.get("/api/health")
    check("프록시 경유 헬스체크", r.status_code == 200, BASE)
    home = c.get("/")
    check("화면(정적 파일) 제공", home.status_code == 200 and "root" in home.text)
    asset = [a for a in home.text.split('"') if a.startswith("/assets/") and a.endswith(".js")]
    check("화면 스크립트 로드", asset and c.get(asset[0]).status_code == 200, asset[0] if asset else "")

    r = c.post("/api/auth/login", json={"user_id": "u_kim", "password": pw})
    check("로그인", r.status_code == 200)
    cookie = r.headers.get("set-cookie", "").lower()
    check("세션 쿠키 HttpOnly·Secure·SameSite", all(k in cookie for k in ("httponly", "secure", "samesite=lax")))
    h = {"X-CSRF-Token": r.json()["csrf_token"]}
    check("CSRF 없는 변경 요청 거부", c.post("/api/auth/logout").status_code == 403)

    r = c.post("/api/matters", headers=h, json=dict(case_no=CASE_NO, case_type="대여금", plaintiff="컨테이너갑", defendant="컨테이너을", our_side="피고", access_group="G-A"))
    check("사건 등록", r.status_code == 201, r.text[:80])

    r = c.post("/api/uploads", headers=h, data={"access_group": "G-A"}, files=[("files", ("컨테이너 시험 준비서면.docx", make_docx(paragraphs()), "application/octet-stream"))])
    check("업로드 접수", r.status_code == 201, str(r.json().get("counts")))
    job_id = r.json()["jobs"][0]["job_id"]

    t0, job = time.time(), None
    while time.time() - t0 < 240:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "needs_review"):
            break
        time.sleep(2)
    check("오프라인 워커가 변환·색인 완료", job["status"] == "done", f"{job['status']} {job.get('reason') or ''} ({time.time() - t0:.0f}초)")
    check("문서 안 사건번호로 자동 지정", job.get("doc_kind") == "준 비 서 면" and job.get("matter_id"), str(job.get("matter_id")))

    if job["status"] == "done":
        d = c.get(f"/api/documents/{job['doc_id']}").json()
        check("문서 열람(본문·색인 단위)", "컨테이너 스택 검증용" in d["full_text"] and "1. 상대방 주장의 요지" in d["sections"])
        with psycopg.connect(config.OWNER_DSN) as o:
            dims = o.execute("select vector_dims(embedding), (select count(*) from doc_chunks where doc_id=%s) from documents where doc_id=%s", (job["doc_id"], job["doc_id"])).fetchone()
        check("벡터 저장(1024차원)과 청크", dims[0] == 1024 and dims[1] >= 5, f"차원 {dims[0]}, 청크 {dims[1]}")
        check("삭제(변호사)", c.delete(f"/api/documents/{job['doc_id']}", headers=h).status_code == 200)

# 정리
with psycopg.connect(config.OWNER_DSN, autocommit=True) as o:
    o.execute("delete from documents where matter_id in (select matter_id from matters where case_no = %s)", (CASE_NO,))
    o.execute("delete from ingest_batches where access_group = 'G-A' and created_by = 'u_kim' and batch_id in (select batch_id from ingest_jobs where orig_name like '컨테이너 시험%%')")
    o.execute("delete from uploaded_files where orig_name like '컨테이너 시험%%'")
    o.execute("delete from matters where case_no = %s", (CASE_NO,))
print("\n결과:", f"{sum(results)}/{len(results)} 통과")
sys.exit(0 if all(results) else 1)
