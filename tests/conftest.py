"""Pytest configuration for the ClauseLens suite."""

from __future__ import annotations

import pytest

from core.analysis import AnalysisPipeline


@pytest.fixture(scope="session")
def pipeline() -> AnalysisPipeline:
    return AnalysisPipeline()
