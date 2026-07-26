"""Deterministic skill matching — no LLM involved.

Scans a job's required vs preferred text (reusing Phase 7's section-splitting
heuristic) for mentions of the candidate's vetted skill vocabulary
(config/skills.yaml), then intersects against the candidate's own skills.

Guarantee: matching_skills can only ever be skills the candidate actually
has in config/profile.yaml — it's always an intersection, never invented.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.processing.eligibility import split_required_and_preferred

# How much of the skills sub-score comes from required vs preferred matches.
_REQUIRED_WEIGHT = 80.0
_PREFERRED_WEIGHT = 20.0
_BONUS_PER_COMBO = 5.0
_MAX_BONUS = 20.0
_MATCHES_FOR_FULL_CREDIT = 3  # matched-skill count at which the ratio-based score gets full weight


@dataclass
class SkillMatchResult:
    matching_skills: list[str] = field(default_factory=list)
    missing_required_skills: list[str] = field(default_factory=list)
    missing_preferred_skills: list[str] = field(default_factory=list)
    combo_bonus_matches: list[list[str]] = field(default_factory=list)
    skills_score: float = 0.0


def _find_mentioned_skills(text: str, alias_lookup: dict[str, str]) -> set[str]:
    """Returns the set of canonical skill names whose alias (or canonical
    name) appears in `text` as a whole-word/phrase match."""
    if not text:
        return set()
    lower = text.lower()
    found: set[str] = set()
    for alias, canonical in alias_lookup.items():
        pattern = r"\b" + re.escape(alias.lower()) + r"\b"
        if re.search(pattern, lower):
            found.add(canonical)
    return found


def check_skill_combinations(
    job_text: str, candidate_skills: set[str], alias_lookup: dict[str, str], combinations: list[list[str]]
) -> list[list[str]]:
    """A combo bonus applies when the job text mentions every phrase in a
    combo AND, for any phrase that maps to a real candidate skill, the
    candidate actually has it. Generic phrases in a combo (e.g. "network
    traffic") only need to appear in the text — they describe the flavor of
    work, not a discrete skill the candidate can "have"."""
    if not job_text:
        return []
    lower_text = job_text.lower()
    matched: list[list[str]] = []
    for combo in combinations:
        if not all(re.search(r"\b" + re.escape(phrase.lower()) + r"\b", lower_text) for phrase in combo):
            continue
        owns_all_real_skills = True
        for phrase in combo:
            canonical = alias_lookup.get(phrase.lower())
            if canonical and canonical not in candidate_skills:
                owns_all_real_skills = False
                break
        if owns_all_real_skills:
            matched.append(combo)
    return matched


def match_skills(
    description: str | None,
    title: str,
    candidate_skills: set[str],
    alias_lookup: dict[str, str],
    skill_combinations: list[list[str]] | None = None,
) -> SkillMatchResult:
    skill_combinations = skill_combinations or []
    full_text = f"{title} {description or ''}"

    if description:
        required_text, preferred_text = split_required_and_preferred(description)
        required_text = f"{title} {required_text}"  # title itself often signals required scope
    else:
        required_text, preferred_text = title, ""

    mentioned_required = _find_mentioned_skills(required_text, alias_lookup)
    mentioned_preferred = _find_mentioned_skills(preferred_text, alias_lookup)
    mentioned_all = mentioned_required | mentioned_preferred

    matching = sorted(mentioned_all & candidate_skills)
    missing_required = sorted(mentioned_required - candidate_skills)
    missing_preferred = sorted((mentioned_preferred - candidate_skills) - mentioned_required)

    combo_matches = check_skill_combinations(full_text, candidate_skills, alias_lookup, skill_combinations)

    if mentioned_required:
        required_ratio = len(mentioned_required & candidate_skills) / len(mentioned_required)
    else:
        # No vocabulary skills detected at all — this is NOT evidence of a
        # match. Defaulting this to 1.0 was a real bug: it gave unrelated
        # postings (e.g. "Account Executive") a perfect skills score just
        # because they don't mention any of our tracked skills. Score it
        # like a job with no detected required skills actually deserves:
        # zero on this axis, and let title/semantic scoring do the real
        # work of judging relevance from other angles.
        required_ratio = 0.0
    if mentioned_preferred:
        preferred_ratio = len(mentioned_preferred & candidate_skills) / len(mentioned_preferred)
    else:
        preferred_ratio = 0.0

    base_score = required_ratio * _REQUIRED_WEIGHT + preferred_ratio * _PREFERRED_WEIGHT

    # Dampen postings where the ratio looks perfect only because a single
    # incidental keyword matched (e.g. "Zero Trust" appearing in a security
    # vendor's boilerplate "about the company" text on an unrelated sales
    # posting) — found via live testing where this inflated Account
    # Executive roles above genuine multi-skill technical matches. Full
    # credit requires several matched skills, not just a high ratio of a
    # small mentioned set.
    match_count = len(matching)
    count_factor = min(1.0, match_count / _MATCHES_FOR_FULL_CREDIT)
    base_score *= 0.5 + 0.5 * count_factor

    bonus = min(len(combo_matches) * _BONUS_PER_COMBO, _MAX_BONUS)
    skills_score = min(100.0, base_score + bonus)

    return SkillMatchResult(
        matching_skills=matching,
        missing_required_skills=missing_required,
        missing_preferred_skills=missing_preferred,
        combo_bonus_matches=combo_matches,
        skills_score=skills_score,
    )
