"""로컬 임베딩(multilingual-e5-large, fastembed/ONNX). 외부 전송 없음. 워커 프로세스에서 한 번만 로드한다."""
import math

from .. import config

_model = None


def _get():
    global _model
    if _model is None:
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name=config.EMBED_MODEL)
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
