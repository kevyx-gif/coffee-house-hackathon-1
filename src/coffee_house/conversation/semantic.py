"""Llama clasifica la consulta; preferencias explícitas y hechos se validan aparte."""
import asyncio
import re
import time
from dataclasses import dataclass, replace

import httpx
from pydantic import BaseModel, ConfigDict

from coffee_house.domain.catalog import Source
from coffee_house.domain.pricing import (
    ClarificationRequired,
    Selection,
    availability_details,
    format_mxn,
)
from coffee_house.inference.client import InferenceError
from coffee_house.responses import CustomerReply, ResponseComposer
from coffee_house.retrieval.search import RetrievalUnavailable, normalize
from coffee_house.storage.sqlite import StorageUnavailable

from .browse import browse_reply
from .flow import TurnResult
from .preferences import extract_preferences, family_of, mode_of


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: str


@dataclass(frozen=True)
class ConversationState:
    family: str | None = None
    modes: tuple[str, ...] = ()
    sizes: tuple[str, ...] = ()
    extras: tuple[str, ...] = ()
    budget: int | None = None
    purpose: str = "quote"
    extra_count: int = 1
    proposed_family: str | None = None
    pending: str | None = None
    unresolved: str | None = None


class SemanticConversationFlow:
    def __init__(self, queries, client):
        self.queries, self.client = queries, client
        self.composer = ResponseComposer(queries)

    def _state(self, confirmed):
        if isinstance(confirmed, ConversationState):
            return confirmed
        if isinstance(confirmed, Selection):
            mode = mode_of(confirmed.product_id)
            return ConversationState(family_of(confirmed.product_id), () if mode=="unique" else (mode,),
                (confirmed.size,) if confirmed.size else (), confirmed.extras)
        return ConversationState()

    def _merge(self, delta, previous, text, intent):
        yes = bool(re.fullmatch(r"(si[, ]*(?:por favor|porfa)?|ese|esa|correcto|correcta)[!?. ]*", text))
        family = delta.family
        if previous.proposed_family and (yes or delta.modes and delta.family is None):
            family = previous.proposed_family
        family = family or previous.family
        old = previous if family == previous.family else ConversationState()
        # Al confirmar una propuesta aproximada, conservar solo preferencias del mensaje original.
        if previous.proposed_family and (yes or delta.modes and delta.family is None):
            old = previous
        extras = () if delta.clear_extras else tuple(e for e in old.extras if e not in delta.removed)
        choosing_milk = old.pending == "milk" and len([e for e in delta.extras if e.startswith("leche_")]) == 1
        if delta.replace_milk or choosing_milk:
            extras = tuple(e for e in extras if not e.startswith("leche_"))
        extras = tuple(dict.fromkeys((*extras, *delta.extras)))
        purpose = old.purpose
        if re.search(r"\b(cuanto|cuesta|cuestan|precio|precios|total|sale|salen)\b", text):
            purpose = "quote"
        elif re.search(r"\b(hay|tienen|tiene|queda|quedan|existencia|disponible|disponibles|stock)\b", text):
            purpose = "stock"
        elif delta.budget is not None or re.search(r"\b(recomienda|recomiendame|alcanza)\b", text):
            purpose = "recommend"
        elif intent == "existencia" and not (delta.extras or delta.removed or delta.clear_extras or delta.sizes or delta.modes):
            purpose = "stock"
        elif intent == "precio" and not previous.family and not previous.proposed_family:
            purpose = "quote"
        count = delta.extra_count if delta.extra_count is not None else old.extra_count
        if "espresso_extra" in old.extras and "espresso_extra" in delta.extras and re.search(r"\b(otro|otra|uno mas|un extra mas)\b", text):
            count = old.extra_count + (delta.extra_count or 1)
        if delta.clear_extras or "espresso_extra" in delta.removed:
            count = 1
        pending = None
        if "leche_avena" in extras and "leche_almendras" in extras:
            pending = "milk"
        elif count > 1:
            pending = "count"
        sizes = delta.sizes or old.sizes
        if yes and old.pending == "size" and family:
            products = [p for p in self.queries.catalog.products.values() if family_of(p.id)==family]
            products = [p for p in products if not (delta.modes or old.modes) or mode_of(p.id) in (delta.modes or old.modes)]
            if len(products)==1 and len(products[0].variants)==1:
                sizes = (products[0].variants[0].size,)
        unresolved = delta.unknown or old.unresolved
        if old.unresolved == "extra" and delta.clear_extras or old.unresolved == "presupuesto" and delta.budget is not None or old.unresolved == "cantidad" and delta.extra_count is not None:
            unresolved = delta.unknown
        if re.search(r"\b(olvida esa restriccion|sin esa restriccion|quita esa restriccion)\b", text):
            unresolved = None
        return ConversationState(family, delta.modes or old.modes, sizes, extras,
            delta.budget if delta.budget is not None else old.budget, purpose, count,
            delta.proposed_family, pending, unresolved)

    def _reply(self,text,status="clarification",sources=(),revision=None):
        return CustomerReply(text,status,tuple(sources),stock_revision=revision)

    def _products(self,state):
        products=[p for p in self.queries.catalog.products.values() if family_of(p.id)==state.family]
        if state.modes:products=[p for p in products if mode_of(p.id) in state.modes or mode_of(p.id)=="unique"]
        return products

    def render(self,state,*,previous_reply=None):
        snapshot = self.queries.stock.snapshot()
        if state.family is None:return self.composer.clarify("ambiguous_product"),None
        products=self._products(state)
        if not products:return self.composer.clarify("unknown_product"),None
        if not state.modes and len({mode_of(p.id) for p in products})>1:
            return self._reply("¿Lo prefieres caliente, frío o frappé?" if any(mode_of(p.id)=="frappe" for p in products) else "¿Lo prefieres caliente o frío?"),None
        if state.extra_count>1:return self.composer.clarify("repeated_extra"),None
        if "leche_avena" in state.extras and "leche_almendras" in state.extras:
            stock=self.queries.stock.snapshot();text="Solo puedes elegir una leche."
            if stock.extras.get("leche_almendras") is False:text+=" Por ahora no tenemos leche de almendras."
            return self._reply(text+" ¿Prefieres avena o consultar otra opción?"),None
        selections=[]
        for p in products:
            if state.sizes:
                if any(size not in {v.size for v in p.variants} for size in state.sizes):
                    current=self.composer.stock(Selection(p.id,None,state.extras))
                    return self._reply("Para "+p.name+" solo tengo documentado "+", ".join(v.size_label for v in p.variants)+". "+(current.text if current.status=="out" else "¿Te parece bien ese tamaño?"),sources=current.sources,revision=current.stock_revision),None
                selections.extend(Selection(p.id,size,state.extras) for size in state.sizes)
            elif state.purpose=="stock":selections.append(Selection(p.id,None,state.extras))
            elif state.purpose=="recommend" or state.budget is not None:
                selections.extend(Selection(p.id,v.size,state.extras) for v in p.variants if v.recommendation_allowed)
            else:
                return self._reply("¿Qué tamaño prefieres: mediano o grande?" if len(p.variants)>1 else "Tenemos "+p.name+" en "+p.variants[0].size_label+". "+(p.variants[0].size_notice+". " if p.variants[0].size_notice else "")+"¿Quieres consultar esa presentación?"),None
        replies=[]
        for selected in selections:
            reply=self.composer.stock(selected) if state.purpose=="stock" else self.composer.quote(selected)
            replies.append(reply)
        if not replies:return self.composer.clarify("missing_size"),None
        sources=tuple(dict.fromkeys(s for r in replies for s in r.sources));revision=snapshot.revision
        text="\n".join(r.text for r in replies)
        if state.budget is not None:
            eligible=[];within=[]
            for selected in selections:
                try:
                    facts=self.queries.prepare(selected)
                    if facts.available and facts.quote.total_cents<=state.budget+5000:
                        eligible.append((selected,facts));
                        if facts.quote.total_cents<=state.budget:within.append((selected,facts))
                except ClarificationRequired:continue
            chosen=within or eligible
            if chosen:
                text=" ".join(self.composer.quote(s).text for s,_ in chosen)
                if within:text+=" Entra en tu presupuesto de "+format_mxn(state.budget)+"."
                if not within:text+=" El excedente es "+format_mxn(chosen[0][1].quote.total_cents-state.budget)+". ¿Te parece bien?"
                else:text+=" ¿Cuál prefieres?" if len(chosen)>1 else " ¿Te parece bien?"
            else:
                text+=" Ninguna de estas combinaciones queda dentro de tu presupuesto más $50.00 MXN."
                bare=[Selection(s.product_id,s.size,()) for s in selections]
                changes=[]
                for s in bare:
                    try:
                        f=self.queries.prepare(s)
                        if f.available and f.quote.total_cents<=state.budget+5000:changes.append(self.composer.quote(s).text)
                    except ClarificationRequired:pass
                if changes and state.extras:text+=" Sin extras: "+" ".join(changes)+" ¿Quieres retirar los extras?"
        if any(r.status=="out" for r in replies):
            alternatives=[]
            for p in self.queries.catalog.products.values():
                if family_of(p.id)!=state.family or p in products:continue
                try:
                    available=availability_details(self.queries.catalog,Selection(p.id,None,()),self.queries.stock.snapshot())
                    if available.available:alternatives.append(p.name)
                except ClarificationRequired:continue
            if alternatives:text+=" Podemos revisar "+" o ".join(alternatives[:2])+". ¿Te interesa alguna?"
        if previous_reply is not None and previous_reply.stock_revision is not None and previous_reply.stock_revision!=revision and previous_reply.status=="available" and any(r.status=="out" for r in replies):
            text="Disculpa, cambió la disponibilidad. "+text
        unavailable_milks = [key for key in state.extras if key.startswith("leche_") and snapshot.extras.get(key) is False]
        if unavailable_milks:
            options = [extra.name for extra in self.queries.catalog.extras.values()
                if extra.exclusive_group == "leche_vegetal" and extra.id not in state.extras and
                snapshot.extras.get(extra.id) is True and all(p.extra_group in extra.groups for p in products)]
            if options:
                text += " Podemos usar " + " o ".join(options) + ". ¿Te parece bien?"
        if self.queries.stock.snapshot().revision != revision:
            return self.composer.clarify("stock_changed"), None
        status="out" if any(r.status=="out" for r in replies) else replies[0].status
        return self._reply(text,status,sources,revision),selections[0] if len(selections)==1 else None

    def revalidate(self, value):
        try:
            if value.browse_question is not None:
                return browse_reply(value.browse_question, self.queries) or value.reply
            if isinstance(value.context, ConversationState) and value.reply.stock_revision is not None:
                current = self.queries.stock.snapshot()
                if current.revision != value.reply.stock_revision:
                    return self.render(value.context, previous_reply=value.reply)[0]
            return value.reply
        except StorageUnavailable:
            return self.composer.unavailable()

    async def answer(self, question, *, confirmed=None, purpose=None, candidates=(), budget_cents=None,
                     seconds=120, on_slot=None):
        if not isinstance(question, str) or not question.strip():
            raise ValueError("La pregunta no puede estar vacía")
        if not 0 < seconds <= 120:
            raise ValueError("Plazo máximo: 120 segundos")
        started = time.monotonic()
        previous = self._state(confirmed)
        generated = None
        state = previous
        try:
            async with asyncio.timeout(seconds):
                listing = browse_reply(question, self.queries)
                if listing is not None:
                    return TurnResult(listing, "catalog_options", "browse", time.monotonic()-started,
                                      context=previous, browse_question=question)
                text = normalize(question).strip("?¿!¡., ")
                # Permisos y remisiones se deciden fuera de la inferencia.
                if re.search(r"\b(ignora|administrador|contrasena|token|modifica|actualiza|cambia el precio)\b", text):
                    reply = self._reply("Puedo ayudarte a consultar el menú. Los cambios de disponibilidad se hacen en el panel privado; no puedo mostrar credenciales.", "protected")
                    return TurnResult(reply,"protected","stock",time.monotonic()-started,context=previous)
                if re.search(r"\b(wifi|wi fi|internet|horario|horarios|cierran|direccion|ubicacion|pan|panes|panaderia|reposteria|alergia|alergico|alergenos|garantizas|gluten)\b", text):
                    reply = self._reply("No tengo esa información confirmada. Nuestro personal te lo podrá confirmar.", "missing_information", (Source("spec_001_r18","Información de la cafetería"),))
                    if re.search(r"\b(alerg|garantizas|gluten)", text):
                        reply = replace(reply,text="No puedo garantizar que sea segura para tu alergia ni descartar contaminación cruzada. Consúltalo con nuestro personal antes de consumirla.")
                    return TurnResult(reply,"missing_information","stock",time.monotonic()-started,context=previous)
                if re.fullmatch(r"(hola|ola|buenos dias|buenas tardes|buenas noches)[!?.¡¿ ]*", text):
                    reply = self._reply("¡Hola! Puedo orientarte con el menú y con información de la cafetería. ¿Qué te gustaría consultar?", "greeting")
                    return TurnResult(reply,"greeting","stock",time.monotonic()-started,context=previous)
                result = await asyncio.to_thread(self.queries.index.search, question)
                if re.search(r"\b(ingredientes|contiene|lleva)\b", text):
                    decision = self.queries.resolve(result, purpose="stock")
                    if decision.status in ("documented_information", "missing_information"):
                        reply = self._reply(decision.message, decision.status, decision.sources)
                        return TurnResult(reply, decision.status, "info", time.monotonic()-started, context=previous)
                delta = extract_preferences(question, self.queries.catalog)
                schema = {"type":"object", "properties":{"intent":{"enum":["precio","existencia","recomendacion","continuar"]}},
                          "required":["intent"], "additionalProperties":False}
                prompt = ("Clasifica el último mensaje del cliente. precio: pregunta cuánto cuesta o el total; "
                          "existencia: pregunta si hay o queda; recomendacion: pide opciones según presupuesto; "
                          "continuar: elige bebida, tamaño, extras o modifica una consulta anterior. "
                          "Devuelve solo JSON. No respondas al cliente.")
                recovered = [c.document.text for c in result.candidates[:2]]
                messages = [{"role":"system","content":prompt + "\nReferencias del menú recuperadas:\n" + "\n".join(recovered)},
                    {"role":"user","content":"¿Tienen chocolate frío?"}, {"role":"assistant","content":'{"intent":"existencia"}'},
                    {"role":"user","content":"¿Cuánto vale un americano?"}, {"role":"assistant","content":'{"intent":"precio"}'},
                    {"role":"user","content":"Tengo 90 pesos, ¿qué puedo elegir?"}, {"role":"assistant","content":'{"intent":"recomendacion"}'},
                    {"role":"user","content":"grande con almendras"}, {"role":"assistant","content":'{"intent":"continuar"}'},
                    {"role":"user","content":question}]
                generated = await self.client.generate(messages, seconds=max(.001, seconds-(time.monotonic()-started)),
                    max_tokens=32, response_schema=schema, **({"on_slot":on_slot} if on_slot else {}))
                parsed = Intent.model_validate_json(generated.text)
                if parsed.intent not in schema["properties"]["intent"]["enum"]:
                    raise ValueError("Intención fuera del contrato")
                state = self._merge(delta, previous, text, parsed.intent)
                if purpose is not None:
                    if purpose not in ("stock","quote"):
                        raise ValueError("Consulta no soportada")
                    state = replace(state, purpose=purpose)
                if budget_cents is not None:
                    state = replace(state, budget=budget_cents)
                recognized_restriction = r"\b(sin esa restriccion|sin extras|sin nada|sin extra|sin leche|sin avena|sin almendras|sin espresso|solo uno|solo quiero|solo el|solo un|solo ese|no quiero|no le pongas)\b"
                unresolved_restriction = any(not re.search(recognized_restriction, normalize(r)) for r in result.restrictions)
                if re.fullmatch(r"(no|no[, ]+gracias|no es ese|no es esa)[!. ]*", text) and previous.proposed_family:
                    state = replace(previous, proposed_family=None)
                    reply = self._reply("¿Qué bebida te gustaría consultar?")
                    return TurnResult(reply, "clarification", "stock", time.monotonic()-started, generated, context=state)
                if delta.family is None and delta.proposed_family is None and not (
                    delta.modes or delta.sizes or delta.extras or delta.removed or delta.clear_extras or
                    delta.budget is not None or delta.extra_count is not None or
                    re.search(r"\b(olvida esa restriccion|sin esa restriccion|quita esa restriccion)\b",text) or
                    re.fullmatch(r"(si[, ]*(?:por favor|porfa)?|ese|esa|correcto|correcta|cuanto cuesta|cual seria el total|cuanto seria|y el precio|todavia hay|aun tienen|sigue disponible|sigue en stock)[?¿!¡. ]*",text)):
                    reply = self._reply("Por el momento no tengo información sobre eso. ¿Hay algo más de la cafetería en lo que te pueda ayudar?", "outside_scope")
                    return TurnResult(reply,"outside_scope","stock",time.monotonic()-started,generated,context=previous)
                if delta.unknown == "rechazo":
                    reply = self._reply("¿Qué bebida te gustaría consultar?")
                    return TurnResult(reply, "clarification", "stock", time.monotonic()-started, generated, context=previous)
                if state.unresolved or unresolved_restriction:
                    state = replace(state, unresolved=state.unresolved or "restriccion")
                    reply = self._reply("No tengo esa información confirmada. Nuestro personal puede ayudarte a revisar esa opción.", "missing_information")
                elif state.proposed_family:
                    proposed = self._products(replace(state, family=state.proposed_family, modes=()))
                    names = [p.name for p in proposed]
                    modes = {mode_of(p.id) for p in proposed}
                    if modes == {"hot", "iced"}:
                        label = next(p.name for p in proposed if mode_of(p.id)=="hot")
                        reply = self._reply("¿Te refieres a " + label + "? ¿Lo prefieres caliente o frío?")
                    else:
                        reply = self._reply("¿Te refieres a " + " o ".join(dict.fromkeys(names)) + "?")
                else:
                    reply, selection = self.render(state)
                    if reply.status=="clarification" and state.family and not state.sizes and state.pending is None:
                        state = replace(state, pending="size")
                    return TurnResult(reply,"interpreted","stock" if state.purpose=="stock" else "quote",
                        time.monotonic()-started,generated,selection=selection,context=state)
                return TurnResult(reply,reply.status,state.purpose,time.monotonic()-started,generated,context=state)
        except (ValueError, ClarificationRequired):
            return TurnResult(self._reply("No pude entender esa parte. ¿Me la puedes decir de otra forma?"),
                "clarification","stock",time.monotonic()-started,generated,
                failure="interpretation_unverified",context=previous)
        except (InferenceError, httpx.HTTPError, TimeoutError, RetrievalUnavailable):
            return TurnResult(self.composer.unavailable(model=True),"unavailable","stock",time.monotonic()-started,
                generated,failure="inference_unavailable",context=previous)
        except StorageUnavailable:
            return TurnResult(self.composer.unavailable(),"unavailable","stock",time.monotonic()-started,
                generated,failure="stock_unavailable",context=previous)
