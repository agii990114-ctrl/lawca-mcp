"""법제처 Open API 어댑터(server/law_api.py)를 실제 서비스에 시험한다. 인증값은 출력하지 않는다.
인증값: 프로젝트 .env 의 LAWCA_LAW_API_OC 또는 KOREAN-LAW-MCP. 사용: server/.venv/Scripts/python eval/law_api_live.py
"""
import json, os, sys
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
env = {}
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1); env[k.strip()] = v.strip()
oc = env.get("LAWCA_LAW_API_OC") or env.get("KOREAN-LAW-MCP") or env.get("KOREAN_LAW_MCP") or ""
if not oc:
    sys.exit("인증값이 .env 에 없습니다.")
os.environ["LAWCA_LAW_API_OC"] = oc
import law_api

def show(label, case_no):
    print(f"\n== {label}: {case_no}")
    try:
        r = law_api.find_case(case_no)
        print("find_case 결과:", r)
    except law_api.LawApiError as e:
        print("LawApiError:", e)

base = os.environ.get("LAWCA_LAW_API_BASE", law_api.DEFAULT_BASE)
r = httpx.get(f"{base}/lawSearch.do", params={"OC": oc, "target": "prec", "type": "JSON", "nb": "2004다59959"}, timeout=10)
print("HTTP", r.status_code, "content-type:", r.headers.get("content-type"), "길이:", len(r.text))
txt = r.text.replace(oc, "<OC>")
print("본문 앞부분:", txt[:600].replace("\n", " "))
try:
    d = r.json()
    def walk(o, depth=0):
        if isinstance(o, dict):
            for k, v in list(o.items())[:14]:
                print("  " * depth + f"{k}: {type(v).__name__}" + ("" if isinstance(v, (dict, list)) else f" = {str(v).replace(oc, '<OC>')[:50]}"))
                if depth < 2 and isinstance(v, (dict, list)): walk(v, depth + 1)
        elif isinstance(o, list) and o:
            print("  " * depth + f"[0] (총 {len(o)}건)"); walk(o[0], depth + 1)
    print("-- JSON 구조"); walk(d)
except ValueError:
    print("JSON 아님")
show("실재하는 판례(앞서 웹 검색으로 존재 확인)", "2004다59959")
show("확인되지 않았던 번호", "2001다3672")
