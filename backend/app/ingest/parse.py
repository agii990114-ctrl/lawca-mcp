"""문서 → 텍스트. kordoc CLI(로컬 실행, 외부 통신 없음)를 하위 프로세스로 호출한다.

- 텍스트 PDF·HWP·HWPX·DOCX는 바로 추출하고, 스캔 PDF·이미지는 내장 OCR(PP-OCRv5)로 처리한다.
- KORDOC_OFFLINE=1 로 실행해 모델 다운로드 등 외부 통신을 막는다(모델은 최초 1회 미리 받아 둔다).
- 원본 이름으로 파일을 만들지 않는다: 저장소의 해시 경로 파일을 임시 디렉터리에 확장자만 붙여 복사해 넘긴다.
"""
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from .. import config
from . import storage


class ParseError(Exception):
    """사용자에게 보여 줄 수 있는 파싱 실패 사유"""


def _friendly(stderr: str, returncode: int) -> str:
    low = stderr.lower()
    if "password" in low or "encrypted" in low or "암호" in stderr:
        return "암호로 보호된 문서라 열 수 없습니다. 암호를 해제한 뒤 다시 올려 주세요."
    if "drm" in low or "문서보안" in stderr:
        return "한컴 문서보안(DRM)이 적용된 문서라 열 수 없습니다. 보안을 해제한 뒤 다시 올려 주세요."
    # kordoc이 내놓는 원시 오류(JSON 덩어리)는 사용자에게 보여 주지 않는다. 상세는 워커 로그에 남는다.
    return f"문서를 읽지 못했습니다. 파일이 손상되었거나 지원되지 않는 형식일 수 있습니다(변환 도구 종료 코드 {returncode}). 원본을 확인해 다시 올려 주세요."


def parse_file(stored_path: str, ext: str) -> tuple[str, dict]:
    src = storage.blob_path(stored_path)
    workdir = storage.TMP / f"parse-{uuid.uuid4().hex}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        tmp_in = workdir / f"input{ext}"
        shutil.copyfile(src, tmp_in)
        out = workdir / "out.md"
        cmd = ["node", str(config.KORDOC_CLI), str(tmp_in), "--ocr", "--plain", "--no-images", "--silent", "-o", str(out)]
        env = dict(os.environ, KORDOC_OFFLINE="1")
        t0 = time.time()
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=config.PARSE_TIMEOUT_SEC, env=env, cwd=str(config.KORDOC_CLI.parent))
        except subprocess.TimeoutExpired:
            raise ParseError(f"처리 시간이 {config.PARSE_TIMEOUT_SEC}초를 넘었습니다(매우 크거나 복잡한 문서).")
        except FileNotFoundError:
            raise ParseError("문서 변환 도구(node/kordoc)를 찾을 수 없습니다. 관리자에게 문의하세요.")
        if p.returncode != 0 or not out.exists():
            raise ParseError(_friendly(p.stderr + p.stdout, p.returncode))
        text = out.read_text(encoding="utf-8", errors="replace")
        return text, {"parser": "kordoc", "seconds": round(time.time() - t0, 1), "chars": len(text)}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
