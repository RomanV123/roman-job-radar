"""Loads the editable candidate profile (config/profile.yaml) as the source of
truth for matching. config/profile.yaml always wins over parsed resume text —
if you correct a skill/date/title there, this module reflects that correction.

Also loads config/skills.yaml to normalize skill names/aliases.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from src.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_PROFILE_PATH = Path("config/profile.yaml")
DEFAULT_SKILLS_PATH = Path("config/skills.yaml")


class Education(BaseModel):
    institution: str
    degree: str
    minor: str | None = None
    modality: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    status: str | None = None


class Experience(BaseModel):
    company: str
    location: str | None = None
    title: str
    start_date: str | None = None
    end_date: str | None = None
    highlights: list[str] = Field(default_factory=list)
    skills_demonstrated: list[str] = Field(default_factory=list)


class Certification(BaseModel):
    name: str
    issuer: str | None = None


class CandidateProfile(BaseModel):
    candidate: dict[str, Any]
    education: list[Education]
    experience: list[Experience]
    projects: list[dict[str, Any]] = Field(default_factory=list)
    skills: dict[str, list[str]]
    certifications: list[Certification] = Field(default_factory=list)
    target_role_categories: list[str] = Field(default_factory=list)
    target_titles_freeform: list[str] = Field(default_factory=list)
    preferred_locations: dict[str, Any] = Field(default_factory=dict)
    employment_types: list[str] = Field(default_factory=list)
    eligibility: dict[str, Any] = Field(default_factory=dict)
    resume_version: str | None = None

    def all_skills(self) -> list[str]:
        seen: list[str] = []
        for group in self.skills.values():
            for skill in group:
                if skill not in seen:
                    seen.append(skill)
        return seen


def load_skill_dictionary(path: str | Path = DEFAULT_SKILLS_PATH) -> dict[str, dict[str, Any]]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return raw


def build_alias_lookup(skill_dict: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Maps a lowercased alias (or canonical name) -> canonical skill name."""
    lookup: dict[str, str] = {}
    for canonical, meta in skill_dict.items():
        lookup[canonical.lower()] = canonical
        for alias in meta.get("aliases", []) or []:
            lookup[alias.lower()] = canonical
    return lookup


def normalize_skill(raw_skill: str, alias_lookup: dict[str, str]) -> str | None:
    """Returns the canonical skill name, or None if unrecognized (i.e. not a
    vetted skill — we do not invent new skills from arbitrary text)."""
    return alias_lookup.get(raw_skill.strip().lower())


def load_profile(path: str | Path = DEFAULT_PROFILE_PATH) -> CandidateProfile:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Candidate profile not found at {path}. Run the resume parser to generate it first."
        )
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    profile = CandidateProfile.model_validate(raw)
    logger.info(
        "Loaded candidate profile for %s (%d skills, %d experience entries)",
        profile.candidate.get("name", "unknown"),
        len(profile.all_skills()),
        len(profile.experience),
    )
    return profile
