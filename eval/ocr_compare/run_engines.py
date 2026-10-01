"""Tesseract(kor)와 EasyOCR를 합성 스캔 페이지에 돌려 쪽별 인식 텍스트와 시간을 저장한다.
사용(비교용 가상환경): python run_engines.py <작업폴더> <tesseract.exe 경로> <tessdata 폴더>
"""
import json, subprocess, sys, time
from pathlib import Path

work = Path(sys.argv[1]); tess = sys.argv[2]; tessdata = sys.argv[3]
out = {}
import easyocr
t0 = time.time()
reader = easyocr.Reader(["ko", "en"], gpu=False, model_storage_directory=str(work / "easyocr_models"), download_enabled=True, verbose=False)
print(f"EasyOCR 모델 로드 {time.time()-t0:.0f}초", flush=True)
for cond in ("easy", "hard"):
    pages = sorted((work / "pages" / cond).glob("p*.png"))
    res = {"tesseract": {"text": [], "sec": []}, "easyocr": {"text": [], "sec": []}}
    for p in pages:
        t = time.time()
        r = subprocess.run([tess, str(p), "stdout", "-l", "kor", "--tessdata-dir", tessdata, "--psm", "6"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        res["tesseract"]["sec"].append(time.time() - t); res["tesseract"]["text"].append("".join(r.stdout.split()))
        t = time.time()
        lines = reader.readtext(str(p), detail=0, paragraph=False)
        res["easyocr"]["sec"].append(time.time() - t); res["easyocr"]["text"].append("".join("".join(lines).split()))
        print(cond, p.name, f"tess {res['tesseract']['sec'][-1]:.1f}s easy {res['easyocr']['sec'][-1]:.1f}s", flush=True)
    out[cond] = res
json.dump(out, open(work / "engines_out.json", "w", encoding="utf-8"), ensure_ascii=False)
