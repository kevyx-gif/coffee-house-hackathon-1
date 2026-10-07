import asyncio
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from coffee_house.api.app import create_app
from coffee_house.api.service import AdminService
from coffee_house.auth import AuthService
from coffee_house.domain.catalog import load_catalog
from coffee_house.storage.sqlite import AvailabilityStore, Change


class Client:
    def __init__(self):
        self.free={0,1,2}
        self.cleanups=set()
    async def close(self):
        pass


class Flow:
    def __init__(self):
        self.client=Client()


@pytest.fixture
def setup(tmp_path):
    catalog = load_catalog(Path(__file__).resolve().parents[2]/"data/catalog.json")
    store = AvailabilityStore(tmp_path/"stock.sqlite3",catalog)
    store.initialize()
    clock = [0.0]
    auth = AuthService("fixture-admin",PasswordHasher().hash("synthetic-panel-password"),clock=lambda:clock[0])
    admin = AdminService(store,auth)
    app = create_app(admin,Flow(),catalog,cookie_secure=False,origins=("http://localhost",),preview=True,mount_ui=False)
    with TestClient(app,base_url="http://localhost",headers={"Origin":"http://localhost"}) as client:
        yield client,admin,clock


def login(client):
    response = client.post("/api/login",headers={"X-Coffee-Client":"1"},json={"username":"fixture-admin","password":"synthetic-panel-password"})
    assert response.status_code==200
    return {"X-Coffee-CSRF":response.json()["csrf"]}


def body(identifier="hot_latte",value=False,revision=0):
    return {"revision":revision,"changes":[{"kind":"product","identifier":identifier,"available":value}]}


@pytest.mark.parametrize("path",["/api/admin/history","/api/me","/api/admin/operation/fixture-op"])
def test_private_reads_reject_visitors(setup,path):
    client,_,_=setup
    assert client.get(path).status_code==401
    assert client.get("/api/menu").status_code==200


def test_direct_write_without_session_csrf_or_review_is_rejected(setup):
    client,admin,_=setup
    payload={**body(),"review_token":"fake","operation_id":"fixture-op"}
    assert client.post("/api/admin/save",json=payload).status_code==401
    login(client)
    assert client.post("/api/admin/review",json=body()).status_code==401
    assert client.post("/api/admin/save",headers={"X-Coffee-CSRF":"wrong"},json=payload).status_code==401
    assert admin.store.snapshot().revision==0 and admin.store.history()==()


def test_review_save_events_and_idempotent_retry(setup):
    client,admin,_=setup
    headers=login(client)
    token=client.post("/api/admin/review",headers=headers,json=body()).json()["review_token"]
    assert admin.store.snapshot().products["hot_latte"]
    response=client.post("/api/admin/save",headers=headers,json={**body(),"review_token":token,"operation_id":"fixture-op"})
    assert response.status_code==200 and response.json()["changed"]==1
    assert admin.events.revision==1
    repeat=client.post("/api/admin/save",headers=headers,json={**body(),"review_token":token,"operation_id":"fixture-op"})
    assert repeat.json()==response.json() and len(admin.store.history())==1
    assert client.get("/api/admin/operation/fixture-op").json()["result"]["revision"]==1
    assert client.get("/api/admin/history").json()["history"][0]["administrator"]=="fixture-admin"


def test_conflict_between_review_and_save_preserves_existing_changes(setup):
    client,admin,_=setup
    headers=login(client)
    token=client.post("/api/admin/review",headers=headers,json=body()).json()["review_token"]
    admin.store.save("other","another-admin",0,[Change("product","hot_cappuccino",False)])
    response=client.post("/api/admin/save",headers=headers,json={**body(),"review_token":token,"operation_id":"fixture-op"})
    assert response.status_code==409
    snapshot=admin.store.snapshot()
    assert snapshot.products["hot_latte"] and not snapshot.products["hot_cappuccino"]
    assert len(admin.store.history())==1


def test_expiration_not_extended_by_reads_or_heartbeat(setup):
    client,admin,clock=setup
    headers=login(client)
    clock[0]=1799
    assert client.get("/api/me").status_code==200
    assert client.get("/api/admin/history").status_code==200
    clock[0]=1800
    assert client.post("/api/admin/review",headers=headers,json=body()).status_code==401
    assert admin.store.snapshot().revision==0


