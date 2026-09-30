"""문서 파싱 검증 리포트: 폴더의 HWP/HWPX/DOCX/PDF/이미지를 실제 인제스트와 같은 경로로 처리하고 **통계만** 보고한다.

  backend/.venv/Scripts/python eval/parse_report.py <폴더|파일> [...] [--json 결과.json] [--show-snippet]

- 본문 내용은 출력하지 않는다(기본). 실제 의뢰인 문서로 돌려도 내용이 화면·로그에 남지 않는다.
  (--show-snippet 을 줄 때만 앞 60자를 보여 준다. 익명화된 문서에만 쓸 것.)
- 파일을 수정하지 않고, 외부 통신 없이 로컬에서만 처리한다(kordoc 오프라인).
- '한컴 저장본으로 보이는가'를 파일 구조로 판별한다(프로그램이 만든 서식·샘플과 구별하기 위해).
"""
import argparse
import json
import re
import struct
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import config  # noqa: E402
from backend.app.ingest import chunking, metadata, parse  # noqa: E402

EXTS = {".hwp", ".hwpx", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}
PII = {
    "주민번호": re.compile(r"\d{6}-[1-4]\d{6}"),
    "전화": re.compile(r"(?<![\d-])0\d{1,2}-\d{3,4}-\d{4}(?!\d)"),
    "이메일": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "계좌형": re.compile(r"(?<![\d-])\d{2,6}-\d{2,6}-\d{2,8}(?!\d)"),
    "사건번호": metadata.CASE_NO_RE,
    "금액": re.compile(r"\d[\d,\.]*\s*(?:억|만)?\s*원(?!고|본|금|인|칙|래|장)"),
}


def sniff(path: Path) -> dict:
    """파일 형식과 '한컴이 저장한 파일인지' 단서. 내용은 읽지 않는다(구조만)."""
    head = path.read_bytes()[:32]
    info = {"format": "알 수 없음", "origin": "-"}
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        info["format"] = "HWP 5.x(OLE 바이너리)" if path.suffix.lower() == ".hwp" else "OLE(구형 오피스)"
        info["origin"] = "한컴 바이너리 형식(생성 도구 판별 불가)"
    elif head.startswith(b"HWP Document File"):
        info["format"] = "HWP 3.x"
    elif head.startswith(b"%PDF"):
        info["format"] = "PDF"
    elif head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                if "mimetype" in names and b"hwp" in z.read("mimetype").lower():
                    info["format"] = "HWPX"
                    hpf = z.read("Contents/content.hpf").decode("utf-8", "replace") if "Contents/content.hpf" in names else ""
                    # 생성 도구는 요소 텍스트(<opf:meta name="creator">rhwp) 또는 속성(content="kordoc") 어느 쪽으로도 기록될 수 있다
                    tool = ""
                    for attrs, inner in re.findall(r'<opf:meta name="(?:generator|creator)"([^>]*)>([^<]*)', hpf):
                        c = re.search(r'content="([^"]*)"', attrs)
                        val = c.group(1).strip() if c and c.group(1).strip().lower() != "text" else inner.strip()
                        if val:
                            tool = val
                            break
                    ver = z.read("version.xml").decode("utf-8", "replace") if "version.xml" in names else ""
                    hancom = "Hancom Office" in ver
                    txt = z.getinfo("Preview/PrvText.txt").file_size if "Preview/PrvText.txt" in names else -1
                    img = z.getinfo("Preview/PrvImage.png").file_size if "Preview/PrvImage.png" in names else -1
                    if tool.lower() in {"kordoc", "rhwp", "pyhwpx", "hwpx-py"} or "generator" in hpf.lower() and tool:
                        info["origin"] = f"프로그램 생성 파일(생성 도구: {tool}) — 한컴 저장본 아님"
                    elif hancom and txt >= 20 and img >= 2000:
                        info["origin"] = "한컴 저장본으로 보임(확정은 아님: 프로그램 생성 여부를 완전히 배제할 수는 없음)"
                    else:
                        info["origin"] = "판별 불가(한컴 저장본 여부를 확인할 수 없음)"
                    info["signals"] = {"tool": tool or "-", "version_xml_says_hancom": hancom, "preview_text_bytes": txt, "preview_image_bytes": img}
                elif any(n.startswith("word/") for n in names):
                    info["format"] = "DOCX"
                else:
                    info["format"] = "ZIP 기반(알 수 없음)"
        except Exception as e:  # noqa: BLE001
            info["format"] = f"손상 가능({type(e).__name__})"
    elif head.startswith(b"\x89PNG"):
        info["format"] = "PNG"
    elif head[:3] == b"\xff\xd8\xff":
        info["format"] = "JPEG"
    return info


