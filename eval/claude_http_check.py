"""실제 Claude Code(헤드리스)를 웹에서 발급한 개인 토큰으로 컨테이너 MCP 서버(HTTP)에 연결해 본다.

  server/.venv/Scripts/python eval/claude_http_check.py [사용자ID=u_kim] [주소=http://localhost:8081]

- 시험용 토큰을 발급해 쓰고 끝나면 폐기한다(토큰 값은 출력하지 않는다). claude CLI 사용량이 든다(약 $0.3~0.6).
- 주소는 인증서를 신뢰하지 않아도 되는 개발용 HTTP(8081)를 기본으로 쓴다.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import db  # noqa: E402

UID = sys.argv[1] if len(sys.argv) > 1 else "u_kim"
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8081"      # Claude가 접속할 MCP 주소(개발용 HTTP)
WEB = "https://localhost:8443"                                             # 웹 API(세션 쿠키가 Secure 속성이라 https로 호출한다)
creds = {l.split("\t")[0]: l.split("\t")[2].strip() for l in (ROOT / ".dev-credentials.txt").read_text(encoding="utf-8").splitlines() if l and l[0] != "#"}
CWD = ROOT / "eval" / "cwd"
CWD.mkdir(parents=True, exist_ok=True)
CFG = CWD / "mcp_http.json"
OWNER = db._BASE + f" user=lawca_owner password={db.ENV['LAWCA_OWNER_PASSWORD']}"

PROMPT = """lawca MCP 도구(list_matters, search_documents)를 사용해서 다음을 하고 결과만 짧게 보고하세요.
1) list_matters 로 내가 접근할 수 있는 사건이 몇 건인지 (limit=100)
2) search_documents 로 '채무자의 문자메시지 답장을 채무 승인으로 주장하며 시효중단을 다투는 사안'에 대응한 준비서면을 검색해 가장 관련 있는 문서 1건의 doc_id와 best_section 만 알려주세요. 본문은 인용하지 마세요."""

# 웹 로그인 → 개인 토큰 발급
web = httpx.Client(base_url=WEB, verify=False, timeout=60)
r = web.post("/api/auth/login", json={"user_id": UID, "password": creds[UID]})
h = {"X-CSRF-Token": r.json()["csrf_token"]}
t = web.post("/api/tokens", headers=h, json={"name": "Claude Code 연결 시험", "days": 1}).json()
CFG.write_text(json.dumps({"mcpServers": {"lawca": {"type": "http", "url": BASE + "/mcp", "headers": {"Authorization": f"Bearer {t['token']}"}}}}), encoding="utf-8")
try:
    cmd = [shutil.which("claude") or "claude", "-p", "--output-format", "stream-json", "--verbose", "--mcp-config", str(CFG), "--strict-mcp-config",
           "--allowedTools", "mcp__lawca__list_matters,mcp__lawca__search_documents,mcp__lawca__get_document", "--max-turns", "10", "--no-session-persistence"]
    p = subprocess.run(cmd, input=PROMPT, capture_output=True, text=True, encoding="utf-8", cwd=str(CWD), timeout=600)
    events = [json.loads(l) for l in p.stdout.splitlines() if l.strip().startswith("{")]
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    servers = {s["name"]: s.get("status") for s in init.get("mcp_servers", [])}
    calls = [(b["name"].replace("mcp__lawca__", ""), b["input"]) for e in events if e.get("type") == "assistant" for b in e["message"]["content"] if b.get("type") == "tool_use"]
    final = next((e for e in events if e.get("type") == "result"), {})
    print("MCP 서버 연결 상태:", servers)
    print("호출한 도구:", [c[0] for c in calls])
    print("최종 답변:\n" + (final.get("result") or "(없음)"))
    print(f"\n비용 ${final.get('total_cost_usd')}, 턴 {final.get('num_turns')}")
    with psycopg.connect(OWNER) as o:
        rows = o.execute("select tool, args->>'via' from audit_log where user_id=%s and (args->>'token_id')::bigint=%s and tool not like 'api:%%' order by id", (UID, t["token_id"])).fetchall()
        n = o.execute("select count(*) from matters m where access_group in (select group_id from user_groups where user_id=%s)", (UID,)).fetchone()[0]
    print("감사 로그(이 토큰):", rows)
    print(f"실제 {UID}의 접근 가능 사건 수(DB 기준): {n}")
finally:
    web.delete(f"/api/tokens/{t['token_id']}", headers=h)
    CFG.unlink(missing_ok=True)
    print("시험 토큰 폐기 완료")
