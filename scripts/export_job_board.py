"""CLI entry point for a manual/on-demand job board export.

The pipeline calls src.services.job_board_export.run_export() itself after
every scheduled run -- this script is only for running it by hand, e.g. to
preview a change to the export logic before the next automatic run.

Usage: python scripts/export_job_board.py [--output-dir PATH] [--no-push]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.job_board_export import run_export


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("../roman-job-radar-board"))
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()
    run_export(args.output_dir.resolve(), push=not args.no_push)


if __name__ == "__main__":
    main()
