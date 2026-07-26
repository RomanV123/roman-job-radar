from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.matching.explanation import build_match_explanation, build_match_presentation, relevant_experience
from src.matching.scorer import (
    required_degree_level,
    score_education,
    score_experience,
    score_freshness,
    score_job,
    score_location,
    score_title,
)
from src.matching.semantic_matcher import build_candidate_corpus, semantic_score
from src.matching.skill_matcher import check_skill_combinations, match_skills
from src.processing.eligibility import EligibilityResult
from src.processing.normalize import NormalizedJob
from src.resume.profile_builder import load_profile, load_skill_dictionary, build_alias_lookup

REFERENCE_TIME = datetime(2026, 7, 25, tzinfo=timezone.utc)


class FakeModel:
    """Deterministic stand-in for SentenceTransformer — avoids loading the
    real ~90MB model in most tests. Returns similarity based on shared
    words, which is enough to test the scaling logic without the real model."""

    def encode(self, sentences, convert_to_tensor=True):
        return [_FakeVector(s) for s in sentences]


class _FakeVector:
    def __init__(self, text: str):
        self.words = set(text.lower().split())


class _FakeUtil:
    @staticmethod
    def cos_sim(a, b):
        overlap = len(a.words & b.words)
        total = len(a.words | b.words) or 1
        return SimpleNamespace(item=lambda: overlap / total)


@pytest.fixture(autouse=True)
def patch_sentence_transformers_util(monkeypatch):
    import src.matching.semantic_matcher as sm

    monkeypatch.setattr("sentence_transformers.util.cos_sim", _FakeUtil.cos_sim, raising=False)
    yield


def make_normalized_job(**overrides) -> NormalizedJob:
    defaults = dict(
        external_id="1",
        source="greenhouse",
        title="Cybersecurity Analyst",
        normalized_title="Cybersecurity Analyst",
        description=(
            "Requirements: 1-3 years of experience with Splunk, SIEM, and network security. "
            "Bachelor's degree required. Preferred Qualifications: AWS experience is a plus."
        ),
        location="Sacramento, CA",
        state="CA",
        is_us=True,
        workplace_type="onsite",
        employment_type="full_time",
        salary_min=None,
        salary_max=None,
        experience_min=1.0,
        experience_max=3.0,
        posted_at=REFERENCE_TIME - timedelta(days=2),
        apply_url="https://example.com/jobs/1",
        source_url="https://example.com/jobs/1",
        content_hash="hash",
    )
    defaults.update(overrides)
    return NormalizedJob(**defaults)


@pytest.fixture(scope="module")
def profile():
    return load_profile()


@pytest.fixture(scope="module")
def alias_lookup():
    skill_dict = load_skill_dictionary()
    return build_alias_lookup(skill_dict)


# ---------- skill_matcher ----------

def test_match_skills_only_returns_owned_skills(alias_lookup):
    result = match_skills(
        "Requirements: Splunk, SIEM, and Kubernetes experience required.",
        "Analyst",
        candidate_skills={"Splunk", "SIEM"},  # candidate does NOT have Kubernetes
        alias_lookup=alias_lookup,
    )
    assert set(result.matching_skills) == {"Splunk", "SIEM"}
    assert "Kubernetes" not in result.matching_skills  # never claim an unowned skill


def test_match_skills_missing_required_vs_preferred(alias_lookup):
    description = "Requirements: Python and SQL required. Preferred Qualifications: AWS and Terraform preferred."
    result = match_skills(description, "Engineer", candidate_skills={"Python"}, alias_lookup=alias_lookup)
    assert "SQL" in result.missing_required_skills
    assert "AWS" in result.missing_preferred_skills
    assert "Terraform" in result.missing_preferred_skills
    assert "SQL" not in result.missing_preferred_skills


def test_match_skills_no_description_uses_title_only(alias_lookup):
    result = match_skills(None, "Splunk Analyst", candidate_skills={"Splunk"}, alias_lookup=alias_lookup)
    assert "Splunk" in result.matching_skills


