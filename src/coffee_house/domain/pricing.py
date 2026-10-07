"""Consulta y presupuesto deterministas. No crea pedidos ni adopta sustituciones."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from .catalog import (
    PROVISIONAL_NOTICE,
    UNKNOWN_SIZE,
    Catalog,
    Extra,
    Product,
    Source,
    nonnegative_integer,
)


class ClarificationRequired(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def parse_mxn(text: str) -> int:
    """Conversión explícita, sin floats ni redondeo de cantidades del cliente."""
    if not isinstance(text, str):
        raise TypeError("Proporciona un importe textual en MXN.")
    amount = text.strip()
    if re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", amount) is None:
        raise ValueError("Usa un importe no negativo con máximo dos decimales.")
    pesos, _, fraction = amount.partition(".")
    return int(pesos) * 100 + int(fraction.ljust(2, "0") or "0")


def format_mxn(cents: int) -> str:
    nonnegative_integer(cents, "Importe")
    return f"${cents // 100}.{cents % 100:02d} MXN"


@dataclass(frozen=True)
class Selection:
    product_id: str
    size: str | None
    extras: tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.product_id, str) or self.size is not None and not isinstance(self.size, str):
            raise ValueError("Producto y tamaño deben ser identificadores textuales.")
        if isinstance(self.extras, str) or not isinstance(self.extras, (tuple, list)):
            raise TypeError("Extras debe ser una lista explícita de identificadores.")
        if any(not isinstance(extra, str) for extra in self.extras):
            raise ValueError("Extra inválido.")
        object.__setattr__(self, "extras", tuple(self.extras))


@dataclass(frozen=True)
class Quote:
    selection: Selection
    base_cents: int
    extras_cents: int
    total_cents: int
    provisional: bool  # solo el precio; size_status distingue tamaño provisional
    notices: tuple[str, ...]
    sources: tuple[Source, ...]
    size_status: str
    size_label: str


@dataclass(frozen=True)
class Availability:
    """Snapshot explícito; T3 aportará persistencia/revisión y T4 frescura al responder."""

    products: Mapping[str, bool]
    extras: Mapping[str, bool]
    revision: int
    confirmed: bool

    def __post_init__(self):
        nonnegative_integer(self.revision, "Revisión")
        if type(self.confirmed) is not bool:
            raise ValueError("Estado de conexión inválido.")
        if any(type(value) is not bool for value in (*self.products.values(), *self.extras.values())):
            raise ValueError("Disponibilidad debe ser booleana.")
        object.__setattr__(self, "products", MappingProxyType(dict(self.products)))
        object.__setattr__(self, "extras", MappingProxyType(dict(self.extras)))


def initial_simulation(catalog: Catalog) -> Availability:
    """Solo fixture inicial: el repositorio SQLite NO debe llamarlo en cada reinicio."""
    return Availability({key: key not in catalog.seed_unavailable_products for key in catalog.products},
                        {key: key not in catalog.seed_unavailable_extras for key in catalog.extras}, 0, True)


def _validate_extras(catalog: Catalog, product: Product, selection: Selection) -> tuple[Extra, ...]:
    if len(selection.extras) != len(set(selection.extras)):
        raise ClarificationRequired("repeated_extra", "Solo se permite una unidad de cada extra. ¿Confirmas una opción válida?")
    extras = []
    for identifier in selection.extras:
        extra = catalog.extras.get(identifier)
        if extra is None:
            raise ClarificationRequired("unknown_extra", "No tengo información de ese extra; consulta al personal.")
        extras.append(extra)
    groups = [extra.exclusive_group for extra in extras if extra.exclusive_group is not None]
    if len(groups) != len(set(groups)):
        raise ClarificationRequired("exclusive_milks", "Solo puedes elegir una leche: avena o almendras. ¿Cuál prefieres?")
    if extras and product.extra_group is None:
        raise ClarificationRequired("unknown_extra_group", "El grupo de extras de esta preparación no está confirmado; consulta al personal.")
    if any(product.extra_group not in extra.groups for extra in extras):
        raise ClarificationRequired("invalid_extra_group", "Ese extra no pertenece al grupo de esta bebida. ¿Quieres revisar otra opción?")
    return tuple(extras)


@dataclass(frozen=True)
class AvailabilityDetails:
    """Estado por elemento; None es desconocido, nunca agotado."""

    selection: Selection
    product_available: bool | None
    extras_available: Mapping[str, bool | None]
    revision: int
    confirmed: bool

    def __post_init__(self):
        nonnegative_integer(self.revision, "Revisión")
        values = (self.product_available, *self.extras_available.values())
        if type(self.confirmed) is not bool or any(value is not None and type(value) is not bool for value in values):
            raise ValueError("Detalle de disponibilidad inválido")
        if set(self.extras_available) != set(self.selection.extras):
            raise ValueError("Detalle incompleto de extras")
        object.__setattr__(self, "extras_available", MappingProxyType(dict(self.extras_available)))

    @property
    def available(self) -> bool | None:
        if not self.confirmed:
            return None
        values = (self.product_available, *self.extras_available.values())
        if any(value is False for value in values):
            return False
        return None if any(value is None for value in values) else True


def availability_details(catalog: Catalog, selection: Selection, stock: Availability) -> AvailabilityDetails:
    """Consulta producto completo sin imponer tamaño; reutiliza reglas de extras.

    Si se elige un tamaño, comprobar la combinación antes de informar existencia.
    Una falta de fila de un producto nuevo permanece explícitamente desconocida.
    """
    product = catalog.products.get(selection.product_id)
    if product is None:
        raise ClarificationRequired("unknown_product", "No tengo información de ese producto; consulta al personal.")
    if selection.size is not None:
        calculate_quote(catalog, selection)
    else:
        _validate_extras(catalog, product, selection)
    return AvailabilityDetails(
        selection,
        stock.products.get(selection.product_id) if stock.confirmed else None,
        MappingProxyType({key: stock.extras.get(key) if stock.confirmed else None for key in selection.extras}),
        stock.revision,
        stock.confirmed,
    )


def calculate_quote(catalog: Catalog, selection: Selection) -> Quote:
    """Precios informativos sin afirmar stock. La recomendación exige otro snapshot."""
    product = catalog.products.get(selection.product_id)
    if product is None:
        raise ClarificationRequired("unknown_product", "No tengo información de ese producto; consulta al personal.")
    if selection.size is None:
        raise ClarificationRequired("missing_size", "¿Qué tamaño prefieres? Si no está documentado, consulta al personal.")
    variant = next((item for item in product.variants if item.size == selection.size), None)
    if variant is None:
        raise ClarificationRequired("invalid_size", "Ese tamaño no está documentado para esta bebida. ¿Quieres revisar otra opción?")
    extras = _validate_extras(catalog, product, selection)
    extra_cents = sum(extra.price_cents for extra in extras)
    provisional = variant.price_status == "provisional"
    notices = [PROVISIONAL_NOTICE] if provisional else []
    if variant.size_notice is not None:
        notices.append(variant.size_notice)
    if selection.size == UNKNOWN_SIZE:
        notices.append("Porción sin confirmar; consulta al personal.")
    sources = [variant.price_source]
    if variant.size_source is not None:
        sources.append(variant.size_source)
    for extra in extras:
        sources.extend(extra.sources)
    return Quote(selection, variant.price_cents, extra_cents, variant.price_cents + extra_cents,
                 provisional, tuple(notices), tuple(dict.fromkeys(sources)), variant.size_status, variant.size_label)


def in_stock(quote: Quote, stock: Availability) -> bool:
    if not stock.confirmed:
        raise ClarificationRequired("unconfirmed_stock", "Disponibilidad sin actualizar. Espera a recuperar la conexión.")
    ids = quote.selection
    if ids.product_id not in stock.products or any(extra not in stock.extras for extra in ids.extras):
        raise ClarificationRequired("incomplete_stock", "No puedo confirmar la disponibilidad de esta combinación.")
    return stock.products[ids.product_id] and all(stock.extras[extra] for extra in ids.extras)


@dataclass(frozen=True)
class Proposal:
    quote: Quote
    excess_cents: int
    changes: tuple[str, ...]
    requires_confirmation: bool
    stock_revision: int


def budget_options(catalog: Catalog, candidates: Sequence[Selection], budget_cents: int,
                   stock: Availability, *, requested: Selection,
                   choose_size: bool = False) -> tuple[Proposal, ...]:
    """Candidatos definidos por preferencias aclaradas; no inventa ni amplía la búsqueda.

    Si hay opciones dentro del presupuesto, no ofrece exceso. Cambios y excesos
    son propuestas para confirmar; nunca modifican la selección solicitada.
    """
    nonnegative_integer(budget_cents, "Presupuesto")
    if type(choose_size) is not bool:
        raise ValueError("La intención de elegir tamaño debe ser explícita.")
    if requested.size is None and choose_size:
        # «¿Qué tamaño me alcanza?» autoriza comparar tamaños, no elegir uno.
        product = catalog.products.get(requested.product_id)
        if product is None:
            raise ClarificationRequired("unknown_product", "No tengo información de ese producto; consulta al personal.")
        _validate_extras(catalog, product, requested)
    else:
        calculate_quote(catalog, requested)  # no evadir aclaraciones mediante alternativas
    if not stock.confirmed:
        raise ClarificationRequired("unconfirmed_stock", "Disponibilidad sin actualizar. Espera a recuperar la conexión.")
    quotes = []
    for candidate in dict.fromkeys(candidates):
        quote = calculate_quote(catalog, candidate)
        product = catalog.products[candidate.product_id]
        variant = next(item for item in product.variants if item.size == candidate.size)
        if not variant.recommendation_allowed:
            raise ClarificationRequired("unknown_portion", "Confirma la porción con el personal antes de recomendar esta preparación.")
        if in_stock(quote, stock):
            quotes.append(quote)
    fits = [quote for quote in quotes if quote.total_cents <= budget_cents]
    eligible = fits or [quote for quote in quotes if quote.total_cents <= budget_cents + 5000]
    proposals = []
    for quote in eligible:
        candidate = quote.selection
        changes = []
        if candidate.product_id != requested.product_id:
            changes.append("Sustituir producto; requiere confirmación.")
        if candidate.size != requested.size:
            changes.append("Elegir tamaño; requiere confirmación." if requested.size is None
                           else "Cambiar tamaño; requiere confirmación.")
        if set(candidate.extras) != set(requested.extras):
            changes.append("Cambiar extras; requiere confirmación.")
        excess = max(0, quote.total_cents - budget_cents)
        proposals.append(Proposal(quote, excess, tuple(changes), bool(changes or excess), stock.revision))
    return tuple(proposals)
