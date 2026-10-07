import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from coffee_house.auth import AuthError
from coffee_house.storage.sqlite import (
    Change,
    OperationConflict,
    RevisionConflict,
    StorageUnavailable,
)
from coffee_house.ui.menu import render_menu

from .service import COOKIE


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class Delta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["product", "extra"]
    identifier: str = Field(max_length=128)
    available: StrictBool


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: StrictInt
    changes: list[Delta] = Field(min_length=1, max_length=128)


class Save(Review):
    review_token: str = Field(max_length=128)
    operation_id: str = Field(min_length=1, max_length=128)


def create_app(admin, flow, catalog, *, cookie_secure=True, origins=(), preview=False, mount_ui=True, root_path=""):
    if not cookie_secure and not preview:
        raise ValueError("Cookie sin Secure solo en preview loopback")
    from coffee_house.ui.queue import QueryQueue
    queue = QueryQueue(flow)
    @asynccontextmanager
    async def lifespan(app):
        yield
        await queue.close()
        await flow.client.close()
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.admin, app.state.queue = admin, queue
    if preview:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])
    @app.middleware("http")
    async def safety(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin not in origins:
                return JSONResponse({"message": "Solicitud de otro origen rechazada"}, 403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response
    @app.exception_handler(AuthError)
    async def auth_error(request, error):
        return JSONResponse({"message": str(error)}, 401)
    @app.exception_handler(RevisionConflict)
    async def conflict(request, error):
        return JSONResponse({"message": "La disponibilidad cambió. Revisa el borrador nuevamente", "revision": error.current_revision}, 409)
    @app.exception_handler(StorageUnavailable)
    async def lost(request, error):
        return JSONResponse({"message": "Disponibilidad sin actualizar; conserva tu borrador"}, 503)
    @app.exception_handler(ValueError)
    async def bad(request, error):
        return JSONResponse({"message": "No se confirmó la operación. Revisa tu borrador"}, 400)
    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        return JSONResponse({"message": "Solicitud inválida"}, 400)
    def sid(request):
        return request.cookies.get(COOKIE)
    def csrf(request):
        return request.headers.get("x-coffee-csrf")
    @app.get("/api/health")
    async def health():
        return {"status": "ready", "private_preview": preview,"active_queries":len(queue.running),"waiting":len(queue.waiting),"free_slots":len(flow.client.free),"stock_connections":len(admin.events.listeners)}
    @app.get("/api/menu")
    async def menu():
        stock = await asyncio.to_thread(admin.store.snapshot)
        return {"revision": stock.revision,"server_time":int(time.time()*1000), "simulated": True, "html": render_menu(catalog, stock)}
    @app.get("/api/stock-worker.js")
    async def stock_worker():
        text=(Path(__file__).resolve().parents[1]/"ui/stock-worker.js").read_text(encoding="utf-8")
        return Response(text,media_type="text/javascript")
    @app.get("/api/events")
    async def events():
        return EventSourceResponse(admin.events.stream(), ping=5)
    @app.post("/api/login")
    async def login(body: Login, request: Request):
        if request.headers.get("x-coffee-client") != "1":
            return JSONResponse({"message": "Solicitud no verificada"}, 403)
        address = request.client.host if request.client else "unknown"
        token, session = await asyncio.to_thread(admin.auth.login, body.username, body.password, address)
        response = JSONResponse({"message": "Sesión iniciada", "csrf": session.csrf})
        response.set_cookie(COOKIE, token, httponly=True, secure=cookie_secure, samesite="strict", path=root_path or "/")
        return response
    @app.get("/api/me")
    async def me(request: Request):
        session = admin.auth.require(sid(request))
        return {"username": session.username, "csrf": session.csrf}
    @app.post("/api/touch")
    async def touch(request: Request):
        admin.auth.require(sid(request), csrf(request), write=True, human=True)
        return {"ok": True}
    @app.post("/api/logout")
    async def logout(request: Request):
        admin.auth.logout(sid(request), csrf(request))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path=root_path or "/", secure=cookie_secure, httponly=True, samesite="strict")
        return response
    @app.post("/api/admin/review")
    async def review(body: Review, request: Request):
        token = admin.review(sid(request), csrf(request), body.revision, [Change(**item.model_dump()) for item in body.changes])
        return {"review_token": token, "message": "Revisión lista; pulsa Guardar para confirmar"}
    @app.post("/api/admin/save")
    async def save(body: Save, request: Request):
        try:
            result = await admin.save(sid(request), csrf(request), body.review_token, body.revision,
                [Change(**item.model_dump()) for item in body.changes], body.operation_id)
        except OperationConflict:
            return JSONResponse({"message": "La operación corresponde a otro guardado; conserva el borrador"}, 409)
        return asdict(result)
    @app.get("/api/admin/history")
    async def history(request: Request, after_sequence: int = 0, limit: int = 100):
        return {"history": [asdict(item) for item in admin.history(sid(request), after_sequence=after_sequence, limit=limit)]}
    @app.get("/api/admin/operation/{operation_id}")
    async def operation(operation_id: str, request: Request):
        session = admin.auth.require(sid(request))
        result = admin.store.operation(operation_id, session.username)
        return {"result": asdict(result) if result else None}
    from .chat import attach_chat
    attach_chat(app,admin,flow,queue,catalog)
    if mount_ui:
        from coffee_house.ui.web import mount
        app = mount(app, admin, flow, queue, catalog, root_path=root_path)
    return app
