"""Recorrido HTTP/cola/contexto con inferencia de prueba; no acredita calidad Llama."""
import asyncio
import json
from pathlib import Path

import numpy as np
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from coffee_house.api.app import create_app
from coffee_house.api.service import AdminService
from coffee_house.auth import AuthService
from coffee_house.conversation.semantic import SemanticConversationFlow
from coffee_house.domain.catalog import load_catalog
from coffee_house.inference.client import GeneratedText
from coffee_house.retrieval.queries import GroundedQueries
from coffee_house.retrieval.search import HybridIndex, build_documents
from coffee_house.storage.sqlite import AvailabilityStore


class Encoder:
    def encode(self, texts):
        return np.ones((len(texts), 3), dtype=np.float32)


class Client:
    def __init__(self):
        self.free = {0, 1, 2}
        self.cleanups = set()

    async def close(self):
        pass

    async def generate(self, messages, on_slot=None, **kwargs):
        slot = min(self.free)
        self.free.remove(slot)
        if on_slot:
            on_slot(slot)
        try:
            await asyncio.sleep(0)
            return GeneratedText(json.dumps({"intent":"continuar"}), 1, 1, .01, .001)
        finally:
            self.free.add(slot)


@pytest.fixture
def web(tmp_path):
    root = Path(__file__).resolve().parents[2]
    catalog = load_catalog(root / "data/catalog.json")
    store = AvailabilityStore(tmp_path / "stock.sqlite3", catalog)
    store.initialize()
    index = HybridIndex(build_documents(catalog, root / "data/knowledge/limites.md"), Encoder())
    flow = SemanticConversationFlow(GroundedQueries(catalog, index, store), Client())
    auth = AuthService("fixture", PasswordHasher().hash("synthetic-password"))
    app = create_app(AdminService(store, auth), flow, catalog, cookie_secure=False, preview=True,
                     origins=("http://localhost",), mount_ui=False)
    with TestClient(app, base_url="http://localhost", headers={"Origin":"http://localhost", "X-Coffee-Client":"1"}) as client:
        yield client


def session(client):
    return client.post("/api/chat/session").json()["session_id"]


def ask(client, sid, question):
    response = client.post("/api/chat/query", json={"session_id":sid,"question":question})
    assert response.status_code == 200
    ticket = response.json()["ticket"]
    for _ in range(200):
        value = client.get(f"/api/chat/{sid}/{ticket}").json()
        if value["status"] == "done" and not value["held"]:
            break
    assert value["status"] == "done" and not value["held"]
    return ticket, value


def test_partial_conversation_survives_http_queue_and_polls(web):
    sid = session(web)
    _, value = ask(web, sid, "quiero un latte")
    assert "caliente" in value["text"]
    _, value = ask(web, sid, "caliente grande con avena")
    assert "$95.00 MXN" in value["text"]
    _, value = ask(web, sid, "¿y con un espresso extra?")
    assert "$110.00 MXN" in value["text"]
    assert "intent" not in value and "context" not in value


def test_old_poll_cannot_revert_latest_preferences(web):
    sid = session(web)
    old, _ = ask(web, sid, "latte caliente mediano con avena")
    _, value = ask(web, sid, "grande")
    assert "$95.00 MXN" in value["text"]
    web.get(f"/api/chat/{sid}/{old}")
    _, value = ask(web, sid, "¿y con un espresso extra?")
    assert "$110.00 MXN" in value["text"]


def test_new_session_does_not_inherit_another_customer_drink(web):
    a, b = session(web), session(web)
    ask(web, a, "latte caliente grande con avena")
    _, value = ask(web, b, "grande con espresso extra")
    assert "$" not in value["text"]
    _, value = ask(web, a, "¿y con un espresso extra?")
    assert "$110.00 MXN" in value["text"]


def test_repeated_stock_question_after_authenticated_save_explains_change(web):
    sid = session(web)
    _, initial = ask(web, sid, "¿Hay Iced Latte?")
    assert "todavía tenemos" in initial["text"]
    login = web.post("/api/login", json={"username":"fixture", "password":"synthetic-password"})
    csrf = {"X-Coffee-CSRF":login.json()["csrf"]}
    body = {"revision":0,"changes":[{"kind":"product", "identifier":"iced_latte", "available":False}]}
    review = web.post("/api/admin/review", json=body, headers=csrf)
    saved = web.post("/api/admin/save", json={**body, "review_token":review.json()["review_token"],
        "operation_id":"synthetic-repeat-stock"}, headers=csrf)
    assert saved.status_code == 200
    _, value = ask(web, sid, "¿Sigue en stock?")
    assert "Disculpa" in value["text"] and "Se nos terminó Iced Latte" in value["text"]
    _, fresh = ask(web, session(web), "¿Hay Iced Latte?")
    assert "cuando preguntaste" not in fresh["text"]
