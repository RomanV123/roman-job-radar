from src.services.job_categorizer import categorize_job


def test_categorizes_cybersecurity():
    assert categorize_job("SOC Analyst") == "cybersecurity"
    assert categorize_job("Senior Security Engineer") == "cybersecurity"
    assert categorize_job("Incident Response Analyst") == "cybersecurity"


def test_categorizes_software_engineering():
    assert categorize_job("Backend Software Engineer") == "software_engineering"
    assert categorize_job("Full Stack Developer") == "software_engineering"


def test_categorizes_data_analytics():
    assert categorize_job("Senior Data Analyst") == "data_analytics"
    assert categorize_job("Machine Learning Engineer") == "data_analytics"


def test_categorizes_product_management():
    assert categorize_job("Associate Product Manager") == "product_management"


def test_uncategorized_title_returns_none():
    assert categorize_job("Warehouse Associate") is None
    assert categorize_job("Accounts Payable Specialist") is None


def test_cybersecurity_takes_priority_over_software_engineering():
    """A title that could plausibly fit two buckets should land in
    cybersecurity -- it's the featured section on the exported board."""
    assert categorize_job("Security Software Engineer") == "cybersecurity"
    assert categorize_job("Software Engineer - Cloud Security") == "cybersecurity"


def test_case_insensitive():
    assert categorize_job("SENIOR SECURITY ANALYST") == "cybersecurity"
    assert categorize_job("junior product manager") == "product_management"
