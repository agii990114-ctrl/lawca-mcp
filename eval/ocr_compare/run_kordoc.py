"""현재 사용 중인 엔진(kordoc + PP-OCRv5)을 같은 페이지에 돌린다. 프로젝트의 backend 가상환경에서 실행.
사용: backend/.venv/Scripts/python eval/ocr_compare/run_kordoc.py <작업폴더>
쪽 단위 이미지를 한 장씩 변환(이미지 파일 입력)하고 시간을 잰다. 첫 쪽에는 프로세스 시작·모델 로드가 포함되므로 둘째 쪽부터의 평균도 함께 낸다.
"""
import json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app.ingest import parse

work = Path(sys.argv[1]); out = {}
for cond in ("easy", "hard"):
    texts, secs = [], []
    for p in sorted((work / "pages" / cond).glob("p*.png")):
        t = time.time()
        try:
            text, _ = parse.parse_path(p, ".png")
        except parse.ParseError as e:
            text = ""; print("실패", p.name, e)
        secs.append(time.time() - t); texts.append("".join(text.split()))
        print(cond, p.name, f"{secs[-1]:.1f}s", flush=True)
    out[cond] = {"kordoc": {"text": texts, "sec": secs}}
json.dump(out, open(work / "kordoc_out.json", "w", encoding="utf-8"), ensure_ascii=False)