def test_check_skill_combinations_requires_ownership_of_real_skills(alias_lookup):
    combos = [["Splunk", "SIEM", "network traffic"]]
    text = "Use Splunk and SIEM to analyze network traffic patterns."
    matched = check_skill_combinations(text, {"Splunk", "SIEM"}, alias_lookup, combos)
    assert matched == combos

    matched_missing_owned = check_skill_combinations(text, {"Splunk"}, alias_lookup, combos)  # missing SIEM
    assert matched_missing_owned == []


def test_check_skill_combinations_requires_text_presence(alias_lookup):
    combos = [["Python", "ETL", "SQL"]]
    matched = check_skill_combinations("Just Python here.", {"Python", "ETL", "SQL"}, alias_lookup, combos)
    assert matched == []


def test_skills_score_zero_when_nothing_from_vocabulary_mentioned(alias_lookup):
    """Regression test: a posting that mentions none of the tracked skills
    (e.g. a sales role) must NOT get a perfect skills score by default —
    that was a real bug caught via live testing that made "Account
    Executive" postings outscore genuine cybersecurity-skill matches."""
    result = match_skills(
        "Build relationships with enterprise customers and close deals.",
        "Account Executive",
        candidate_skills={"Python", "SQL", "Splunk"},
        alias_lookup=alias_lookup,
    )
    assert result.matching_skills == []
    assert result.skills_score == 0.0


def test_skills_score_dampens_single_incidental_match(alias_lookup):
    """Regression test: a sales posting that happens to mention one product
    buzzword (e.g. "Zero Trust" in a security vendor's boilerplate company
    description) shouldn't score as well as a posting with several genuine
    skill matches — caught via live data where this inflated Account
    Executive roles above real technical matches."""
    single_buzzword = match_skills(
        "About us: we pioneered Zero Trust security. Build relationships with enterprise customers.",
        "Account Executive",
        candidate_skills={"Zero Trust", "Splunk", "SIEM", "Python"},
        alias_lookup=alias_lookup,
    )
    multi_skill_match = match_skills(
        "Requirements: Zero Trust, Splunk, and SIEM experience required.",
        "Security Engineer",
        candidate_skills={"Zero Trust", "Splunk", "SIEM", "Python"},
        alias_lookup=alias_lookup,
    )
    assert single_buzzword.skills_score < multi_skill_match.skills_score
    assert multi_skill_match.skills_score >= 80.0


def test_skills_score_higher_when_all_required_matched(alias_lookup):
    full_match = match_skills(
        "Requirements: Python and SQL required.", "Engineer",
        candidate_skills={"Python", "SQL"}, alias_lookup=alias_lookup,
    )
    partial_match = match_skills(
        "Requirements: Python and SQL required.", "Engineer",
        candidate_skills={"Python"}, alias_lookup=alias_lookup,  # missing SQL
    )
    assert full_match.skills_score > partial_match.skills_score


# ---------- semantic_matcher ----------

def test_semantic_score_empty_text_returns_zero():
    assert semantic_score("", "something", model=FakeModel()) == 0.0
    assert semantic_score("something", "", model=FakeModel()) == 0.0


def test_semantic_score_identical_text_scores_high():
    text = "python security automation splunk siem"
    score = semantic_score(text, text, model=FakeModel())
    assert score > 50


def test_get_model_loads_from_cache_without_network(monkeypatch):
    """Regression test: found by deliberately breaking network access and
    watching the full test suite hang for minutes — SentenceTransformer()
    without local_files_only=True still tries to reach Hugging Face Hub to
    check for updates even when the model is already cached, defeating the
    point of a local/offline scoring system. Verifies get_model() requests
    the cache-only load first."""
    import src.matching.semantic_matcher as sm

    sm._model = None
    calls = []

    class DummyModel:
        pass

    def fake_sentence_transformer(name, local_files_only=False, **kwargs):
        calls.append(local_files_only)
        return DummyModel()

    monkeypatch.setattr("sentence_transformers.SentenceTransformer", fake_sentence_transformer)
    sm.get_model()
    sm._model = None  # don't leak state into other tests

    assert calls == [True]


