"""Semantic similarity between the candidate's resume and a job description,
using a local sentence-transformer model — never an external LLM API call,
per the privacy requirement that resume content never leaves the machine
unless explicitly enabled.

The model is loaded lazily and cached at module level so a batch pipeline
run pays the ~5s load cost once, not per job. Callers can inject their own
`model` (e.g. a stub) to keep unit tests fast and offline.
"""
from __future__ import annotations

from typing import Protocol

from src.logging_config import get_logger

logger = get_logger(__name__)

MODEL_NAME = "all-MiniLM-L6-v2"

# Empirically calibrated against this domain's job postings (see Phase 8
# notes): unrelated postings score ~0.10-0.20 cosine similarity, strong
# matches ~0.55-0.65. Rescaled to spread scores across the full 0-100 range
# instead of everything clustering in a narrow band.
_SIMILARITY_FLOOR = 0.10
_SIMILARITY_CEILING = 0.65

_model = None


class EmbeddingModel(Protocol):
    def encode(self, sentences: list[str], convert_to_tensor: bool = True): ...


def get_model() -> EmbeddingModel:
    """Loads the model from the local cache without touching the network —
    once downloaded, this system should never need connectivity again to
    score jobs (it's supposed to run entirely locally/offline). Confirmed
    this mattered by testing with network deliberately broken: without
    local_files_only, SentenceTransformer still tries to reach Hugging Face
    Hub to check for updates on every fresh process, and hangs for a long
    time on a broken/unreachable network instead of just using the cache.
    Falls back to a normal (network-requiring) load only the very first
    time, before anything has been cached yet."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        try:
            logger.info("Loading semantic similarity model %s from local cache", MODEL_NAME)
            _model = SentenceTransformer(MODEL_NAME, local_files_only=True)
        except Exception:
            logger.info(
                "Model %s not found in local cache — downloading (requires network, one-time only)", MODEL_NAME
            )
            _model = SentenceTransformer(MODEL_NAME)
    return _model


def build_candidate_corpus(profile) -> str:
    """Concatenates resume experience highlights and skills into one text
    blob representing the candidate, for comparison against job text."""
    parts: list[str] = []
    for exp in profile.experience:
        parts.append(exp.title)
        parts.extend(exp.highlights)
    parts.extend(profile.all_skills())
    return " ".join(parts)


def cosine_similarity(text_a: str, text_b: str, model: EmbeddingModel | None = None) -> float:
    from sentence_transformers import util

    model = model or get_model()
    embeddings = model.encode([text_a, text_b], convert_to_tensor=True)
    return float(util.cos_sim(embeddings[0], embeddings[1]).item())


def semantic_score(candidate_text: str, job_text: str, model: EmbeddingModel | None = None) -> float:
    """Returns a 0-100 score. Falls back to 0 (not an exception) if either
    text is empty — there's nothing to compare."""
    if not candidate_text or not job_text:
        return 0.0
    similarity = cosine_similarity(candidate_text, job_text, model=model)
    scaled = (similarity - _SIMILARITY_FLOOR) / (_SIMILARITY_CEILING - _SIMILARITY_FLOOR)
    return max(0.0, min(1.0, scaled)) * 100
