"""Builds the human-readable "why this job matches" text and determines
which resume experience entries are relevant — the non-numeric half of
Phase 8's output.

The `job_matches` table has a single `missing_skills` text column (see
src/database/models.py, fixed in Phase 3), so required vs preferred missing
skills are both stored there as one JSON object —
{"required": [...], "preferred": [...]} — rather than in two columns.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from src.matching.scorer import ScoreBreakdown
from src.processing.eligibility import EligibilityResult


@dataclass
class MatchPresentation:
    matching_skills_json: str
    missing_skills_json: str
    match_explanation: str


def relevant_experience(profile, matching_skills: set[str]) -> list[str]:
    """Resume experience entries that demonstrate at least one matching
    skill — never invents relevance, only reports overlap that already
    exists in config/profile.yaml's skills_demonstrated lists."""
    relevant = []
    for exp in profile.experience:
        if matching_skills & set(exp.skills_demonstrated):
            relevant.append(f"{exp.title} at {exp.company}")
    return relevant


def build_match_explanation(
    breakdown: ScoreBreakdown,
    profile,
    eligibility_result: EligibilityResult,
    company_name: str,
) -> str:
    skill_match = breakdown.skill_match
    parts: list[str] = []

    if skill_match.matching_skills:
        shown = skill_match.matching_skills[:6]
        parts.append(f"Matches your experience with {', '.join(shown)}")

    if skill_match.combo_bonus_matches:
        combo_text = "; ".join(" + ".join(combo) for combo in skill_match.combo_bonus_matches[:2])
        parts.append(f"Strong alignment with your {combo_text} background")

    relevant = relevant_experience(profile, set(skill_match.matching_skills))
    if relevant:
        parts.append(f"Relevant experience: {', '.join(relevant[:3])}")

    if skill_match.missing_required_skills:
        parts.append(f"Missing required: {', '.join(skill_match.missing_required_skills[:4])}")

    if eligibility_result.warnings:
        parts.append("; ".join(eligibility_result.warnings))

    if not parts:
        parts.append(f"General fit for this role at {company_name} based on your background")

    return ". ".join(parts) + "."


def build_match_presentation(
    breakdown: ScoreBreakdown,
    profile,
    eligibility_result: EligibilityResult,
    company_name: str,
) -> MatchPresentation:
    skill_match = breakdown.skill_match
    missing = {
        "required": skill_match.missing_required_skills,
        "preferred": skill_match.missing_preferred_skills,
    }
    explanation = build_match_explanation(breakdown, profile, eligibility_result, company_name)
    return MatchPresentation(
        matching_skills_json=json.dumps(skill_match.matching_skills),
        missing_skills_json=json.dumps(missing),
        match_explanation=explanation,
    )
