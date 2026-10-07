import hashlib
from pathlib import Path

import numpy as np
import pytest

from coffee_house.domain.catalog import load_catalog
from coffee_house.retrieval.queries import GroundedQueries
from coffee_house.retrieval.search import HybridIndex, build_documents, tokens
from coffee_house.storage.sqlite import AvailabilityStore


class ControlledEncoder:
    """Solo fixture de fallos/reglas; no acredita MiniLM real ni calidad del modelo."""

    def __init__(self):
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        matrix = np.zeros((len(texts), 128), dtype=np.float32)
        for row, text in enumerate(texts):
            matrix[row, 0] = 0.001
            for word in tokens(text):
                index = int(hashlib.sha256(word.encode()).hexdigest()[:8], 16) % 127 + 1
                matrix[row, index] += 1
        return matrix


@pytest.fixture
def setup(tmp_path):
    root = Path(__file__).resolve().parents[2]
    catalog = load_catalog(root / "data/catalog.json")
    encoder = ControlledEncoder()
    index = HybridIndex(build_documents(catalog, root / "data/knowledge/limites.md"), encoder)
    store = AvailabilityStore(tmp_path / "stock.sqlite3", catalog)
    store.initialize()
    return catalog, encoder, index, store, GroundedQueries(catalog, index, store)
