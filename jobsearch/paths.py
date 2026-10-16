"""Locations of the data repo. Override with JOBSEARCH_DATA=/path/to/job-search-data."""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("JOBSEARCH_DATA", REPO / "data")).resolve()

PROFILE = DATA / "profile"
FACTS = PROFILE / "facts.yaml"
RULES = PROFILE / "letter-rules.yaml"
SEARCH = DATA / "search"
JOBS = DATA / "jobs"
EFFORTS = DATA / "efforts.csv"
REPORTS = DATA / "reports"