def test_build_candidate_corpus_includes_skills_and_experience(profile):
    corpus = build_candidate_corpus(profile)
    assert "Python" in corpus
    assert "Lonza" in corpus or "Operational Technology Infrastructure Intern" in corpus


# ---------- scorer: location ----------

def test_score_location_california_full():
    assert score_location("CA", "onsite") == 100.0


def test_score_location_remote_high():
    assert score_location(None, "remote") == 90.0


def test_score_location_other_us_moderate():
    assert score_location("TX", "onsite") == 70.0


# ---------- scorer: experience ----------

def test_score_experience_meets_requirement():
    assert score_experience(candidate_years=3, required_min=2) == 100.0


def test_score_experience_no_requirement_stated():
    assert score_experience(candidate_years=1, required_min=None) == 85.0


def test_score_experience_below_requirement_partial_credit():
    score = score_experience(candidate_years=1, required_min=4)
    assert 40.0 <= score < 100.0


# ---------- scorer: title ----------

def test_score_title_exact_match_scores_highest():
    assert score_title("Cybersecurity Analyst", ["Cybersecurity Analyst", "Network Engineer"]) == 100.0


def test_score_title_unrelated_scores_low():
    score = score_title("Pastry Chef", ["Cybersecurity Analyst", "Network Engineer"])
    assert score < 30.0


def test_score_title_empty_target_list_returns_neutral():
    assert score_title("Cybersecurity Analyst", []) == 50.0


# ---------- scorer: education ----------

def test_required_degree_level_detects_bachelor():
    assert required_degree_level("A Bachelor's degree is required.") == "bachelor"


def test_required_degree_level_none_when_unmentioned():
    assert required_degree_level("Great team, flexible hours.") is None


def test_score_education_meets_requirement():
    assert score_education("Bachelor's degree required.", highest_completed_level="bachelor") == 100.0


def test_score_education_in_progress_partial_credit():
    score = score_education("Master's degree required.", highest_completed_level="bachelor", in_progress_level="master")
    assert score == 75.0


def test_score_education_no_requirement_stated():
    assert score_education("Great team culture.") == 90.0


def test_score_education_falls_short():
    score = score_education("PhD required.", highest_completed_level="bachelor", in_progress_level="master")
    assert score == 40.0


# ---------- scorer: freshness ----------

def test_score_freshness_recent_posting_scores_high():
    posted = REFERENCE_TIME - timedelta(days=1)
    score = score_freshness(posted, lookback_days=30, reference_time=REFERENCE_TIME)
    assert score > 90


def test_score_freshness_old_posting_scores_low():
    posted = REFERENCE_TIME - timedelta(days=29)
    score = score_freshness(posted, lookback_days=30, reference_time=REFERENCE_TIME)
    assert score < 10


def test_score_freshness_unknown_date_neutral():
    assert score_freshness(None, lookback_days=30, reference_time=REFERENCE_TIME) == 50.0


# ---------- scorer: score_job integration ----------

def test_score_job_strong_match_scores_well(profile, alias_lookup):
    job = make_normalized_job()
    breakdown = score_job(
        job,
        profile,
        alias_lookup,
        skill_combinations=[["Splunk", "SIEM", "network security"]],
        target_titles=["Cybersecurity Analyst", "Security Analyst"],
        reference_time=REFERENCE_TIME,
        semantic_model=FakeModel(),
    )
    assert breakdown.total_score > 60
    assert "Splunk" in breakdown.skill_match.matching_skills
    assert "SIEM" in breakdown.skill_match.matching_skills
    assert breakdown.band in ("exceptional", "strong", "good", "possible")


def test_score_job_poor_match_scores_lower(profile, alias_lookup):
    job = make_normalized_job(
        title="Pastry Chef",
        normalized_title="Pastry Chef",
        description="Manage a bakery kitchen and inventory. 10+ years required.",
        experience_min=10.0,
        state="NY",
    )
    breakdown = score_job(
        job,
        profile,
        alias_lookup,
        target_titles=["Cybersecurity Analyst"],
        reference_time=REFERENCE_TIME,
        semantic_model=FakeModel(),
    )
    strong_job = make_normalized_job()
    strong_breakdown = score_job(
        strong_job, profile, alias_lookup, target_titles=["Cybersecurity Analyst"],
        reference_time=REFERENCE_TIME, semantic_model=FakeModel(),
    )
    assert breakdown.total_score < strong_breakdown.total_score


