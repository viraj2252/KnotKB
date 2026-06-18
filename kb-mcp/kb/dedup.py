from dataclasses import dataclass


@dataclass
class DedupConfig:
    merge_threshold: float
    skip_threshold: float


def decide(best_similarity: float | None, cfg: DedupConfig) -> str:
    if best_similarity is None:
        return "created"
    if best_similarity >= cfg.skip_threshold:
        return "skipped"
    if best_similarity >= cfg.merge_threshold:
        return "merged"
    return "created"
