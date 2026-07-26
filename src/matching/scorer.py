"""Combines skill, experience, title, education, location, semantic, and
freshness scores into one weighted total — the only place a numeric score
gets produced. All inputs are deterministic (regex/lookup-based) except the
semantic sub-score, which comes from a local sentence-transformer model
(src/matching/semantic_matcher.py), never an LLM asked to "judge" a score.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher

from src.matching.semantic_matcher import build_candidate_corpus, semantic_score
from src.matching.skill_matcher import SkillMatchResult, match_skills
from src.processing.eligibility import EligibilityResult
from src.processing.normalize import NormalizedJob

DEFAULT_WEIGHTS = {
    "skills": 0.30,
    "experience": 0.20,
    "title": 0.15,
    "semantic": 0.10,
    "education": 0.10,
    "location": 0.10,
    "freshness": 0.05,
}

SCORE_BANDS = [
    ("exceptional", 88, 100),
    ("strong", 80, 87),
    ("good", 70, 79),
    ("possible", 60, 69),
]
HIDE_BELOW = 60

_DEGREE_RANK = {"high_school": 1, "associate": 2, "bachelor": 3, "master": 4, "phd": 5}
_DEGREE_PATTERNS = {
    "phd": r"\bph\.?d\.?\b|\bdoctorate\b",
    "master": r"\bmaster'?s degree\b|\bmasters degree\b|\bm\.s\.\b",
    "bachelor": r"\bbachelor'?s degree\b|\bbachelors degree\b|\bb\.s\.\b|\bundergraduate degree\b",
    "associate": r"\bassociate'?s degree\b",
    "high_school": r"\bhigh school diploma\b",
}


@dataclass
class ScoreBreakdown:
    total_score: float
    skills_score: float
    experience_score: float
    title_score: float
    education_score: float
    location_score: float
    semantic_score: float
    freshness_score: float
    skill_match: SkillMatchResult
    band: str


def score_band(total_score: float) -> str:
    for name, low, high in SCORE_BANDS:
        if low <= total_score <= high:
            return name
    return "hidden" if total_score < HIDE_BELOW else "possible"


# ---------- Location ----------

def score_location(state: str | None, workplace_type: str | None) -> float:
    """California -> full score, remote-US -> high, other US -> moderate.
    (Outside-US jobs are rejected in Phase 7 before reaching the scorer;
    this only handles the three cases the spec actually asks it to rank.)"""
    if workplace_type == "remote":
        return 90.0
    if state == "CA":
        return 100.0
    return 70.0


# ---------- Experience ----------

def score_experience(candidate_years: float, required_min: float | None) -> float:
    if required_min is None or required_min <= 0:
        return 85.0  # no explicit requirement — assume a reasonable fit
    if candidate_years >= required_min:
        return 100.0
    ratio = candidate_years / required_min
    return max(40.0, ratio * 100)  # floor so a near-miss isn't zeroed out


# ---------- Title ----------

def score_title(normalized_title: str, target_titles: list[str]) -> float:
    if not target_titles:
        return 50.0
    lower_title = normalized_title.lower()
    title_words = set(re.findall(r"[a-z0-9]+", lower_title))
    best = 0.0
    for target in target_titles:
        lower_target = target.lower()
        sequence_ratio = SequenceMatcher(None, lower_title, lower_target).ratio()
        target_words = set(re.findall(r"[a-z0-9]+", lower_target))
        overlap_ratio = len(title_words & target_words) / len(title_words | target_words) if title_words or target_words else 0.0
        best = max(best, sequence_ratio, overlap_ratio)
    return best * 100


# ---------- Education ----------

def required_degree_level(description: str | None) -> str | None:
    if not description:
        return None
    lower = description.lower()
    for level in ("phd", "master", "bachelor", "associate", "high_school"):
        if re.search(_DEGREE_PATTERNS[level], lower):
            return level
    return None


def score_education(
    description: str | None, highest_completed_level: str = "bachelor", in_progress_level: str | None = "master"
) -> float:
    required = required_degree_level(description)
    if required is None:
        return 90.0  # no explicit requirement stated
    required_rank = _DEGREE_RANK[required]
    have_rank = _DEGREE_RANK.get(highest_completed_level, 0)
    in_progress_rank = _DEGREE_RANK.get(in_progress_level, have_rank) if in_progress_level else have_rank
    if required_rank <= have_rank:
        return 100.0
    if required_rank <= in_progress_rank:
        return 75.0  # currently pursuing the required level, not yet completed
    return 40.0


# ---------- Freshness ----------

def score_freshness(posted_at: datetime | None, lookback_days: int, reference_time: datetime | None = None) -> float:
    if posted_at is None:
        return 50.0  # unknown — neutral, don't reward or punish missing data
    reference_time = reference_time or datetime.now(timezone.utc)
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    age_days = (reference_time - posted_at).days
    if lookback_days <= 0:
        return 50.0
    ratio = max(0.0, 1 - age_days / lookback_days)
    return ratio * 100


# ---------- Top-level ----------

def score_job(
    normalized_job: NormalizedJob,
    profile,  # src.resume.profile_builder.CandidateProfile
    alias_lookup: dict[str, str],
    skill_combinations: list[list[str]] | None = None,
    target_titles: list[str] | None = None,
    weights: dict[str, float] | None = None,
    lookback_days: int = 30,
    reference_time: datetime | None = None,
    semantic_model=None,
) -> ScoreBreakdown:
    weights = weights or DEFAULT_WEIGHTS
    candidate_skills = set(profile.all_skills())
    candidate_years = float(profile.eligibility.get("max_years_experience_have", 0) or 0)

    skill_match = match_skills(
        normalized_job.description, normalized_job.title, candidate_skills, alias_lookup, skill_combinations
    )

    required_experience_min = normalized_job.experience_min
    experience_score = score_experience(candidate_years, required_experience_min)

    title_score = score_title(normalized_job.normalized_title or normalized_job.title, target_titles or [])

    education_score = score_education(normalized_job.description)

    location_score = score_location(normalized_job.state, normalized_job.workplace_type)

    freshness = score_freshness(normalized_job.posted_at, lookback_days, reference_time)

    candidate_text = build_candidate_corpus(profile)
    job_text = f"{normalized_job.title} {normalized_job.description or ''}"
    semantic = semantic_score(candidate_text, job_text, model=semantic_model)

    total = (
        skill_match.skills_score * weights["skills"]
        + experience_score * weights["experience"]
        + title_score * weights["title"]
        + semantic * weights["semantic"]
        + education_score * weights["education"]
        + location_score * weights["location"]
        + freshness * weights["freshness"]
    )
    total = max(0.0, min(100.0, total))

    return ScoreBreakdown(
        total_score=total,
        skills_score=skill_match.skills_score,
        experience_score=experience_score,
        title_score=title_score,
        education_score=education_score,
        location_score=location_score,
        semantic_score=semantic,
        freshness_score=freshness,
        skill_match=skill_match,
        band=score_band(total),
    )
