from pathlib import Path
from types import SimpleNamespace

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from coffee_house.api.app import create_app
from coffee_house.api.service import AdminService
from coffee_house.auth import AuthService
from coffee_house.conversation import TurnResult
from coffee_house.domain.catalog import load_catalog
from coffee_house.responses import CustomerReply
from coffee_house.storage.sqlite import AvailabilityStore


class Flow:
    def __init__(self):
        self.client=SimpleNamespace(free={0,1,2},cleanups=set(),close=self.close)
        self.calls=[]
    async def close(self): pass
    async def answer(self,question,**kwargs):
        self.calls.append((question,kwargs))
        return TurnResult(CustomerReply('Respuesta verificada','available'),'selected','stock',0,
                          inference=SimpleNamespace(text='BORRADOR PRIVADO QUE NO DEBE PUBLICARSE'))

@pytest.fixture
def setup(tmp_path):
    catalog=load_catalog(Path(__file__).resolve().parents[2]/'data/catalog.json')
    store=AvailabilityStore(tmp_path/'stock.sqlite3',catalog);store.initialize()
    flow=Flow()
    auth=AuthService('fixture',PasswordHasher().hash('synthetic-password'))
    app=create_app(AdminService(store,auth),flow,catalog,cookie_secure=False,preview=True,origins=('http://localhost',),mount_ui=False)
    with TestClient(app,base_url='http://localhost',headers={'Origin':'http://localhost','X-Coffee-Client':'1'}) as client:
        yield client,flow

def start(client):
    return client.post('/api/chat/session').json()['session_id']

def test_isolated_read_only_sessions_and_private_draft_never_exposed(setup):
    client,flow=setup
    a,b=start(client),start(client)
    job=client.post('/api/chat/query',json={'session_id':a,'question':'Hola'}).json()['ticket']
    for _ in range(100):
        response=client.get(f'/api/chat/{a}/{job}')
        if response.json()['status']=='done':break
    assert response.json()['text']=='Respuesta verificada'
    assert 'BORRADOR' not in response.text
    assert client.get(f'/api/chat/{b}/{job}').status_code==409
    assert client.post(f'/api/chat/{b}/{job}/cancel').status_code==409
    assert client.get('/api/admin/history').status_code==401
    assert len(flow.calls)==1

def test_old_transport_request_is_not_executed(setup):
    client,flow=setup
    response=client.post('/api/chat/query',json={'session_id':start(client),'question':'Hola','submitted_at':1})
    assert response.status_code==409
    assert not flow.calls

def test_preferences_do_not_authorize_actions_or_unknown_fields(setup):
    client,flow=setup
    session=start(client)
    response=client.post('/api/chat/query',json={'session_id':session,'question':'Hola','available':True})
    assert response.status_code==400 and not flow.calls
    assert client.post('/api/chat/session',headers={'Origin':'https://foreign.invalid'}).status_code==403
