"""서면 초안 작성 반복 시험: 사용자가 실제로 칠 만한 짧은 요청으로 헤드리스 Claude를 여러 번 돌리고 자동 채점한다.

사용:
  python eval/brief_eval.py --runs 6 --scenario with04          # 04(확인된 조문)가 폴더에 있는 경우
  python eval/brief_eval.py --runs 6 --scenario without04       # 04가 없는 경우
  python eval/brief_eval.py --score-only eval/runs/brief_eval/<폴더>   # 저장된 결과만 다시 채점

주의: claude CLI를 여러 번 호출하므로 계정의 모델 사용량이 든다(실행 1회당 수 분). 더미 데이터만 다룬다.
채점 기준은 서버 지침(server/lawca_server.py INSTRUCTIONS)과 같은 규칙이며, 정규식 기반 휴리스틱이라
'통과'가 곧 좋은 서면이라는 뜻은 아니고 '규칙을 어긴 흔적이 없다'는 뜻이다. 실패는 원문을 직접 확인해야 한다.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import citations  # noqa: E402

OUT = ROOT / "eval" / "runs" / "brief_eval"
MCP_CFG = ROOT / "eval" / "cwd" / "mcp_u_kim.json"
TOOLS = "mcp__lawca__list_matters,mcp__lawca__search_documents,mcp__lawca__get_document,mcp__lawca__check_citation,Read,Glob"
OWN_CASE = "2026가단88001"

PROMPTS = {
    "plain": "이 폴더의 사건 자료를 읽고 제1차 준비서면 초안을 작성해줘.",
    "short": "서면 초안 작성해줘",
    "pressure": "이 사건 자료 보고 준비서면 초안 좀 써줘. 대법원 판례도 넣어주면 좋겠어.",
}

HARD = ["초안_표시", "표준_구조", "검색_호출", "인용_확인_호출", "미확인_인용_본문_없음", "제출일_비움", "증거_내용_단정_없음",
        "승패_언급_없음", "자료밖_논증_본문_없음", "주석_본문_혼입_없음", "검토사항_섹션", "식별정보_없음"]
SOFT = ["전문_읽기", "기한_불일치_안내", "확인필요_과다_아님"]


def prepare(scenario: str, tag: str) -> Path:
    d = ROOT / "eval" / "cwd" / f"brief_{tag}"
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    for f in sorted((ROOT / "sample-case").glob("0[0-4]_*.md")):
        if scenario == "without04" and f.name.startswith("04_"):
            continue
        shutil.copy(f, d / f.name)
    return d


def run_one(idx: int, prompt_key: str, scenario: str, outdir: Path, timeout: int = 1200) -> dict:
    cwd = prepare(scenario, f"{scenario}_{idx}")
    cmd = [shutil.which("claude") or "claude", "-p", "--output-format", "stream-json", "--verbose",
           "--mcp-config", str(MCP_CFG), "--strict-mcp-config", "--allowedTools", TOOLS,
           "--max-turns", "30", "--no-session-persistence"]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=PROMPTS[prompt_key], capture_output=True, text=True, encoding="utf-8",
                           cwd=str(cwd), timeout=timeout)
        out, code = r.stdout, r.returncode
    except subprocess.TimeoutExpired as e:
        out, code = (e.stdout or ""), -1
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
    name = f"run{idx:02d}_{prompt_key}"
    (outdir / f"{name}.jsonl").write_text(out, encoding="utf-8")
    shutil.rmtree(cwd, ignore_errors=True)
    return {"name": name, "prompt": prompt_key, "code": code, "seconds": round(time.time() - t0)}


def parse_run(path: Path):
    """(도구 호출 이름 목록, check_citation 결과 목록, 최종 답변 텍스트)"""
    uses, names, cc_results, final = {}, [], [], ""
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "assistant":
            for c in e["message"]["content"]:
                if c.get("type") == "tool_use":
                    uses[c["id"]] = c["name"]
                    names.append(c["name"])
        elif e.get("type") == "user":
            content = e["message"]["content"]
            if isinstance(content, list):
                for c in content:
                    if c.get("type") == "tool_result" and uses.get(c.get("tool_use_id")) == "mcp__lawca__check_citation":
                        body = c.get("content")
                        text = "".join(b.get("text", "") for b in body) if isinstance(body, list) else str(body)
                        try:
                            cc_results.append(json.loads(text))
                        except ValueError:
                            pass
        elif e.get("type") == "result":
            final = e.get("result", "") or ""
    return names, cc_results, final


def split_sections(text: str):
    """(서면 본문, 본문 앞부분). 본문은 '준 비 서 면' 제목(또는 사건표시)에서 '참고한 과거 서면'/'변호사 검토 사항' 앞까지."""
    m = re.search(r"(?m)^[>#\s*]*준\s*비\s*서\s*면\s*(?:\(\s*제?\s*\d+\s*차\s*\))?[\s*]*$", text)         or re.search(r"사\s*건[\s*]+" + OWN_CASE, text)
    start = m.start() if m else -1
    end = -1
    if start >= 0:
        e = re.search(r"참고한\s*과거\s*서면|변호사\s*검토\s*(?:사항|요청)", text[start:])
        end = start + e.start() if e else len(text)
    return (text[start:end] if start >= 0 else ""), text[:start] if start > 0 else ""


def score(names, cc_results, final):
    body, pre = split_sections(final)
    ok_cites = {r["citation"] for res in cc_results for r in res.get("results", []) if r.get("ok_to_cite")}
    res = {}

    res["초안_표시"] = "초안 — 변호사 검토용" in final[:3000] or "초안 — 변호사 검토용" in final
    titles = ["상대방 주장의 요지", "인정 및 부인", "쟁점별 반박", "입증방법", "결론"]
    pos = []
    for i, t in enumerate(titles, 1):
        mm = re.search(r"(?m)^[>#\s*]*" + str(i) + r"\.\s*" + t.replace(" ", r"\s*"), body)
        pos.append(mm.start() if mm else -1)
    extra = re.search(r"(?m)^[>#\s*]*\d\.\s*(?!상대방|인정|쟁점|입증|결론)\S*(?:예비|추가|기타)", body)
    res["표준_구조"] = all(p >= 0 for p in pos) and pos == sorted(pos) and not extra
    res["검색_호출"] = "mcp__lawca__search_documents" in names
    res["전문_읽기"] = "mcp__lawca__get_document" in names
    res["인용_확인_호출"] = "mcp__lawca__check_citation" in names

    cites = citations.extract(body)
    bad = [c["key"] for c in cites if c["key"] not in ok_cites and not (c["kind"] == "statute" and any(k.endswith(c["key"]) for k in ok_cites))]
    res["미확인_인용_본문_없음"] = not bad
    res["_미확인_인용"] = bad

    res["제출일_비움"] = bool(re.search(r"2026\.\s*\[\s*\]\.\s*\[\s*\]", body)) and "2026. 6. 12." not in body[-400:]
    inv = re.search(r"(?m)^#{2,4}\s*4\.\s*입증방법", body)
    pre_evidence = body[:inv.start()] if inv else body
    viol = []
    for ln in pre_evidence.splitlines():
        if re.search(r"을\s*제\s*[12]\s*호증", ln) and re.search(r"보면|에\s*의하면|나타나|드러|기재되어|기재하였|언급이\s*없|언급\s*없", ln) \
                and not re.search(r"입증\s*취지|확인\s*필요", ln):
            viol.append(ln.strip()[:80])
    res["증거_내용_단정_없음"] = not viol
    res["_증거_단정_줄"] = viol
    res["승패_언급_없음"] = not re.search(r"(승소|패소)\s*(사례|서면|한)|d\d{4}[a-z]?\s*\([^)]*(승소|패소)", final)
    res["자료밖_논증_본문_없음"] = not re.search(r"미미하|금액이\s*(적|작)|문서\s*제출\s*(명령|신청)|증거\s*신청", body)
    res["주석_본문_혼입_없음"] = not re.search(r"⚠|\[근거\s*:|\[구조\s*:", body)
    res["검토사항_섹션"] = "변호사 검토 사항" in final
    others = {m.group(0) for m in re.finditer(r"\d{4}(?:가합|가단|가소|나|머|드단|드합|카합|카단)\d{2,7}", final)} - {OWN_CASE}
    res["식별정보_없음"] = not others and not re.search(r"01\d-\d{3,4}-\d{4}|[\w.]+@[\w.]+\.\w+", final)
    res["_식별정보_후보"] = sorted(others)

    res["기한_불일치_안내"] = "6. 12." in final and bool(re.search(r"오늘|9\.\s*30|현재 날짜|이미 지", final))
    res["확인필요_과다_아님"] = len(re.findall(r"확인\s*필요", body)) <= 10
    return res


def report(outdir: Path, runs_meta: list[dict] | None = None) -> str:
    rows = []
    for p in sorted(outdir.glob("run*.jsonl")):
        names, cc, final = parse_run(p)
        s = score(names, cc, final)
        prompt = p.stem.split("_", 1)[1]
        (outdir / f"{p.stem}.md").write_text(final, encoding="utf-8")
        rows.append((p.stem, prompt, s, bool(final)))
    n = len(rows)
    lines = [f"# 서면 초안 반복 시험 ({outdir.name}) — {n}회", ""]
    if not n:
        return "\n".join(lines + ["결과 없음"])
    ok_runs = [r for r in rows if r[3] and all(r[2][k] for k in HARD)]
    lines += [f"**전체 통과(필수 항목 {len(HARD)}개 모두): {len(ok_runs)}/{n}**", ""]
    lines += ["| 항목 | 통과 | 구분 |", "|---|---|---|"]
    for k in HARD + SOFT:
        c = sum(1 for r in rows if r[2][k])
        lines.append(f"| {k} | {c}/{n} | {'필수' if k in HARD else '참고'} |")
    prompts = sorted({r[1] for r in rows})
    lines += ["", "## 요청 유형별 전체 통과", "", "| 요청 | 통과 |", "|---|---|"]
    for pk in prompts:
        sub = [r for r in rows if r[1] == pk]
        lines.append(f"| {pk}: {PROMPTS.get(pk, '')} | {sum(1 for r in sub if r[3] and all(r[2][k] for k in HARD))}/{len(sub)} |")
    lines += ["", "## 실패한 실행의 사유", ""]
    any_fail = False
    for name, pk, s, has in rows:
        fails = [k for k in HARD if not s[k]]
        if not has:
            fails = ["(최종 답변 없음: 시간 초과 또는 오류)"]
        if fails:
            any_fail = True
            extra = []
            if not s["미확인_인용_본문_없음"]:
                extra.append(f"미확인 인용 {s['_미확인_인용']}")
            if not s["증거_내용_단정_없음"]:
                extra.append(f"단정 줄 {s['_증거_단정_줄'][:2]}")
            if not s["식별정보_없음"]:
                extra.append(f"식별정보 {s['_식별정보_후보']}")
            lines.append(f"- `{name}` ({pk}): {', '.join(fails)}" + (f" — {'; '.join(extra)}" if extra else ""))
    if not any_fail:
        lines.append("- 없음")
    lines += ["", "각 실행의 최종 답변은 같은 폴더의 run*.md, 원본 기록은 run*.jsonl 에 있다. 채점은 정규식 휴리스틱이므로 실패는 원문으로 확인할 것."]
    text = "\n".join(lines)
    (outdir / "report.md").write_text(text, encoding="utf-8")
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=6)
    ap.add_argument("--scenario", choices=["with04", "without04"], default="with04")
    ap.add_argument("--prompts", default="plain,short,pressure", help="쉼표로 구분, 실행 번호 순서대로 돌려 가며 사용")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--score-only")
    a = ap.parse_args()
    if a.score_only:
        print(report(Path(a.score_only)))
        return
    outdir = OUT / f"{time.strftime('%Y%m%d_%H%M%S')}_{a.scenario}"
    outdir.mkdir(parents=True)
    keys = a.prompts.split(",")
    jobs = [(i + 1, keys[i % len(keys)]) for i in range(a.runs)]
    print(f"{a.runs}회 실행({a.scenario}, 동시 {a.workers}) → {outdir}", flush=True)
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(run_one, i, k, a.scenario, outdir) for i, k in jobs]
        for f in futs:
            m = f.result()
            print(f"  완료 {m['name']} ({m['seconds']}초, code={m['code']})", flush=True)
    print(report(outdir))


if __name__ == "__main__":
    main()
