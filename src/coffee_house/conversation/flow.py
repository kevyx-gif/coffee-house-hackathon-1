"""Recuperación → resolución conservadora → borrador Llama → respuesta verificada.

Sin UI, estado persistente, órdenes, cola ni texto factual libre publicado.
"""
import asyncio
import json
import re
import time
from dataclasses import dataclass

import httpx

from coffee_house.conversation.browse import browse_reply
from coffee_house.domain.pricing import ClarificationRequired, Selection
from coffee_house.inference.client import GeneratedText, InferenceError, LlamaClient
from coffee_house.responses import CustomerReply, InvalidFacts, ResponseComposer
from coffee_house.retrieval.queries import Decision, GroundedQueries
from coffee_house.retrieval.search import RetrievalUnavailable, normalize
from coffee_house.storage.sqlite import StorageUnavailable


@dataclass(frozen=True)
class TurnResult:
    reply: CustomerReply
    decision: str
    purpose: str
    elapsed_seconds: float
    inference: GeneratedText | None = None
    failure: str | None = None
    selection: Selection | None = None
    context: object = None
    browse_question: str | None = None


class ConversationFlow:
    def __init__(self, queries: GroundedQueries, client: LlamaClient):
        self.queries, self.client = queries, client
        self.composer = ResponseComposer(queries)

    def revalidate(self, value):
        if value.browse_question is not None:
            try:
                return browse_reply(value.browse_question, self.queries) or value.reply
            except StorageUnavailable:
                return self.composer.unavailable()
        return value.reply

    async def answer(self, question: str, *, confirmed: Selection | None = None,
                     purpose: str | None = None, candidates=(), budget_cents=None,
                     seconds: float = 120, on_slot=None) -> TurnResult:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("La pregunta no puede estar vacía")
        if not 0 < seconds <= 120:
            raise ValueError("Plazo máximo: 120 segundos")
        started = time.monotonic()
        text = normalize(question)
        if purpose is None:
            purpose = "quote" if re.search(r"\b(cuesta|cuestan|precio|precios|total|cuanto|cuantos)\b", text) else "stock"
        if purpose not in {"stock", "quote"}:
            raise ValueError("Consulta no soportada")
        generated = None
        decision = Decision("unresolved", "")
        try:
            async with asyncio.timeout(seconds):
                listing = browse_reply(question, self.queries)
                if listing is not None:
                    return TurnResult(listing, "catalog_options", "browse", time.monotonic()-started,
                                      browse_question=question)
                result = await asyncio.to_thread(self.queries.index.search, question)
                # No heredar una bebida anterior si se menciona otra o cambia una preferencia.
                explicit = any(c.exact_length > 0 for c in result.candidates if c.document.product_id)
                changed = bool(result.restrictions or result.numbers or
                    re.search(r"\b(fr[aá]ppe|caliente|frio|mediano|grande|extra|extras|avena|almendras|cambia|otro|otra)\b", text))
                followup = re.fullmatch(r"(cuanto cuesta|cual seria el total|cuanto seria|todavia hay|aun tienen|hay todavia|sigue disponible|sigue en stock|y el precio)[?¿!¡. ]*", text.strip("?¿!¡. "))
                context = confirmed if followup and not explicit and not changed else None
                decision = self.queries.resolve(result, confirmed=context, purpose=purpose)
                # Una mención del mismo producto no borra tamaño/extras ya aclarados.
                # Cambios ambiguos, restricciones o extras expresados siempre se preguntan.
                unsafe = bool(result.restrictions or re.search(r"\b(extra|extras|avena|almendras|doble|dos|tres|otro|otra|nuevo|nueva|chico|chica|pequeno|pequena)\b", text))
                if confirmed is not None and context is None and not unsafe:
                    same = decision.selection is not None and decision.selection.product_id == confirmed.product_id
                    missing_size = not re.search(r"\b(mediano|mediana|grande)\b", text) and decision.status == "clarification" and decision.candidates == (confirmed.product_id,) and (
                        "tamaño prefieres" in decision.message or "presentación única provisional" in decision.message)
                    if same or missing_size and not result.numbers:
                        selected_size = decision.selection.size if same else None
                        retained = Selection(confirmed.product_id, selected_size or confirmed.size, confirmed.extras)
                        decision = self.queries.resolve(result, confirmed=retained, purpose=purpose)
                if confirmed is not None and not explicit and changed and decision.status == "outside_scope":
                    name = self.queries.catalog.products[confirmed.product_id].name
                    decision = Decision("clarification", f"¿Te refieres a {name}? Confírmame el tamaño y los extras que quieres.")
                prepared = None
                prepared_stock = None
                evidence = {"decision": decision.status, "sources": [s.brief for s in decision.sources]}
                if decision.selection is not None:
                    if purpose == "quote":
                        try:
                            prepared = self.queries.prepare(decision.selection)
                            evidence.update(total_cents=prepared.quote.total_cents, available=prepared.available,
                                            revision=prepared.stock_revision)
                        except (StorageUnavailable, ClarificationRequired):
                            evidence["availability"] = "unknown"
                    else:
                        try:
                            prepared_stock = self.composer.prepare_stock(decision.selection)
                            evidence.update(product_available=prepared_stock.product_available,
                                extras_available=dict(prepared_stock.extras_available), revision=prepared_stock.revision)
                        except (StorageUnavailable, ClarificationRequired):
                            evidence["availability"] = "unknown"
                documents = [c.document.text for c in result.candidates[:3]]
                messages = [{"role": "system", "content":
                    "Eres el asistente de Coffee & Matcha House. Habla español natural y breve. "
                    "No inventes datos ni confirmes pedidos. Si faltan preferencias, pregunta. "
                    "Solo usa estos documentos y hechos para un borrador interno: " +
                    json.dumps({"documents": documents, "facts": evidence}, ensure_ascii=False)},
                    {"role": "user", "content": question}]
                remaining = seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError
                slot_callback = {"on_slot": on_slot} if on_slot is not None else {}
                generated = await self.client.generate(messages, seconds=remaining, max_tokens=128, **slot_callback)
                # Nunca concatenar generated.text: no tiene autoridad sobre stock/precios.
                if decision.selection is not None:
                    if purpose == "stock":
                        reply = self.composer.stock(decision.selection, prepared=prepared_stock)
                    else:
                        reply = self.composer.quote(decision.selection, prepared=prepared,
                            candidates=candidates, budget_cents=budget_cents)
                elif re.fullmatch(r"(hola|ola|buenos dias|buenas tardes|buenas noches)[!¡?. ]*", text):
                    reply = CustomerReply("¡Hola! Puedo orientarte con el menú y con información de la cafetería. ¿Qué te gustaría consultar?", "greeting")
                else:
                    # Decision.message pertenece a rutas cerradas del backend, no al LLM.
                    reply = CustomerReply(decision.message, decision.status, decision.sources)
                if time.monotonic() - started >= seconds:
                    raise TimeoutError
                return TurnResult(reply, decision.status, purpose, time.monotonic() - started, generated,
                                  selection=decision.selection)
        except RetrievalUnavailable:
            reply = CustomerReply("Por ahora no puedo consultar esa información. Puedes revisar el menú o preguntar a nuestro personal.", "retrieval_unavailable")
            failure = "retrieval_unavailable"
        except (InferenceError, httpx.HTTPError, TimeoutError):
            reply, failure = self.composer.unavailable(model=True), "inference_unavailable"
        except ClarificationRequired as error:
            reply, failure = self.composer.clarify(error.code), "clarification_required"
        except (InvalidFacts, StorageUnavailable):
            reply, failure = self.composer.unavailable(), "facts_unavailable"
        # CancelledError se propaga: no entrega una respuesta tardía tras cancelación.
        return TurnResult(reply, decision.status, purpose, time.monotonic() - started, generated, failure)
