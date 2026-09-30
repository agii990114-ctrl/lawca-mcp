"""웹으로 올린 문서가 MCP 서버(에이전트용 도구)에서 검색·조회되는지 종단 확인.

  server/.venv/Scripts/python eval/upload_to_mcp_check.py

흐름: u_kim 이 웹 API로 시험 문서(sample-uploads/01)를 업로드 → 워커가 색인 → MCP 서버를 u_kim / u_park 신원으로 띄워
search_documents·get_document 호출 → 결과 확인 → 시험 문서 삭제. (개발용 스택이 떠 있어야 한다.)
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = next((ROOT / "sample-uploads").glob("01_*.docx"))
PY = str(ROOT / "server" / ".venv" / "Scripts" / "python.exe")
SERVER = str(ROOT / "server" / "lawca_server.py")
pw = next(l.split("\t")[2].strip() for l in (ROOT / ".dev-credentials.txt").read_text(encoding="utf-8").splitlines() if l.startswith("u_kim\t"))
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name, detail, flush=True)


def mcp_client(uid):
    env = dict(os.environ, PYTHONUTF8="1", LAWCA_USER_ID=uid)
    return Client(StdioServerParameters(command=PY, args=[SERVER], env=env, cwd=str(ROOT)), read_timeout_seconds=180)


async def call(c, tool, args):
    r = await c.call_tool(tool, args)
    return r.structured_content or json.loads(r.content[0].text)


async def mcp_part(doc_id):
    async with mcp_client("u_kim") as c:
        tools = sorted(t.name for t in (await c.list_tools()).tools)
        check("MCP 도구 목록", tools == ["get_document", "list_matters", "search_documents"], tools)
        res = await call(c, "search_documents", {"query": "변제 주장은 입증되지 않았고 이체 내역의 수취인이 원고가 아니다", "limit": 5})
        ids = [r["doc_id"] for r in res["results"]]
        check("웹으로 올린 문서가 MCP 검색 결과에 나온다", doc_id in ids, f"순위 {ids.index(doc_id) + 1 if doc_id in ids else '없음'} / {ids[:3]}")
        hit = next((r for r in res["results"] if r["doc_id"] == doc_id), {})
        check("검색 결과의 사건번호는 기본으로 가려진다", hit.get("case_no") == "[사건번호]", str(hit.get("case_no")))
        doc = await call(c, "get_document", {"doc_id": doc_id})
        check("get_document: 본문 마스킹(당사자·사건번호)", "[사건번호]" in doc["text"] and "소우진" not in doc["text"] and "2024가단62215" not in doc["text"],
              str(doc.get("masking_report")))
        check("get_document: 색인 단위(섹션) 제공", "1. 상대방 주장의 요지" in doc["sections"], str(doc["sections"][:3]))
    async with mcp_client("u_park") as c:
        res = await call(c, "search_documents", {"query": "변제 주장은 입증되지 않았고 이체 내역의 수취인이 원고가 아니다", "limit": 20})
        check("다른 접근 그룹(u_park)에는 검색되지 않는다", doc_id not in [r["doc_id"] for r in res["results"]])
        d = await call(c, "get_document", {"doc_id": doc_id})
        check("다른 접근 그룹은 조회도 거부(존재 비노출)", "error" in d and "text" not in d, d.get("error"))


with httpx.Client(base_url="https://localhost:8443", verify=False, timeout=120) as c:
    r = c.post("/api/auth/login", json={"user_id": "u_kim", "password": pw})
    h = {"X-CSRF-Token": r.json()["csrf_token"]}
    up = c.post("/api/uploads", headers=h, data={"access_group": "G-A"}, files=[("files", (SAMPLE.name, SAMPLE.read_bytes(), "application/octet-stream"))])
    job_id = up.json()["jobs"][0]["job_id"]
    t0, job = time.time(), None
    while time.time() - t0 < 180:
        job = c.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "needs_review"):
            break
        time.sleep(2)
    check("웹 업로드 → 워커 색인 완료", job["status"] == "done", f"{job['status']} {job.get('reason') or ''}")
    if job["status"] == "done":
        try:
            asyncio.run(mcp_part(job["doc_id"]))
        finally:
            c.delete(f"/api/documents/{job['doc_id']}", headers=h)
print("\n결과:", f"{sum(results)}/{len(results)} 통과")
sys.exit(0 if all(results) else 1)
