"""Aclaraciones y hechos verificados antes de publicar; no sustituye a Llama."""

import re
from dataclasses import dataclass
from typing import Protocol

from coffee_house.domain.catalog import SINGLE_PRESENTATION, Catalog, Source
from coffee_house.domain.pricing import (
    Availability,
    AvailabilityDetails,
    ClarificationRequired,
    Proposal,
    Quote,
    Selection,
    availability_details,
    budget_options,
    calculate_quote,
    in_stock,
)

from .search import HybridIndex, SearchResult


class StockReader(Protocol):
    def snapshot(self) -> Availability: ...


@dataclass(frozen=True)
class Decision:
    status: str
    message: str
    candidates: tuple[str, ...] = ()
    selection: Selection | None = None
    sources: tuple[Source, ...] = ()


@dataclass(frozen=True)
class VerifiedFacts:
    quote: Quote
    available: bool
    stock_revision: int
    simulated: bool = True
    details: AvailabilityDetails | None = None


@dataclass(frozen=True)
class Publication:
    facts: VerifiedFacts
    stock_changed: bool
    message: str
    alternatives: tuple[Proposal, ...]
    became_unavailable: bool = False


class GroundedQueries:
    def __init__(self, catalog: Catalog, index: HybridIndex, stock: StockReader):
        self.catalog, self.index, self.stock = catalog, index, stock

    def resolve(self, result: SearchResult, *, confirmed: Selection | None = None,
                purpose: str = "quote") -> Decision:
        """Confirmación aportada por contexto humano; no aceptar IDs elegidos por el LLM."""
        text = result.normalized
        if purpose not in {"quote", "stock"}:
            raise ValueError("Tipo de consulta inválido")
        if re.search(r"\b(horario|horarios|direccion|ubicacion|wifi|wi fi|internet|alergia|alergias|alergenos|gluten|panaderia|reposteria)\b", text):
            return Decision("missing_information", "No tengo esa información confirmada. Consulta al personal.",
                            sources=(Source("spec_001_r18", "Información autorizada y ausencias"),))
        if re.search(r"\b(ignora|administrador|contrasena|token|modifica|actualiza|cambia el precio)\b", text):
            return Decision("clarification", "Solo puedo consultar el menú y la disponibilidad simulada; no modificar datos.")
        if confirmed is not None:
            if purpose == "quote":
                calculate_quote(self.catalog, confirmed)
            else:
                availability_details(self.catalog, confirmed, Availability({}, {}, 0, False))
            # No reaplica detección heurística: contexto confirmado conserva extras/temperatura/tamaño.
            return Decision("selected", "Selección confirmada para consulta informativa.",
                            selection=confirmed)
        products = [c for c in result.candidates if c.document.product_id is not None]
        exact = [c for c in products if c.exact_length > 0]
        if not exact:
            domain_anchor = re.search(r"\b(cafe|bebida|leche|matcha|te|chocolate|fresa)\b", text)
            suggested = tuple(c.document.product_id for c in products
                              if c.fuzzy_score >= 75 or domain_anchor and c.semantic_score >= 0.50)
            if suggested:
                return Decision("clarification", "¿A cuál de estas opciones te refieres?", suggested)
            return Decision("outside_scope", "Por el momento no tengo información sobre eso. ¿Hay algo más de Coffee & Matcha House en lo que te pueda ayudar?")
        longest = max(c.exact_length for c in exact)
        identifiers = [c.document.product_id for c in exact if c.exact_length == longest]
        modes = set()
        for mode, pattern in (("hot", r"\b(caliente|hot)\b"), ("iced", r"\b(frio|fria|iced)\b"),
                              ("frappe", r"\b(frape|frappe)\b")):
            if re.search(pattern, text):
                modes.add(mode)
        if len(modes) > 1:
            return Decision("clarification", "¿Qué temperatura o modalidad prefieres?", tuple(identifiers))
        if modes:
            identifiers = [key for key in identifiers if key.split("_", 1)[0] in modes]
        if len(identifiers) != 1:
            return Decision("clarification", "¿Qué producto y modalidad prefieres?", tuple(identifiers))
        product = self.catalog.products[identifiers[0]]
        if re.search(r"\b(ingredientes|contiene|lleva)\b", text):
            if product.documented_ingredients is None:
                return Decision("missing_information", "No tengo los ingredientes confirmados de esta preparación. Consulta al personal.",
                                (product.id,), sources=(product.source,))
            return Decision("documented_information", "Ingredientes parcialmente documentados: "
                            + ", ".join(product.documented_ingredients)
                            + ". La lista no es exhaustiva ni garantiza ausencia de alérgenos.",
                            (product.id,), sources=(product.source,))
        sizes = set()
        for size, pattern in (("mediano", r"\b(mediano|mediana|12\s*oz|360\s*ml)\b"),
                              ("grande", r"\b(grande|16\s*oz|480\s*ml)\b")):
            if re.search(pattern, text):
                sizes.add(size)
        if len(sizes) > 1:
            return Decision("clarification", "¿Qué tamaño prefieres?", (product.id,))
        size = next(iter(sizes), None)
        # La única presentación provisional no se convierte en una elección implícita.
        if product.variants[0].size == SINGLE_PRESENTATION and not (purpose == "stock" and size is None):
            if re.search(r"\b(mediano|mediana|grande|12\s*oz|16\s*oz|480\s*ml)\b", text):
                return Decision("clarification", "Esta preparación tiene una presentación única provisional. ¿La confirmas?", (product.id,))
            if not re.search(r"\b(presentacion unica|" + str(product.variants[0].volume_ml) + r"\s*ml)\b", text):
                return Decision("clarification", "¿Confirmas la presentación única provisional?", (product.id,))
            size = SINGLE_PRESENTATION
        if size is None and purpose != "stock":
            return Decision("clarification", "¿Qué tamaño prefieres?", (product.id,))
        # Restricciones, extras y cantidades se conservan y se aclaran; nunca se eliminan para cotizar base.
        allowed_numbers = {"12", "16", "360", "480", str(product.variants[0].volume_ml)}
        if result.restrictions or any(n not in allowed_numbers for n in result.numbers) or re.search(r"\b(avena|almendra|almendras|extra|extras|doble|dos|tres|presupuesto|pesos)\b|\$", text):
            return Decision("clarification", "Confirma cantidad, extras y restricciones para calcular la opción correcta.", (product.id,))
        selection = Selection(product.id, size, ())
        if purpose == "stock":
            availability_details(self.catalog, selection, Availability({}, {}, 0, False))
            return Decision("selected", "Consulta de existencia del producto completo.",
                            (product.id,), selection, (product.source,))
        try:
            quote = calculate_quote(self.catalog, selection)
        except ClarificationRequired as error:
            return Decision("clarification", str(error), (product.id,))
        return Decision("selected", "Consulta de precio base; sin extras incluidos en este importe.",
                        (product.id,), selection, quote.sources)

    def prepare(self, selection: Selection) -> VerifiedFacts:
        quote = calculate_quote(self.catalog, selection)
        stock = self.stock.snapshot()
        return VerifiedFacts(quote, in_stock(quote, stock), stock.revision,
                             details=availability_details(self.catalog, selection, stock))

    def publish(self, prepared: VerifiedFacts, *, candidates: tuple[Selection, ...] = (),
                budget_cents: int | None = None) -> Publication:
        """Invocar DESPUÉS de generación. Texto del modelo nunca es autoridad numérica.

        El consumidor renderiza estos hechos de forma separada; revisiones futuras
        se notifican mediante T9. No existe una garantía de inmovilidad del stock.
        """
        current = self.prepare(prepared.quote.selection)
        changed = current.stock_revision != prepared.stock_revision
        message = "Disponibilidad simulada: en existencia." if current.available else "Disponibilidad simulada: agotado."
        if prepared.available and not current.available:
            message = "Disculpa, esta opción se agotó en la simulación desde la consulta anterior. Podemos revisar alternativas."
        proposals = ()
        if candidates:
            if budget_cents is None:
                raise ValueError("Confirma el presupuesto antes de proponer alternativas.")
            # Segunda lectura explícita: ninguna propuesta usa el snapshot previo a generación.
            stock = self.stock.snapshot()
            proposals = budget_options(self.catalog, candidates, budget_cents, stock,
                                       requested=prepared.quote.selection)
            if stock.revision != current.stock_revision:
                raise ClarificationRequired("stock_changed", "La disponibilidad cambió de nuevo; vuelve a consultar antes de publicar.")
        return Publication(current, changed, message, proposals,
                           became_unavailable=prepared.available and not current.available)
