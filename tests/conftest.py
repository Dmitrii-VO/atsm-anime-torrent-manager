from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from atsm.app import bootstrap  # noqa: E402
from atsm.db import Database  # noqa: E402


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def ctx(tmp_path: Path):
    context = bootstrap(data_dir=tmp_path / "data", console_log=False)
    yield context
    context.shutdown()
