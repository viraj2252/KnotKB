from typing import Protocol

from kb.models import Fact


class Reranker(Protocol):
    def rerank(self, query: str,
               candidates: list[tuple[Fact, float]]) -> list[tuple[Fact, float]]: ...


class FastReranker:
    """Local cross-encoder reranker via fastembed (ONNX CPU, no API)."""

    def __init__(self, model: str = "BAAI/bge-reranker-base") -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder  # lazy
        self._model = TextCrossEncoder(model_name=model)

    def rerank(self, query, candidates):
        if not candidates:
            return []
        docs = [fact.content for fact, _ in candidates]
        scores = list(self._model.rerank(query, docs))
        ranked = sorted(zip(candidates, scores),
                        key=lambda cs: (-cs[1], cs[0][0].id))
        return [(fact, float(score)) for (fact, _old), score in ranked]
