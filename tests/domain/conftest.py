from pathlib import Path

import pytest

from coffee_house.domain.catalog import load_catalog
from coffee_house.domain.pricing import initial_simulation

CATALOG_PATH = Path(__file__).resolve().parents[2] / "data/catalog.json"


@pytest.fixture
def catalog():
    return load_catalog(CATALOG_PATH)


@pytest.fixture
def stock(catalog):
    return initial_simulation(catalog)
