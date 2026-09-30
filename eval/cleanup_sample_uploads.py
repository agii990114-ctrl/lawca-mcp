"""가상 시험 문서(sample-uploads)를 올려 본 흔적을 지운다: 문서·청크·벡터, 업로드 작업, 원본 파일.

  backend/.venv/Scripts/python eval/cleanup_sample_uploads.py

sample-uploads/ 의 파일명(폴더 안 파일과 그 ZIP 내부 이름)과 일치하는 것만 지운다. 다른 문서는 건드리지 않는다.
개발용 스택(docker compose up -d)이 떠 있어야 하며, 원본 파일은 api 컨테이너를 통해 볼륨에서 지운다.
"""
import subprocess
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import config  # noqa: E402

names = {p.name for p in (ROOT / "sample-uploads").glob("*") if p.suffix.lower() != ".txt"}
if not names:
    sys.exit("sample-uploads/ 에 시험 파일이 없습니다(먼저 eval/make_sample_uploads.py).")

with psycopg.connect(config.OWNER_DSN, autocommit=True) as o:
    # 업로드 작업의 파일명은 '이름' 또는 'ZIP이름/내부경로' 형태다
    jobs = o.execute("select job_id, batch_id, file_id from ingest_jobs where orig_name = any(%s) or split_part(orig_name, '/', 1) = any(%s)",
                     (list(names), list(names))).fetchall()
    batches = sorted({j[1] for j in jobs})
    file_ids = [j[2] for j in jobs if j[2]]
    paths = [r[0] for r in o.execute("select stored_path from uploaded_files where file_id = any(%s)", (file_ids,))] if file_ids else []
    docs = o.execute("select doc_id from documents where source_file_id = any(%s)", (file_ids,)).fetchall() if file_ids else []
    o.execute("delete from documents where source_file_id = any(%s)", (file_ids,))      # 청크·벡터·태그는 함께 삭제
    o.execute("delete from ingest_jobs where job_id = any(%s)", ([j[0] for j in jobs],))
    o.execute("delete from ingest_batches b where batch_id = any(%s) and not exists (select 1 from ingest_jobs j where j.batch_id = b.batch_id)", (batches,))
    o.execute("delete from uploaded_files where file_id = any(%s)", (file_ids,))
    remaining = {p for p in paths if o.execute("select 1 from uploaded_files where stored_path = %s", (p,)).fetchone()}

removable = [p for p in paths if p not in remaining]
if removable:
    r = subprocess.run(["docker", "compose", "exec", "-T", "api", "sh", "-c", 'while read f; do rm -f "/data/blobs/$f"; done'],
                       input="\n".join(removable) + "\n", text=True, capture_output=True, cwd=str(ROOT))
    if r.returncode != 0:
        print("원본 파일 삭제 실패(스택이 떠 있는지 확인):", r.stderr.strip()[:200])
print(f"삭제: 문서 {len(docs)}건, 작업 {len(jobs)}건, 묶음 {len(batches)}개, 원본 파일 {len(removable)}개")
