"""Catálogo versionado: importes enteros y fuentes explícitas, nunca inferidas."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

PROVISIONAL_NOTICE = "estimado para la demo; pendiente de confirmación del negocio"
PROVISIONAL_SIZE_NOTICE = "Tamaño provisional para la demo; pendiente de confirmación del negocio"
UNKNOWN_SIZE = "sin_tamano_documentado"
SINGLE_PRESENTATION = "presentacion_unica"
RF39_VOLUMES = MappingProxyType({
    "espresso": 60, "espresso_macchiato": 90, "espresso_cortado": 120, "espresso_affogato": 180,
    "te_manzanilla": 360, "te_limon": 360, "te_hierbabuena": 360, "te_verde": 360,
})
PUMPKIN_IDS = frozenset({"hot_pumpkin_spice_latte", "iced_pumpkin_spice_latte", "frappe_pumpkin_spice_latte"})


def nonnegative_integer(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} debe ser un entero no negativo.")
    return value


@dataclass(frozen=True)
class Source:
    source_id: str
    section: str
    page: int | None = None

    @property
    def brief(self) -> str:
        if self.source_id == "spec_001_r18" and self.section == "Información autorizada y ausencias":
            return "Información autorizada · Ausencias documentadas"
        title = "Menú de otoño" if self.source_id == "menu_otono" else "Estimación autorizada para la demo"
        return f"{title} · {self.section}"


@dataclass(frozen=True)
class Variant:
    size: str
    price_cents: int
    volume_ml: int | None
    volume_oz: int | None
    portion_confirmed: bool
    price_status: str
    price_source: Source
    size_source: Source | None
    size_status: str
    size_notice: str | None

    @property
    def size_label(self) -> str:
        if self.size == UNKNOWN_SIZE:
            return "Porción sin confirmar"
        label = "Presentación única" if self.size == SINGLE_PRESENTATION else self.size.capitalize()
        volume = f"{self.volume_ml} ml" if self.volume_oz is None else f"{self.volume_oz} oz / {self.volume_ml} ml"
        return f"{label} · {volume}"

    @property
    def recommendation_allowed(self) -> bool:
        if self.size == UNKNOWN_SIZE or self.size_status == "sin_confirmar":
            return False
        if self.size_status == "documentado":
            return self.portion_confirmed
        return (self.size_status == "provisional" and self.size_source is not None
                and self.size_source.source_id in ("spec_001_r17", "spec_001_r18")
                and self.size_notice == PROVISIONAL_SIZE_NOTICE)


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    category: str
    extra_group: str | None
    variants: tuple[Variant, ...]
    source: Source
    note: str | None
    documented_ingredients: tuple[str, ...] | None
    ingredients_exhaustive: bool


@dataclass(frozen=True)
class Extra:
    id: str
    name: str
    price_cents: int
    groups: tuple[str, ...]
    max_quantity: int
    exclusive_group: str | None
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class Catalog:
    products: Mapping[str, Product]
    extras: Mapping[str, Extra]
    seed_unavailable_products: frozenset[str]
    seed_unavailable_extras: frozenset[str]


def load_catalog(path: str | Path) -> Catalog:
    """Rechaza datos malformados antes de usarlos en cálculos o recomendaciones."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if type(data["schema_version"]) is not int or data["schema_version"] != 2 or data["currency"] != "MXN":
        raise ValueError("Versión de catálogo o moneda no soportada.")
    source_ids = {source["id"] for source in data["sources"]}

    def source(raw: dict) -> Source:
        if raw["source_id"] not in source_ids or not raw["section"]:
            raise ValueError("Fuente desconocida o sin sección.")
        page = raw.get("page")
        if raw["source_id"] == "menu_otono" and (type(page) is not int or page not in (1, 2, 3)):
            raise ValueError("Página del menú inválida.")
        return Source(raw["source_id"], raw["section"], page)

    products = {}
    for raw in data["products"]:
        variants = []
        for item in raw["variants"]:
            size = item["size"]
            if size not in ("mediano", "grande", UNKNOWN_SIZE, SINGLE_PRESENTATION):
                raise ValueError("Tamaño no soportado.")
            status = item["price_status"]
            if status not in ("documentado", "provisional") or type(item["portion_confirmed"]) is not bool:
                raise ValueError("Estado del precio o porción inválido.")
            expected_volume = {"mediano": (360, 12), "grande": (480, 16), UNKNOWN_SIZE: (None, None),
                               SINGLE_PRESENTATION: (RF39_VOLUMES.get(raw["id"]), None)}[size]
            if (item["volume_ml"], item["volume_oz"]) != expected_volume:
                raise ValueError("Volumen inconsistente con el tamaño documentado.")
            if item["volume_ml"] is not None and type(item["volume_ml"]) is not int:
                raise ValueError("Volumen debe ser un entero.")
            price_source = source(item["price_source"])
            size_source = source(item["size_source"]) if item["size_source"] is not None else None
            if (status == "provisional") != (price_source.source_id == "spec_001_r17"):
                raise ValueError("Un precio provisional no puede atribuirse al PDF.")
            if status == "provisional" and raw["id"] not in PUMPKIN_IDS:
                raise ValueError("Precio provisional sin excepción autorizada.")
            if status == "documentado" and price_source.source_id != "menu_otono":
                raise ValueError("Un precio documentado debe citar el PDF.")
            if size == UNKNOWN_SIZE and (item["portion_confirmed"] or size_source is not None):
                raise ValueError("No completar una porción desconocida.")
            if size != UNKNOWN_SIZE and size_source is None:
                raise ValueError("Tamaño sin fuente.")
            size_status = item["size_status"]
            size_notice = item["size_notice"]
            if size_status == "documentado":
                if size == UNKNOWN_SIZE or not item["portion_confirmed"] or size_source.source_id != "menu_otono" or size_notice is not None:
                    raise ValueError("Tamaño documentado inconsistente con su fuente/aviso.")
            elif size_status == "provisional":
                if item["portion_confirmed"] or size_notice != PROVISIONAL_SIZE_NOTICE or size_source is None:
                    raise ValueError("Tamaño provisional sin aviso o presentado como confirmado.")
                expected_source = "spec_001_r18" if raw["id"] in RF39_VOLUMES else "spec_001_r17" if raw["id"] in PUMPKIN_IDS else None
                if expected_source is None or size_source.source_id != expected_source:
                    raise ValueError("Tamaño provisional sin excepción autorizada.")
            elif size_status == "sin_confirmar":
                if size != UNKNOWN_SIZE or size_source is not None or item["portion_confirmed"] or size_notice is not None:
                    raise ValueError("Porción sin confirmar inconsistente.")
            else:
                raise ValueError("Estado del tamaño desconocido.")
            if raw["id"] in RF39_VOLUMES:
                if size != SINGLE_PRESENTATION or size_status != "provisional" or status != "documentado" or raw["extra_group"] is not None:
                    raise ValueError("RF-39 conserva presentación única provisional, precio PDF y grupo de extras sin confirmar.")
            elif size == SINGLE_PRESENTATION:
                raise ValueError("Presentación única sin excepción autorizada.")
            if raw["id"] in PUMPKIN_IDS and size_status != "provisional":
                raise ValueError("El tamaño de Pumpkin sigue siendo provisional.")
            variants.append(Variant(size, nonnegative_integer(item["price_cents"], "Precio"),
                                    item["volume_ml"], item["volume_oz"], item["portion_confirmed"],
                                    status, price_source, size_source, size_status, size_notice))
        if not variants or len({v.size for v in variants}) != len(variants):
            raise ValueError("Producto sin variantes o con tamaños duplicados.")
        if raw["extra_group"] not in (None, "calientes", "frias"):
            raise ValueError("Grupo de extras desconocido.")
        if raw["id"] in products:
            raise ValueError("ID de producto duplicado.")
        if any(v.price_status == "provisional" for v in variants) and raw["note"] != PROVISIONAL_NOTICE:
            raise ValueError("Falta aviso del precio provisional.")
        products[raw["id"]] = Product(raw["id"], raw["name"], raw["category"], raw["extra_group"],
                                       tuple(variants), source(raw["source"]), raw["note"],
                                       tuple(raw["documented_ingredients"]) if raw["documented_ingredients"] is not None else None,
                                       raw["ingredients_exhaustive"])
    extras = {}
    for raw in data["extras"]:
        if raw["id"] in extras or type(raw["max_quantity"]) is not int or raw["max_quantity"] != 1:
            raise ValueError("Extra duplicado o límite inválido.")
        if not raw["groups"] or any(group not in ("calientes", "frias") for group in raw["groups"]):
            raise ValueError("Grupo de extras inválido.")
        extras[raw["id"]] = Extra(raw["id"], raw["name"], nonnegative_integer(raw["price_cents"], "Extra"),
                                    tuple(raw["groups"]), 1, raw["exclusive_group"],
                                    tuple(source(ref) for ref in raw["sources"]))
    unavailable_products = frozenset(data["simulation_seed"]["unavailable_products"])
    unavailable_extras = frozenset(data["simulation_seed"]["unavailable_extras"])
    if not unavailable_products <= products.keys() or not unavailable_extras <= extras.keys():
        raise ValueError("La semilla refiere elementos desconocidos.")
    return Catalog(MappingProxyType(products), MappingProxyType(extras), unavailable_products, unavailable_extras)
