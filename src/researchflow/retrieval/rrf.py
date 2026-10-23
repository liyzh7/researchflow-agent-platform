"""Deterministic reciprocal-rank fusion without cross-scale score addition."""

from researchflow.retrieval.types import RetrievalHit


def reciprocal_rank_fusion(
    vector_hits: list[RetrievalHit],
    keyword_hits: list[RetrievalHit],
    *,
    rrf_k: int,
    top_k: int,
) -> list[RetrievalHit]:
    if rrf_k <= 0 or top_k <= 0:
        raise ValueError("rrf_k and top_k must be positive")

    combined: dict[object, RetrievalHit] = {}
    scores: dict[object, float] = {}
    for source, rank_field in ((vector_hits, "vector_rank"), (keyword_hits, "keyword_rank")):
        for rank, hit in enumerate(source, start=1):
            key = hit.segment_id
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
            existing = combined.get(key)
            if existing is None:
                combined[key] = hit.with_updates(**{rank_field: rank})
                continue
            updates: dict[str, object] = {rank_field: rank}
            if hit.vector_distance is not None:
                updates["vector_distance"] = hit.vector_distance
            if hit.keyword_score is not None:
                updates["keyword_score"] = hit.keyword_score
            combined[key] = existing.with_updates(**updates)

    fused = [
        hit.with_updates(retrieval_method="hybrid", rrf_score=scores[key])
        for key, hit in combined.items()
    ]
    fused.sort(
        key=lambda hit: (
            -(hit.rrf_score or 0.0),
            hit.vector_distance if hit.vector_distance is not None else float("inf"),
            -(hit.keyword_score or 0.0),
            str(hit.segment_id),
        )
    )
    return fused[:top_k]
