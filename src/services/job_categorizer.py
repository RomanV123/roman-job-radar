"""Keyword-based category classifier for the public job-board export.

This is deliberately separate from the resume-matching scorer in
src/matching/ -- that engine answers "how well does this job fit Roman,"
while this one answers "which broad section of the public board should this
job appear in," independent of anyone's resume. A job is assigned to at most
one category (checked in the priority order below) so the exported board
doesn't show duplicate listings across sections.

Cybersecurity is checked first deliberately -- it's the featured section on
the exported board, so a role like "Security Software Engineer" lands there
rather than in software_engineering.
"""
from __future__ import annotations

CATEGORY_ORDER = ["cybersecurity", "product_management", "data_analytics", "software_engineering"]

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "cybersecurity": (
        "security", "cybersecurity", "cyber security", "soc analyst", "threat",
        "vulnerability", "penetration test", "pentest", "incident response",
        "siem", "grc", "compliance analyst", "identity and access", " iam ",
        "infosec", "red team", "blue team", "appsec", "application security",
        "cloud security", "network security", "ciso", "risk analyst",
    ),
    "product_management": (
        "product manager", "product owner", "product analyst",
        "product operations", "product lead", "group product manager",
        "principal product manager", "associate product manager",
    ),
    "data_analytics": (
        "data analyst", "data scientist", "data engineer",
        "business intelligence", "analytics engineer", " bi analyst",
        "data analytics", "machine learning engineer", "ml engineer",
        "quantitative analyst", "research scientist",
    ),
    "software_engineering": (
        "software engineer", "software developer", "backend engineer",
        "backend developer", "frontend engineer", "frontend developer",
        "full stack", "full-stack", "fullstack", " sde ", "sde ", "sde,",
        "application developer", "devops engineer", "site reliability",
        "platform engineer", "infrastructure engineer", "mobile engineer",
        "ios engineer", "android engineer", "qa engineer", "systems engineer",
        "engineering manager",
    ),
}


def categorize_job(title: str) -> str | None:
    """Return the single best-matching category for a job title, or None if
    it doesn't fit any of the four board sections."""
    text = f" {title.lower()} "
    for category in CATEGORY_ORDER:
        if any(keyword in text for keyword in _KEYWORDS[category]):
            return category
    return None
