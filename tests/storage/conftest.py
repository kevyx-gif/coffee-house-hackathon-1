from pathlib import Path

import pytest

from coffee_house.domain.catalog import load_catalog
from coffee_house.storage.sqlite import AvailabilityStore


@pytest.fixture
def catalog():
    return load_catalog(Path(__file__).resolve().parents[2] / "data/catalog.json")


@pytest.fixture
def store(tmp_path, catalog):
    result = AvailabilityStore(tmp_path / "private" / "availability.sqlite3", catalog)
    result.initialize()
    return result