def test_score_job_never_claims_unowned_skill(profile, alias_lookup):
    job = make_normalized_job(description="Requires Kubernetes and Docker Swarm expertise.")
    breakdown = score_job(job, profile, alias_lookup, reference_time=REFERENCE_TIME, semantic_model=FakeModel())
    candidate_skills = set(profile.all_skills())
    assert set(breakdown.skill_match.matching_skills).issubset(candidate_skills)


# ---------- explanation ----------

def test_relevant_experience_matches_skills_demonstrated(profile):
    result = relevant_experience(profile, {"Splunk", "Panorama"})
    assert any("Intel" in r for r in result)


def test_relevant_experience_empty_when_no_overlap(profile):
    result = relevant_experience(profile, {"Underwater Basket Weaving"})
    assert result == []


def test_build_match_explanation_includes_matching_skills(profile, alias_lookup):
    job = make_normalized_job()
    breakdown = score_job(job, profile, alias_lookup, reference_time=REFERENCE_TIME, semantic_model=FakeModel())
    eligibility = EligibilityResult(eligible=True, reasons=[], warnings=[])
    explanation = build_match_explanation(breakdown, profile, eligibility, "Acme")
    assert "Splunk" in explanation or "SIEM" in explanation


def test_build_match_explanation_surfaces_eligibility_warning(profile, alias_lookup):
    job = make_normalized_job()
    breakdown = score_job(job, profile, alias_lookup, reference_time=REFERENCE_TIME, semantic_model=FakeModel())
    eligibility = EligibilityResult(eligible=True, reasons=[], warnings=["Job may require a security clearance"])
    explanation = build_match_explanation(breakdown, profile, eligibility, "Acme")
    assert "security clearance" in explanation


def test_build_match_explanation_includes_combo_bonus_text(profile, alias_lookup):
    job = make_normalized_job(
        description="Requirements: Splunk, SIEM, and network security experience required."
    )
    breakdown = score_job(
        job, profile, alias_lookup,
        skill_combinations=[["Splunk", "SIEM", "network security"]],
        reference_time=REFERENCE_TIME, semantic_model=FakeModel(),
    )
    eligibility = EligibilityResult(eligible=True, reasons=[], warnings=[])
    explanation = build_match_explanation(breakdown, profile, eligibility, "Acme")
    assert "Strong alignment with your" in explanation


def test_build_match_explanation_lists_missing_required_skills(profile, alias_lookup):
    from src.matching.scorer import ScoreBreakdown
    from src.matching.skill_matcher import SkillMatchResult

    skill_match = SkillMatchResult(
        matching_skills=["Python"],
        missing_required_skills=["AWS"],
        missing_preferred_skills=[],
        combo_bonus_matches=[],
        skills_score=50.0,
    )
    breakdown = ScoreBreakdown(
        total_score=70, skills_score=50, experience_score=80, title_score=60, education_score=90,
        location_score=100, semantic_score=40, freshness_score=90, skill_match=skill_match, band="good",
    )
    eligibility = EligibilityResult(eligible=True, reasons=[], warnings=[])
    explanation = build_match_explanation(breakdown, profile, eligibility, "Acme")
    assert "Missing required: AWS" in explanation


def test_build_match_presentation_missing_skills_split_json(profile, alias_lookup):
    import json

    job = make_normalized_job(
        description="Requirements: Kubernetes required. Preferred Qualifications: Docker preferred."
    )
    breakdown = score_job(job, profile, alias_lookup, reference_time=REFERENCE_TIME, semantic_model=FakeModel())
    eligibility = EligibilityResult(eligible=True, reasons=[], warnings=[])
    presentation = build_match_presentation(breakdown, profile, eligibility, "Acme")
    missing = json.loads(presentation.missing_skills_json)
    assert "required" in missing and "preferred" in missing
    matching = json.loads(presentation.matching_skills_json)
    assert isinstance(matching, list)
