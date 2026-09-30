"""로컬 임베딩(multilingual-e5-large, fastembed/ONNX). 외부 전송 없음. 워커 프로세스에서 한 번만 로드한다."""
import math
import os
import shutil
from pathlib import Path

from .. import config

_model = None


def materialize_symlinks(cache_dir: str | None) -> int:
    """허깅페이스 캐시의 심볼릭 링크를 같은 볼륨 안의 실제 파일(하드링크, 안 되면 복사)로 바꾼다.

    리눅스에서는 snapshots/<해시>/파일 이 blobs/ 를 가리키는 링크인데, 최신 onnxruntime은 외부 가중치 파일
    (model.onnx_data)의 실제 경로가 모델 폴더 밖이면 로딩을 거부한다. 링크를 실제 파일로 바꾸면 해결된다.
    """
    if not cache_dir:
        return 0
    n = 0
    for snap in Path(cache_dir).glob("models--*/snapshots/*"):
        for link in snap.rglob("*"):
            if link.is_symlink():
                target = link.resolve()
                if not target.is_file():
                    continue
                link.unlink()
                try:
                    os.link(target, link)
                except OSError:
                    shutil.copy2(target, link)
                n += 1
    return n


def _get():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        materialize_symlinks(config.MODEL_CACHE)
        _model = TextEmbedding(model_name=config.EMBED_MODEL, cache_dir=config.MODEL_CACHE, local_files_only=config.OFFLINE)
    return _model


def embed_passages(texts: list[str]) -> list[list[float]]:
    return [list(map(float, v)) for v in _get().passage_embed(texts)]


def mean_vector(vectors: list[list[float]]) -> list[float]:
    """문서 전체 벡터: 청크 벡터의 평균(정규화). 임베딩 모델의 입력 길이 한계를 넘는 긴 문서를 대표한다."""
    n = len(vectors)
    m = [sum(col) / n for col in zip(*vectors)]
    norm = math.sqrt(sum(x * x for x in m)) or 1.0
    return [x / norm for x in m]


def vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