def test_explicit_human_activity_extends_session(setup):
    client,_,clock=setup
    headers=login(client)
    clock[0]=1799
    assert client.post("/api/touch",headers=headers).status_code==200
    clock[0]=1801
    assert client.get("/api/me").status_code==200
    assert client.post("/api/logout",headers=headers).status_code==200
    assert client.get("/api/admin/history").status_code==401


def test_cross_origin_and_unverified_login_rejected(setup):
    client,_,_=setup
    assert client.post("/api/login",json={"username":"fixture-admin","password":"synthetic-panel-password"}).status_code==403
    assert client.post("/api/login",headers={"Origin":"https://other.example","X-Coffee-Client":"1"},json={"username":"fixture-admin","password":"synthetic-panel-password"}).status_code==403


def test_login_throttled_and_unicode_does_not_raise(setup):
    client,_,_=setup
    for _ in range(6):
        response=client.post("/api/login",headers={"X-Coffee-Client":"1"},json={"username":"fíxture","password":"wrong"})
        assert response.status_code==401
    assert "Espera" in response.json()["message"]


def test_unreviewed_modified_payload_rejected(setup):
    client,admin,_=setup
    headers=login(client)
    token=client.post("/api/admin/review",headers=headers,json=body()).json()["review_token"]
    changed={**body("hot_cappuccino"),"review_token":token,"operation_id":"fixture-op"}
    assert client.post("/api/admin/save",headers=headers,json=changed).status_code==400
    assert admin.store.snapshot().revision==0


def test_lost_database_no_event_or_success(setup):
    client,admin,_=setup
    headers=login(client)
    token=client.post("/api/admin/review",headers=headers,json=body()).json()["review_token"]
    admin.store.path.unlink()
    assert client.post("/api/admin/save",headers=headers,json={**body(),"review_token":token,"operation_id":"fixture-op"}).status_code==503
    assert admin.events.revision==0


def test_sse_subscription_covers_commit_after_subscribe(setup):
    _,admin,_=setup
    async def run():
        stream=admin.events.stream()
        first=await anext(stream)
        assert first=={"event":"stock","data":"0"}
        admin.events.publish(1)
        assert await anext(stream)=={"event":"stock","data":"1"}
        await stream.aclose()
        assert not admin.events.listeners
    asyncio.run(run())


def test_cookie_flags_default_secure(setup):
    _,admin,_=setup
    app=create_app(admin,Flow(),admin.store.catalog,origins=("https://localhost",),mount_ui=False)
    with TestClient(app,base_url="https://localhost",headers={"Origin":"https://localhost"}) as client:
        response=client.post("/api/login",headers={"X-Coffee-Client":"1"},json={"username":"fixture-admin","password":"synthetic-panel-password"})
        cookie=response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie



def test_reviews_in_two_tabs_and_replay_never_rolls_events_back(setup):
    client,admin,_=setup
    headers=login(client)
    first=client.post("/api/admin/review",headers=headers,json=body()).json()["review_token"]
    second_body=body("hot_cappuccino")
    second=client.post("/api/admin/review",headers=headers,json=second_body).json()["review_token"]
    first_payload={**body(),"review_token":first,"operation_id":"first-op"}
    assert client.post("/api/admin/save",headers=headers,json=first_payload).status_code==200
    assert client.post("/api/admin/save",headers=headers,json={**second_body,"review_token":second,"operation_id":"second-op"}).status_code==409
    token=client.post("/api/admin/review",headers=headers,json=body("hot_cappuccino",revision=1)).json()["review_token"]
    assert client.post("/api/admin/save",headers=headers,json={**body("hot_cappuccino",revision=1),"review_token":token,"operation_id":"second-op"}).status_code==200
    assert admin.events.revision==2
    assert client.post("/api/admin/save",headers=headers,json=first_payload).status_code==200
    assert admin.events.revision==2 and len(admin.store.history())==2


def test_unknown_fields_and_non_boolean_values_cannot_edit_catalog(setup):
    client,admin,_=setup
    headers=login(client)
    payload=body();payload["changes"][0]["price_cents"]=1
    assert client.post("/api/admin/review",headers=headers,json=payload).status_code==400
    payload=body();payload["changes"][0]["available"]="false"
    assert client.post("/api/admin/review",headers=headers,json=payload).status_code==400
    assert admin.store.snapshot().revision==0
