"""Composición general; el catálogo aporta nombres y el dominio aporta importes y estado.

No recibe respuestas libres del modelo. No constituye un intérprete de intención,
una UI ni un permiso para ejecutar acciones o adoptar alternativas.
"""

from dataclasses import dataclass, field, replace

from coffee_house.domain.catalog import SINGLE_PRESENTATION, Source, nonnegative_integer
from coffee_house.domain.pricing import (
    AvailabilityDetails,
    ClarificationRequired,
    availability_details,
    calculate_quote,
    format_mxn,
    in_stock,
)
from coffee_house.retrieval.queries import GroundedQueries, Publication, VerifiedFacts
from coffee_house.storage.sqlite import StorageUnavailable


class InvalidFacts(ValueError):
    pass


@dataclass(frozen=True)
class CustomerReply:
    text: str
    status: str
    sources: tuple[Source, ...] = ()
    notices: tuple[str, ...] = ()
    stock_revision: int | None = None
    simulated: bool = True
    origin: str = field(default="verified_data", init=False)

    @property
    def source_references(self):
        return tuple(dict.fromkeys(source.brief for source in self.sources))


def _join(names):
    return " y ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + " y " + names[-1]


class ResponseComposer:
    def __init__(self, queries: GroundedQueries):
        self.queries = queries
        self.catalog = queries.catalog

    def _label(self, selection):
        name = self.catalog.products[selection.product_id].name
        size = "presentación única" if selection.size == SINGLE_PRESENTATION else selection.size
        return name if size is None else f"{name} {size}"

    def _extra_names(self, ids):
        return _join([self.catalog.extras[key].name for key in ids])

    def _reply(self, text, status, quote=None, revision=None, sources=(), notices=()):
        sources = tuple(dict.fromkeys((*sources, *(quote.sources if quote else ()))))
        notices = tuple(dict.fromkeys((*notices, *(quote.notices if quote else ()))))
        if notices:
            text += " " + "; ".join(notices) + "."
        return CustomerReply(text, status, sources, notices, revision)

    def clarify(self, code):
        # Códigos cerrados; no reutilizar message/text de un modelo o una excepción.
        messages = {
            "missing_size": "¿Qué tamaño prefieres?",
            "missing_modality": "¿Lo prefieres caliente o frío?",
            "ambiguous_product": "¿A qué producto te refieres?",
            "repeated_extra": "Podemos agregar una unidad de cada extra. ¿Te parece bien?",
            "exclusive_milks": "Solo puedes elegir una leche. ¿Prefieres avena o almendras?",
            "invalid_size": "Ese tamaño no lo tenemos para esta bebida. ¿Te gustaría revisar otra opción?",
            "invalid_extra_group": "Ese extra no corresponde a esta bebida. ¿Te gustaría revisar otra opción?",
            "unknown_extra_group": "No tengo confirmado qué extras podemos agregar a esta preparación. Consulta al personal.",
            "unknown_product": "No tengo información de ese producto. Nuestro personal te puede ayudar.",
            "unknown_extra": "No tengo información de ese extra. Nuestro personal te puede ayudar.",
            "stock_changed": "La disponibilidad cambió mientras consultaba. Necesito revisarla de nuevo.",
            "incomplete_stock": "No puedo confirmar si todavía tenemos esa combinación. Nuestro personal te puede ayudar.",
        }
        if code == "unconfirmed_stock":
            return self.unavailable()
        return self._reply(messages.get(code, "No tengo esa información confirmada. Nuestro personal te puede ayudar."), "clarification")

    def unavailable(self, *, model=False):
        text = ("El asistente no está disponible por ahora. Puedes seguir consultando el menú." if model else
                "Por ahora no puedo consultar qué nos queda. Puedes ver el menú; nuestro personal te puede ayudar.")
        return self._reply(text, "model_unavailable" if model else "stock_unavailable")

    def prepare_stock(self, selection):
        return availability_details(self.catalog, selection, self.queries.stock.snapshot())

    def _stock_text(self, details):
        label = self._label(details.selection)
        if not details.confirmed:
            return self.unavailable().text, "stock_unavailable"
        missing = [key for key, value in details.extras_available.items() if value is None]
        exhausted = [key for key, value in details.extras_available.items() if value is False]
        if details.product_available is False:
            text, status = f"Se nos terminó {label}.", "out"
        elif details.product_available is None:
            text, status = f"No sé si todavía queda {label}. Nuestro personal te lo puede confirmar.", "unknown"
        else:
            text, status = f"Sí, todavía tenemos {label}.", "available"
        if exhausted:
            text += f" Por ahora no tenemos {self._extra_names(exhausted)}."
            status = "out"
        if missing:
            text += f" No sé si queda {self._extra_names(missing)}; nuestro personal te lo puede confirmar."
            if status != "out":
                status = "unknown"
        return text, status

    def stock(self, selection, *, prepared: AvailabilityDetails | None = None):
        if prepared is not None and prepared.selection != selection:
            raise InvalidFacts("La consulta no corresponde a la selección preparada")
        try:
            current = self.prepare_stock(selection)  # Siempre después de cualquier generación.
        except StorageUnavailable:
            return self.unavailable()
        except ClarificationRequired as error:
            return self.clarify(error.code)
        text, status = self._stock_text(current)
        if prepared is not None and prepared.available is True and current.available is False:
            text = "Disculpa, cambió la disponibilidad. " + text
        product = self.catalog.products[selection.product_id]
        sources = (product.source, *(s for key in selection.extras for s in self.catalog.extras[key].sources))
        notices = ()
        if selection.size is not None:
            variant = next(v for v in product.variants if v.size == selection.size)
            notices = (variant.size_notice,) if variant.size_notice else ()
        return self._reply(text, status, revision=current.revision, sources=sources, notices=notices)

    def publication(self, publication: Publication):
        """Consume solo Publication emitida por el backend, nunca DTOs del cliente.

        message se ignora: incluso un texto incorrecto anexado no altera los hechos.
        Relee la revisión antes de componer; T9/T10 aplicarán eventos posteriores.
        """
        facts = publication.facts
        if facts.quote != calculate_quote(self.catalog, facts.quote.selection):
            raise InvalidFacts("Cotización no coincide con catálogo y selección")
        nonnegative_integer(facts.stock_revision, "Revisión")
        if type(facts.available) is not bool or facts.simulated is not True:
            raise InvalidFacts("Hechos fuera del contrato de la demo")
        if facts.details is not None:
            d = facts.details
            if (d.selection != facts.quote.selection or d.revision != facts.stock_revision
                    or not d.confirmed or d.available is not facts.available):
                raise InvalidFacts("Detalle de disponibilidad inconsistente")
        if publication.became_unavailable and facts.available:
            raise InvalidFacts("Cambio de disponibilidad contradictorio")
        snapshot = self.queries.stock.snapshot()
        if snapshot.revision != facts.stock_revision:
            raise ClarificationRequired("stock_changed", "La disponibilidad cambió antes de responder")
        if in_stock(facts.quote, snapshot) is not facts.available:
            raise InvalidFacts("La existencia no coincide con la base")
        if facts.details is not None and facts.details != availability_details(self.catalog, facts.quote.selection, snapshot):
            raise InvalidFacts("Detalle no coincide con la base")
        quote = facts.quote
        if facts.available:
            extras = f", con {self._extra_names(quote.selection.extras)}" if quote.selection.extras else ", sin extras"
            text = f"{self._label(quote.selection)}{extras}: serían {format_mxn(quote.total_cents)}."
            status = "available"
        elif facts.details is not None:
            text, status = self._stock_text(facts.details)
        else:
            text, status = "Por ahora no tenemos esa combinación.", "out"
        if publication.became_unavailable:
            text = "Disculpa, cambió la disponibilidad. " + text
        sources = list(quote.sources)
        notices = list(quote.notices)
        offers = []
        for proposal in publication.alternatives:
            if (proposal.stock_revision != facts.stock_revision
                    or proposal.quote != calculate_quote(self.catalog, proposal.quote.selection)):
                raise InvalidFacts("Alternativa desactualizada o cotización inválida")
            nonnegative_integer(proposal.excess_cents, "Exceso")
            if proposal.excess_cents > 5000:
                raise InvalidFacts("Alternativa excede el límite de presupuesto")
            if not in_stock(proposal.quote, snapshot):
                raise InvalidFacts("Alternativa agotada")
            if proposal.requires_confirmation is not True and (proposal.changes or proposal.excess_cents):
                raise InvalidFacts("Alternativa requiere confirmación")
            alternative = proposal.quote
            description = self._label(alternative.selection)
            if alternative.selection.extras:
                description += f" con {self._extra_names(alternative.selection.extras)}"
            else:
                description += " sin extras"
            description += f" por {format_mxn(alternative.total_cents)}"
            if proposal.excess_cents:
                description += f" ({format_mxn(proposal.excess_cents)} más de tu presupuesto)"
            offers.append(description)
            sources.extend(alternative.sources)
            notices.extend(alternative.notices)
        if offers:
            text += f" Tenemos {_join(offers)}. ¿Te gustaría considerar alguna de estas opciones?"
        elif not facts.available:
            text += " Nuestro personal te puede ayudar a buscar otra opción."
        return self._reply(text, status, revision=facts.stock_revision, sources=sources, notices=notices)

    def quote(self, selection, *, prepared: VerifiedFacts | None = None, candidates=(), budget_cents=None):
        if prepared is not None and prepared.quote.selection != selection:
            raise InvalidFacts("No adoptar otra selección sin confirmación")
        try:
            quote = calculate_quote(self.catalog, selection)
        except ClarificationRequired as error:
            if error.code in {"unknown_extra", "unknown_extra_group"}:
                try:
                    base = calculate_quote(self.catalog, replace(selection, extras=()))
                except ClarificationRequired as nested:
                    return self.clarify(nested.code)
                missing = ("No tengo el precio de ese extra; nuestro personal te puede confirmar el total." if error.code == "unknown_extra" else
                           "No tengo confirmado si ese extra corresponde a esta preparación; consulta al personal antes de combinarlo.")
                return self._reply(f"{self._label(selection)} cuesta {format_mxn(base.base_cents)} sin extras. {missing}",
                                   "partial_price", quote=base)
            return self.clarify(error.code)
        try:
            publication = self.queries.publish(prepared or self.queries.prepare(selection),
                                               candidates=tuple(candidates), budget_cents=budget_cents)
            reply = self.publication(publication)
            if budget_cents is not None and publication.facts.quote.total_cents > budget_cents and not publication.alternatives:
                reply = replace(reply,text=reply.text + " Entre las opciones que elegiste, no encontré alguna dentro de tu presupuesto ni hasta $50 más. ¿Te gustaría revisar otras bebidas?")
            return reply
        except StorageUnavailable:
            return self._reply(self.unavailable().text + f" El precio de {self._label(selection)} con los extras elegidos sería {format_mxn(quote.total_cents)}.",
                               "stock_unavailable", quote=quote)
        except ClarificationRequired as error:
            if error.code in {"unconfirmed_stock", "incomplete_stock"}:
                return self._reply(self.clarify(error.code).text + f" El precio de la combinación elegida sería {format_mxn(quote.total_cents)}.",
                                   "stock_unavailable" if error.code == "unconfirmed_stock" else "unknown", quote=quote)
            return self.clarify(error.code)
