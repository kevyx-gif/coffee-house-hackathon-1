import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

from argon2 import PasswordHasher

from coffee_house.api.app import create_app
from coffee_house.api.service import COOKIE, AdminService
from coffee_house.auth import AuthService
from coffee_house.domain.catalog import load_catalog
from coffee_house.storage.sqlite import AvailabilityStore, Change
from coffee_house.ui.web import delta, table_for


class Client:
    def __init__(self):
        self.free={0,1,2};self.cleanups=set()
    async def close(self):
        pass


class Flow:
    def __init__(self):self.client=Client()


def build(tmp_path):
    catalog=load_catalog(Path(__file__).resolve().parents[2]/"data/catalog.json")
    store=AvailabilityStore(tmp_path/"stock.sqlite3",catalog);store.initialize()
    clock=[0.0]
    auth=AuthService("fixture-admin",PasswordHasher().hash("synthetic-panel-password"),clock=lambda:clock[0])
    admin=AdminService(store,auth)
    async def ui_app():
        return create_app(admin,Flow(),catalog,cookie_secure=False,preview=True,origins=("http://localhost",))
    app=asyncio.run(ui_app())
    sid,session=auth.login("fixture-admin","synthetic-panel-password","fixture-ip")
    request=SimpleNamespace(cookies={COOKIE:sid})
    base=table_for(catalog,store.snapshot());rows=copy.deepcopy(base)
    next(row for row in rows if row[1]=="hot_latte")[3]=False
    return app,admin,clock,session,request,base,rows


def test_expired_save_keeps_draft_and_login_requires_new_review(tmp_path):
    app,admin,clock,session,request,base,rows=build(tmp_path)
    callbacks=app.state.ui_callbacks
    reviewed=callbacks["review_admin"](rows,base,0,session.csrf,request)
    clock[0]=1800
    result=asyncio.run(callbacks["save_admin"](rows,base,0,session.csrf,reviewed[3],request))
    assert result[0]==rows and "Borrador" in result[-1] and admin.store.snapshot().revision==0
    sid,new_session=admin.auth.login("fixture-admin","synthetic-panel-password","fixture-ip")
    new_request=SimpleNamespace(cookies={COOKIE:sid})
    loaded=callbacks["load_admin"]("","",new_session.csrf,rows,base,reviewed[3],new_request)
    assert loaded[3]==rows and loaded[6] is None
    assert delta(loaded[3],loaded[4],admin.store.catalog)==[Change("product","hot_latte",False)]


def test_conflict_merges_only_explicit_draft_without_overwriting_other_tab(tmp_path):
    app,admin,_,session,request,base,rows=build(tmp_path)
    admin.store.save("another-tab","fixture-admin",0,[Change("product","hot_cappuccino",False)])
    result=app.state.ui_callbacks["review_admin"](rows,base,0,session.csrf,request)
    assert result[2]==1 and result[3] is None
    assert next(row for row in result[0] if row[1]=="hot_latte")[3] is False
    assert next(row for row in result[0] if row[1]=="hot_cappuccino")[3] is False
    assert delta(result[0],result[1],admin.store.catalog)==[Change("product","hot_latte",False)]
    assert admin.store.snapshot().products["hot_latte"]


def test_gradio_file_route_cannot_read_state_or_auth_files(tmp_path):
    from fastapi.testclient import TestClient
    app,_,_,_,_,_,_=build(tmp_path)
    private=tmp_path/"private-auth.json";private.write_text("synthetic-private-marker")
    with TestClient(app,base_url="http://localhost") as client:
        response=client.get("/gradio_api/file="+str(private.absolute()).replace("\\","/"))
        assert response.status_code in {403,404} and "synthetic-private-marker" not in response.text


def test_admin_table_rejects_new_rows_and_false_bool_strings(tmp_path):
    _,admin,_,_,_,base,rows=build(tmp_path)
    import pytest
    rows.append(["product","new","Inventado",True])
    with pytest.raises(ValueError):delta(rows,base,admin.store.catalog)
    rows=copy.deepcopy(base);rows[0][3]="false"
    with pytest.raises(ValueError):delta(rows,base,admin.store.catalog)
