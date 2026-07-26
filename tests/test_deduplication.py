import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.models import Base, Company, Job
from src.processing.deduplicate import (
    CompanyJob,
    canonicalize_url,
    deduplicate_jobs,
    description_similarity,
    find_existing_job,
    is_duplicate,
    merge_duplicate_group,
)
from src.processing.normalize import NormalizedJob


def make_job(**overrides) -> NormalizedJob:
    defaults = dict(
        external_id="ext-1",
        source="greenhouse",
        title="Security Analyst",
        normalized_title="Security Analyst",
        description="Do security things day to day.",
        location="Sacramento, CA",
        state="CA",
        is_us=True,
        workplace_type="onsite",
        employment_type="full_time",
        salary_min=None,
        salary_max=None,
        experience_min=None,
        experience_max=None,
        posted_at=None,
        apply_url="https://example.com/jobs/1",
        source_url="https://example.com/jobs/1",
        content_hash="hash-1",
    )
    defaults.update(overrides)
    return NormalizedJob(**defaults)


def make_entry(company_name="Acme", company_id=1, **overrides) -> CompanyJob:
    return CompanyJob(company_name=company_name, company_id=company_id, job=make_job(**overrides))


# ---------- canonicalize_url ----------

def test_canonicalize_url_strips_trailing_slash_and_lowercases_host():
    assert canonicalize_url("https://Example.com/jobs/1/") == canonicalize_url("https://example.com/jobs/1")


def test_canonicalize_url_strips_tracking_params():
    a = canonicalize_url("https://example.com/jobs/1?utm_source=linkedin&ref=abc")
    b = canonicalize_url("https://example.com/jobs/1")
    assert a == b


def test_canonicalize_url_keeps_meaningful_query_params():
    a = canonicalize_url("https://example.com/jobs?id=1")
    b = canonicalize_url("https://example.com/jobs?id=2")
    assert a != b


def test_canonicalize_url_none():
    assert canonicalize_url(None) is None


# ---------- description_similarity ----------

def test_description_similarity_identical():
    assert description_similarity("hello world", "hello world") == 1.0


def test_description_similarity_different():
    assert description_similarity("hello world", "completely unrelated text") < 0.5


def test_description_similarity_missing():
    assert description_similarity(None, "text") == 0.0
    assert description_similarity("text", None) == 0.0


# ---------- is_duplicate ----------

def test_duplicate_via_external_id():
    a = make_entry(external_id="123", source="greenhouse", apply_url="https://a.com/1", content_hash="h1")
    b = make_entry(external_id="123", source="greenhouse", apply_url="https://a.com/2", content_hash="h2")
    assert is_duplicate(a, b) == "external_id"


def test_not_duplicate_same_external_id_different_source():
    a = make_entry(
        external_id="123", source="greenhouse", content_hash="h1", apply_url="https://a.com/1",
        description="Work on Palo Alto firewall policy tuning and Zero Trust segmentation.",
    )
    b = make_entry(
        external_id="123", source="lever", content_hash="h2", apply_url="https://b.com/1",
        description="Lead vulnerability management and incident response for the OT network.",
    )
    assert is_duplicate(a, b) is None


def test_duplicate_via_apply_url():
    a = make_entry(external_id="1", source="greenhouse", apply_url="https://a.com/jobs/1?utm_source=x", content_hash="h1")
    b = make_entry(external_id="2", source="custom", apply_url="https://a.com/jobs/1", content_hash="h2")
    assert is_duplicate(a, b) == "apply_url"


def test_duplicate_via_content_hash():
    a = make_entry(external_id="1", apply_url="https://a.com/1", content_hash="same-hash")
    b = make_entry(external_id="2", apply_url="https://b.com/2", content_hash="same-hash")
    assert is_duplicate(a, b) == "content_hash"


def test_duplicate_via_title_location_and_description_similarity():
    a = make_entry(
        external_id="1", apply_url="https://a.com/1", content_hash="h1",
        title="SOC Analyst", normalized_title="Security Operations Analyst", location="Sacramento, CA",
        description="Monitor SIEM alerts and investigate anomalies in network traffic daily.",
    )
    b = make_entry(
        external_id="2", apply_url="https://b.com/2", content_hash="h2",
        title="SOC Analyst", normalized_title="Security Operations Analyst", location="Sacramento, CA",
        description="Monitor SIEM alerts and investigate anomalies in network traffic each day.",
    )
    assert is_duplicate(a, b) == "title_location_similarity"


def test_same_title_location_but_dissimilar_description_not_duplicate():
    """Same role/location can legitimately be two different open reqs —
    don't merge without description corroboration."""
    a = make_entry(
        external_id="1", apply_url="https://a.com/1", content_hash="h1",
        normalized_title="Security Operations Analyst", location="Sacramento, CA",
        description="Work on Palo Alto firewall policy tuning and Zero Trust segmentation.",
    )
    b = make_entry(
        external_id="2", apply_url="https://b.com/2", content_hash="h2",
        normalized_title="Security Operations Analyst", location="Sacramento, CA",
        description="Lead vulnerability management and incident response for the OT network.",
    )
    assert is_duplicate(a, b) is None


