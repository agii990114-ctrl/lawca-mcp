"""컨테이너 최초 준비: 임베딩 모델과 OCR 모델을 모델 볼륨에 내려받는다. **인터넷이 되는 환경에서 한 번만** 실행한다.

  docker compose --profile setup run --rm fetch-models

이후 워커는 외부 통신이 막힌 네트워크에서 캐시만 읽는다(LAWCA_OFFLINE=1, KORDOC_OFFLINE=1).
"""
import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import config

# 글자가 조금 들어 있는 작은 이미지: kordoc이 OCR 모델을 처음 쓰는 순간 내려받게 한다.
PROBE_PNG_B64 = "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"


def main():
    cache = config.MODEL_CACHE
    print(f"모델 캐시: {cache or '(기본 위치)'}", flush=True)

    print("1/2 임베딩 모델 내려받기:", config.EMBED_MODEL, flush=True)
    from fastembed import TextEmbedding
    m = TextEmbedding(model_name=config.EMBED_MODEL, cache_dir=cache, lazy_load=True)   # 내려받기만 하고 로딩은 뒤로 미룬다
    from .ingest.embed import materialize_symlinks
    fixed = materialize_symlinks(cache)      # 캐시의 심볼릭 링크를 실제 파일로(onnxruntime 로딩 조건)
    if fixed:
        print(f"   캐시 링크 {fixed}개를 실제 파일로 바꿨습니다.", flush=True)
    dims = len(next(iter(m.passage_embed(["모델 준비 확인"]))))
    print(f"   완료 (벡터 차원 {dims})", flush=True)

    print("2/2 OCR 모델 내려받기(kordoc PP-OCRv5)", flush=True)
    with tempfile.TemporaryDirectory() as d:
        png = Path(d) / "probe.png"
        png.write_bytes(base64.b64decode(PROBE_PNG_B64))
        env = {k: v for k, v in os.environ.items() if k != "KORDOC_OFFLINE"}    # 이 단계만 온라인
        p = subprocess.run(["node", str(config.KORDOC_CLI), str(png), "--silent", "-o", str(Path(d) / "out.md")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=600,
                           cwd=str(config.KORDOC_CLI.parent))
        if p.returncode != 0:
            sys.exit(f"OCR 모델 준비 실패: {(p.stderr or p.stdout)[-300:]}")
    print("   완료", flush=True)
    print("모든 모델을 준비했습니다. 이제 워커는 오프라인으로 실행할 수 있습니다.")


if __name__ == "__main__":
    main()
