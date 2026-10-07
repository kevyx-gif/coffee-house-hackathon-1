"""API de lectura para conversaciones efímeras, sin texto libre del modelo expuesto."""
import secrets
import time
from dataclasses import replace

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from coffee_house.domain.pricing import Selection, calculate_quote, parse_mxn
from coffee_house.storage.sqlite import StorageUnavailable
from coffee_house.ui.queue import AdmissionError


class Preference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str = Field(max_length=128)
    size: str | None = Field(default=None,max_length=128)
    extras: list[str] = Field(default_factory=list,max_length=3)


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=20,max_length=128)
    question: str = Field(min_length=1,max_length=2000)
    preference: Preference | None = None
    budget: str = Field(default="",max_length=32)
    submitted_at: StrictInt | None = None
    compare: list[str] = Field(default_factory=list,max_length=5)


def attach_chat(app,admin,flow,queue,catalog):
    sessions = {}
    app.state.chat_sessions = sessions
    def session(identifier):
        current=time.monotonic()
        for key,value in tuple(sessions.items()):
            previous=queue.tickets.get(queue.by_session.get(key))
            if current-value["seen"]>900 and not (previous and previous.held):
                sessions.pop(key)
        if identifier not in sessions:
            raise AdmissionError("La conversación se reinició. Tu texto no se reenviará automáticamente")
        sessions[identifier]["seen"]=current
        return sessions[identifier]
    @app.exception_handler(AdmissionError)
    async def admission(request,error):
        return JSONResponse({"message":str(error)},409)
    @app.post("/api/chat/session")
    async def new_session(request: Request):
        if request.headers.get("x-coffee-client")!="1":
            return JSONResponse({"message":"Solicitud no verificada"},403)
        queue.prune()
        # Limitar sesiones abandonadas sin almacenar conversaciones en disco.
        current=time.monotonic()
        for key,value in tuple(sessions.items()):
            previous=queue.tickets.get(queue.by_session.get(key))
            if current-value["seen"]>900 and not (previous and previous.held):sessions.pop(key)
        if len(sessions)>=1000:
            raise AdmissionError("Inténtalo en un rato; el menú sigue disponible")
        identifier=secrets.token_urlsafe(32)
        sessions[identifier]={"confirmed":None,"seen":current}
        return {"session_id":identifier}
    @app.post("/api/chat/query")
    async def submit(body: Query):
        context=session(body.session_id)
        admin.store.snapshot()
        preference=Selection(body.preference.product_id,body.preference.size,tuple(body.preference.extras)) if body.preference else context["confirmed"]
        candidates=()
        budget=None
        if body.budget.strip():
            if preference is None:
                raise AdmissionError("Elige una bebida y un tamaño para comparar tu presupuesto")
            try:
                budget=parse_mxn(body.budget)
                calculate_quote(catalog,preference)
            except ValueError as error:
                raise AdmissionError(str(error)) from error
            choices=[]
            for identifier in dict.fromkeys([preference.product_id,*body.compare]):
                if identifier not in catalog.products:
                    raise AdmissionError("No tengo información de esa bebida; consulta al personal")
                for variant in catalog.products[identifier].variants:
                    if variant.recommendation_allowed:
                        choice=Selection(identifier,variant.size,preference.extras)
                        try:calculate_quote(catalog,choice)
                        except ValueError as error:raise AdmissionError(str(error)) from error
                        choices.append(choice)
            candidates=tuple(choices)
        ticket=queue.submit(body.session_id,body.question,preference,candidates=candidates,budget_cents=budget,submitted_at=body.submitted_at)
        return {"ticket":ticket.id,"status":ticket.status}
    @app.get("/api/chat/{session_id}/{ticket_id}")
    async def result(session_id: str,ticket_id: str):
        context=session(session_id)
        ticket=queue.get(session_id,ticket_id)
        response={"status":ticket.status,"held":ticket.held,"text":None,"sources":[]}
        if ticket.result is not None and ticket.status=="done":
            value=ticket.result
            reply=value.reply
            if hasattr(flow,"revalidate"):
                reply=flow.revalidate(value)
            if value.selection is not None and reply.stock_revision is not None:
                try:
                    current=admin.store.snapshot()
                    if current.revision != reply.stock_revision:
                        updated=flow.composer.quote(value.selection,candidates=ticket.candidates,budget_cents=ticket.budget_cents) if value.purpose=="quote" else flow.composer.stock(value.selection)
                        if reply.status=="available" and updated.status=="out":updated=replace(updated,text="Disculpa, cambió la disponibilidad. "+updated.text)
                        reply=updated
                except StorageUnavailable:
                    reply=flow.composer.unavailable()
            response.update(text=reply.text,sources=list(reply.source_references))
            # Un poll tardío de una consulta anterior no puede revertir la conversación.
            if queue.by_session.get(session_id)==ticket_id:
                if value.context is not None:context["confirmed"]=value.context
                elif value.selection is not None:context["confirmed"]=value.selection
        return response
    @app.post("/api/chat/{session_id}/{ticket_id}/cancel")
    async def cancel(session_id: str,ticket_id: str):
        session(session_id)
        ticket=queue.cancel(session_id,ticket_id)
        return {"status":ticket.status,"held":ticket.held}