def test_same_title_location_missing_description_not_duplicate():
    a = make_entry(
        external_id="1", apply_url="https://a.com/1", content_hash="h1",
        normalized_title="Security Operations Analyst", location="Sacramento, CA", description=None,
    )
    b = make_entry(
        external_id="2", apply_url="https://b.com/2", content_hash="h2",
        normalized_title="Security Operations Analyst", location="Sacramento, CA", description=None,
    )
    assert is_duplicate(a, b) is None


def test_completely_different_jobs_not_duplicate():
    a = make_entry(external_id="1", apply_url="https://a.com/1", content_hash="h1")
    b = make_entry(
        external_id="2", apply_url="https://b.com/2", content_hash="h2",
        normalized_title="Cloud Infrastructure Engineer", location="Remote",
        description="Totally unrelated cloud infrastructure work.",
    )
    assert is_duplicate(a, b) is None


# ---------- merge_duplicate_group ----------

def test_merge_prefers_entry_with_description():
    a = make_entry(source="greenhouse", description=None)
    b = make_entry(source="custom", description="Full description here.")
    result = merge_duplicate_group([a, b])
    assert result.kept.job.source == "custom"
    assert result.merged_from == [a]


def test_merge_prefers_salary_when_description_tied():
    a = make_entry(source="ashby", description="desc", salary_min=None)
    b = make_entry(source="greenhouse", description="desc", salary_min=120000.0)
    result = merge_duplicate_group([a, b])
    assert result.kept.job.source == "greenhouse"


def test_merge_single_entry_group():
    a = make_entry()
    result = merge_duplicate_group([a])
    assert result.kept == a
    assert result.merged_from == []


# ---------- deduplicate_jobs ----------

def test_deduplicate_jobs_groups_and_keeps_unique():
    dup_a = make_entry(
        company_name="Acme", external_id="1", content_hash="same-hash", description=None,
        apply_url="https://acme.com/jobs/1",
    )
    dup_b = make_entry(
        company_name="Acme", external_id="2", content_hash="same-hash", description="fuller text",
        apply_url="https://acme.com/jobs/1",
    )
    unique_job = make_entry(
        company_name="Acme", external_id="3", content_hash="different-hash",
        normalized_title="Cloud Infrastructure Engineer", location="Remote",
        description="Totally unrelated cloud infrastructure work.",
        apply_url="https://acme.com/jobs/3",
    )
    result = deduplicate_jobs([dup_a, dup_b, unique_job])
    assert len(result.unique) == 1
    assert result.unique[0].job.external_id == "3"
    assert len(result.merged) == 1
    assert result.merged[0].kept.job.external_id == "2"  # has description, preferred
    assert len(result.all_kept) == 2


def test_deduplicate_jobs_scoped_per_company():
    """Same title/location/content_hash at two DIFFERENT companies must
    never merge — dedup is scoped per company."""
    job_a = make_entry(company_name="Acme", external_id="1", content_hash="same-hash")
    job_b = make_entry(company_name="Other Co", external_id="2", content_hash="same-hash")
    result = deduplicate_jobs([job_a, job_b])
    assert len(result.unique) == 2
    assert len(result.merged) == 0


def test_deduplicate_jobs_empty_list():
    result = deduplicate_jobs([])
    assert result.unique == []
    assert result.merged == []


# ---------- find_existing_job (DB-aware) ----------

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def company(session):
    c = Company(name="Acme", ats_type="greenhouse", board_identifier="acme")
    session.add(c)
    session.commit()
    return c


def test_find_existing_job_by_external_id(session, company):
    existing = Job(external_id="123", source="greenhouse", company_id=company.id, title="Security Analyst")
    session.add(existing)
    session.commit()

    normalized = make_job(external_id="123", source="greenhouse")
    found = find_existing_job(session, company.id, normalized)
    assert found is not None
    assert found.id == existing.id


def test_find_existing_job_by_content_hash(session, company):
    existing = Job(
        external_id="old-id", source="greenhouse", company_id=company.id,
        title="Security Analyst", content_hash="stable-hash",
    )
    session.add(existing)
    session.commit()

    normalized = make_job(external_id="new-id", source="greenhouse", content_hash="stable-hash")
    found = find_existing_job(session, company.id, normalized)
    assert found is not None
    assert found.id == existing.id


def test_find_existing_job_by_apply_url(session, company):
    existing = Job(
        external_id="old-id", source="greenhouse", company_id=company.id,
        title="Security Analyst", apply_url="https://acme.com/jobs/1",
    )
    session.add(existing)
    session.commit()

    normalized = make_job(
        external_id="new-id", source="greenhouse", content_hash="different-hash",
        apply_url="https://acme.com/jobs/1?utm_source=x",
    )
    found = find_existing_job(session, company.id, normalized)
    assert found is not None
    assert found.id == existing.id


def test_find_existing_job_no_match_returns_none(session, company):
    normalized = make_job(external_id="brand-new", source="greenhouse", content_hash="unique-hash")
    found = find_existing_job(session, company.id, normalized)
    assert found is None


def test_find_existing_job_scoped_to_company(session, company):
    other_company = Company(name="Other Co", ats_type="lever", board_identifier="other")
    session.add(other_company)
    session.commit()
    existing = Job(
        external_id="old-id", source="greenhouse", company_id=other_company.id,
        title="Security Analyst", content_hash="stable-hash",
    )
    session.add(existing)
    session.commit()

    normalized = make_job(external_id="new-id", source="greenhouse", content_hash="stable-hash")
    found = find_existing_job(session, company.id, normalized)  # searching under `company`, not `other_company`
    assert found is None
