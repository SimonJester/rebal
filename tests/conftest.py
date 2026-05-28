"""Shared paths for fixture-based tests."""

from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / 'fixtures'


def fixture_path(name: str) -> str:
    return str(FIXTURES_DIR / name)
