"""Extracts raw text from resume.pdf. Does not interpret or structure it —
see profile_builder.py for turning text into structured profile data."""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from src.logging_config import get_logger

logger = get_logger(__name__)


def extract_text(pdf_path: str | Path) -> str:
    """Extract text from every page of the resume PDF, in order."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"Resume PDF not found: {pdf_path}")

    pages: list[str] = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            pages.append(page.get_text())

    text = "\n".join(pages)
    logger.info("Extracted %d characters from %d page(s) of %s", len(text), len(pages), pdf_path.name)
    return text


def save_raw_text(text: str, output_path: str | Path) -> None:
    """Persist the original extracted text separately from the editable profile,
    so manual corrections to profile.yaml never lose the source resume content."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    logger.info("Saved raw resume text to %s", output_path)
