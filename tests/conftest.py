import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from paperdb.metadata import Paper  # noqa: E402


@pytest.fixture
def preprint() -> Paper:
    return Paper(
        title="A Title Long Enough To Exercise Truncation Behaviour",
        authors=["Ada Lastname", "Bo Second"],
        year="2023",
        venue="arXiv",
        arxiv_id="2401.01234v2",
        abstract="An abstract.",
        topics=["cs.LG", "cs.AI"],
        citekey="lastname2023paper",
    )


@pytest.fixture
def published() -> Paper:
    return Paper(
        title="A Study of A_B & C",
        authors=["Jane Smith"],
        year="2024",
        venue="Nature",
        doi="10.1038/example",
        citekey="smith2024study",
    )