def analyze(path: Path, snippet: bool) -> dict:
    r = {"file": path.name, "ext": path.suffix.lower(), "size_kb": round(path.stat().st_size / 1024, 1)}
    r.update(sniff(path))
    t0 = time.time()
    try:
        text, meta = parse.parse_path(path, path.suffix.lower())
    except parse.ParseError as e:
        r.update(status="파싱 실패", reason=str(e)[:120], seconds=round(time.time() - t0, 1))
        return r
    r["seconds"] = round(time.time() - t0, 1)
    body = "".join(text.split())
    ok, why = metadata.text_quality(text)
    lines = text.splitlines()
    chunks = chunking.chunk_text(text)
    det = metadata.detect(text)
    r.update(
        status="성공" if ok else "품질 의심",
        quality_note=why or "-",
        chars=len(body),
        lines=sum(1 for l in lines if l.strip()),
        table_rows=sum(1 for l in lines if l.lstrip().startswith("|")),
        space_ratio=round((len(text) - len(body) - text.count("\n")) / max(len(text), 1), 3),
        chunks=len(chunks),
        chunk_labels=sorted({c[0].split(" > ")[0] for c in chunks})[:8],
        max_chunk=max((len(c[1]) for c in chunks), default=0),
        detected={k: v for k, v in det.items() if k != "case_no_candidates"},
        pii_candidates={k: len(rx.findall(text)) for k, rx in PII.items() if rx.search(text)},
    )
    if snippet:
        r["snippet"] = " ".join(text.split())[:60]
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--json")
    ap.add_argument("--show-snippet", action="store_true")
    a = ap.parse_args()
    files = []
    for p in map(Path, a.paths):
        files += sorted(f for f in (p.rglob("*") if p.is_dir() else [p]) if f.is_file() and f.suffix.lower() in EXTS)
    if not files:
        sys.exit("처리할 파일(HWP/HWPX/DOCX/PDF/이미지)이 없습니다.")
    out = []
    for f in files:
        r = analyze(f, a.show_snippet)
        out.append(r)
        print(f"\n■ {r['file']}  [{r['format']} · {r['size_kb']}KB]", flush=True)
        print(f"   생성 도구 단서: {r['origin']}")
        if r["status"] == "파싱 실패":
            print(f"   결과: 파싱 실패 — {r['reason']}  ({r['seconds']}s)")
            continue
        print(f"   결과: {r['status']}{'' if r['quality_note'] == '-' else ' — ' + r['quality_note']}  ({r['seconds']}s)")
        print(f"   글자 {r['chars']:,} · 줄 {r['lines']} · 표 행 {r['table_rows']} · 공백 비율 {r['space_ratio']}")
        print(f"   청크 {r['chunks']}개(최대 {r['max_chunk']}자) 구성: {', '.join(r['chunk_labels'])}")
        print(f"   자동 감지: {r['detected'] or '없음'}")
        print(f"   개인정보 후보(개수만): {r['pii_candidates'] or '없음'}")
        if "snippet" in r:
            print(f"   앞부분: {r['snippet']}")
    ok = sum(1 for r in out if r["status"] == "성공")
    print(f"\n요약: {len(out)}개 중 성공 {ok}, 품질 의심 {sum(1 for r in out if r['status'] == '품질 의심')}, 파싱 실패 {sum(1 for r in out if r['status'] == '파싱 실패')}")
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"결과 저장: {a.json}")


if __name__ == "__main__":
    main()
