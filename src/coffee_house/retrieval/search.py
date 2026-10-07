"""BM25 + nombres aproximados + MiniLM; fusión por rango, no probabilidades."""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from rank_bm25 import BM25Okapi
from rapidfuzz import fuzz

from coffee_house.domain.catalog import Catalog, Source

MODEL_ID = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_REVISION = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"


class RetrievalUnavailable(RuntimeError):
    pass


def normalize(text: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFD", text.casefold())
                           if unicodedata.category(c) != "Mn").split())


def tokens(text: str) -> list[str]:
    return re.findall(r"\w+(?:[.,]\d+)?", normalize(text))


def lexical_tokens(text: str) -> list[str]:
    # Solo la copia de búsqueda; nunca borrar no/sin, cantidades o importe original.
    stopwords = {"de", "del", "la", "el", "los", "las", "un", "una", "y", "con", "que", "quiero", "por"}
    return [word for word in tokens(text) if word not in stopwords]


def name_similarity(query: str, alias: str) -> float:
    # WRatio sobre toda la frase premia coincidencias accidentales de palabras
    # cortas. Comparar los términos del nombre exige cubrir toda la preparación.
    words = [word for word in tokens(alias) if word not in ("de", "hot", "iced", "frappe")]
    query_words = [word for word in tokens(query) if word not in ("de", "la", "el", "con", "un", "una")]
    if not words or not query_words:
        return 0.0
    scores = [max(fuzz.ratio(word, candidate) for candidate in query_words) for word in words]
    return sum(scores) / len(scores) if min(scores) >= 70 else 0.0


class Encoder(Protocol):
    def encode(self, texts: list[str]) -> np.ndarray: ...


class MiniLMEncoder:
    """Carga explícita, CPU y revisión fija. Nunca instala ni descarga al importar."""

    def __init__(self, cache_folder: str | Path, *, local_files_only: bool = True):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(MODEL_ID, revision=MODEL_REVISION, device="cpu",
                                         cache_folder=str(cache_folder), local_files_only=local_files_only,
                                         trust_remote_code=False, token=False)

    def encode(self, texts: list[str]) -> np.ndarray:
        return self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False,
                                 convert_to_numpy=True, batch_size=16)


@dataclass(frozen=True)
class Document:
    identifier: str
    text: str
    aliases: tuple[str, ...]
    sources: tuple[Source, ...]
    product_id: str | None


@dataclass(frozen=True)
class Candidate:
    document: Document
    ranks: tuple[tuple[str, int], ...]
    fused_score: float
    fuzzy_score: float
    semantic_score: float
    exact_length: int


@dataclass(frozen=True)
class SearchResult:
    original: str
    normalized: str
    numbers: tuple[str, ...]
    restrictions: tuple[str, ...]
    candidates: tuple[Candidate, ...]


def build_documents(catalog: Catalog, knowledge_path: str | Path) -> tuple[Document, ...]:
    documents = []
    for product in catalog.products.values():
        name = normalize(product.name)
        base = re.sub(r"^(iced |frappe |frappe de )", "", name)
        aliases = tuple(dict.fromkeys((name, base, normalize(product.id.replace("_", " ")))))
        mode = "caliente" if product.id.startswith("hot_") else "iced frío" if product.id.startswith("iced_") else "frappé" if product.id.startswith("frappe_") else ""
        translated_category = normalize(product.category).replace("coffee", "café").replace("iced", "frío").replace("tea", "té")
        text = f"{product.name}. Categoría del menú: {product.category} / {translated_category}. {mode}."
        if product.documented_ingredients:
            text += " Ingredientes parcialmente documentados: " + ", ".join(product.documented_ingredients)
        sources = tuple(dict.fromkeys([product.source] + [s for v in product.variants
                                                          for s in (v.price_source, v.size_source) if s is not None]))
        documents.append(Document(product.id, text, aliases, sources, product.id))
    # Archivo autorizado del proyecto, nunca texto recibido del usuario/modelo.
    for number, paragraph in enumerate(Path(knowledge_path).read_text(encoding="utf-8").split("\n- ")):
        if paragraph.strip():
            documents.append(Document(f"knowledge_{number}", paragraph.strip(), (),
                                      (Source("spec_001_r18", "Información autorizada y ausencias"),), None))
    return tuple(documents)


class HybridIndex:
    def __init__(self, documents: tuple[Document, ...], encoder: Encoder):
        if not documents or len({d.identifier for d in documents}) != len(documents):
            raise ValueError("Documentos vacíos o identificadores repetidos.")
        self.documents = documents
        self.encoder = encoder
        self.bm25 = BM25Okapi([lexical_tokens(d.text) for d in documents])
        self.vectors = self._vectors([d.text for d in documents])

    def _vectors(self, texts: list[str]) -> np.ndarray:
        try:
            vectors = np.asarray(self.encoder.encode(texts), dtype=np.float32)
            if vectors.ndim != 2 or vectors.shape[0] != len(texts) or vectors.shape[1] == 0 or not np.isfinite(vectors).all():
                raise ValueError("Embeddings inválidos.")
            norm = np.linalg.norm(vectors, axis=1, keepdims=True)
            if (norm == 0).any():
                raise ValueError("Embeddings nulos.")
            return vectors / norm
        except Exception as exc:
            raise RetrievalUnavailable("No se pudo completar la búsqueda semántica.") from exc

    def search(self, original: str, *, limit: int = 8) -> SearchResult:
        if not isinstance(original, str) or not original.strip() or len(original) > 4000:
            raise ValueError("Consulta vacía o demasiado larga.")
        if type(limit) is not int or not 1 <= limit <= len(self.documents):
            raise ValueError("Límite de candidatos inválido.")
        query = normalize(original)
        bm25 = self.bm25.get_scores(lexical_tokens(query))
        vector = self._vectors([original])
        if vector.shape[1] != self.vectors.shape[1]:
            raise RetrievalUnavailable("Dimensión del índice incompatible.")
        semantic = (self.vectors @ vector[0]).tolist()
        fuzzy = [max((name_similarity(query, a) for a in d.aliases), default=0.0) for d in self.documents]
        exact = [max((len(a) for a in d.aliases if re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", query)), default=0)
                 for d in self.documents]
        signals = {"exact": exact, "bm25": bm25, "fuzzy": fuzzy, "semantic": semantic}
        ranks: list[dict[str, int]] = [{} for _ in self.documents]
        for signal, scores in signals.items():
            ordered = sorted(range(len(scores)), key=lambda i: (-scores[i], self.documents[i].identifier))
            rank = 0
            for i in ordered:
                if scores[i] <= 0:
                    continue
                rank += 1
                ranks[i][signal] = rank
        candidates = [Candidate(d, tuple(ranks[i].items()),
                                sum((2 if signal == "exact" else 1) / (60 + rank) for signal, rank in ranks[i].items()),
                                fuzzy[i], semantic[i], exact[i]) for i, d in enumerate(self.documents)]
        candidates.sort(key=lambda c: (-c.exact_length, -c.fused_score, c.document.identifier))
        restrictions = tuple(m.group(0).strip() for m in re.finditer(
            r"\b(?:sin|no|excepto|menos|solo)\b(?:(?:[.,](?=\d))|[^,;?.!])*", original, re.IGNORECASE))
        return SearchResult(original, query, tuple(re.findall(r"\d+(?:[.,]\d+)?", original)), restrictions,
                            tuple(candidates[:limit]))
