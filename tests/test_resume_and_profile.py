from pathlib import Path

import pytest

from src.resume.parser import extract_text, save_raw_text
from src.resume.profile_builder import (
    build_alias_lookup,
    load_profile,
    load_skill_dictionary,
    normalize_skill,
)

RESUME_PATH = Path("resume.pdf")


def test_extract_text_reads_resume_pdf():
    text = extract_text(RESUME_PATH)
    assert "Roman Vasilyev" in text
    assert "Lonza" in text
    assert "Intel" in text


def test_extract_text_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_text(tmp_path / "does_not_exist.pdf")


def test_save_raw_text(tmp_path):
    out = tmp_path / "resume_raw.txt"
    save_raw_text("hello world", out)
    assert out.read_text(encoding="utf-8") == "hello world"


def test_load_profile_matches_resume_facts():
    profile = load_profile()
    assert profile.candidate["name"] == "Roman Vasilyev"
    companies = {exp.company for exp in profile.experience}
    assert companies == {"Lonza", "Intel", "California Air Resources Board", "UC Davis"}
    assert "Python" in profile.all_skills()
    assert "Operational Technology" in profile.all_skills()


def test_skill_alias_normalization():
    skill_dict = load_skill_dictionary()
    lookup = build_alias_lookup(skill_dict)
    assert normalize_skill("Amazon Web Services", lookup) == "AWS"
    assert normalize_skill("aws", lookup) == "AWS"
    assert normalize_skill("Palo Alto Networks", lookup) == "Palo Alto NGFW"
    assert normalize_skill("Security Information and Event Management", lookup) == "SIEM"
    assert normalize_skill("AD", lookup) == "Active Directory"
    assert normalize_skill("Operational Technology", lookup) == "Operational Technology"


def test_unrecognized_skill_returns_none():
    skill_dict = load_skill_dictionary()
    lookup = build_alias_lookup(skill_dict)
    assert normalize_skill("Underwater Basket Weaving", lookup) is None
