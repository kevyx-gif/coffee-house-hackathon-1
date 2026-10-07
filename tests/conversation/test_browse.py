import asyncio

import pytest

from coffee_house.conversation.flow import ConversationFlow
from coffee_house.storage.sqlite import Change, StorageUnavailable


class NoGeneration:
    async def generate(self,*args,**kwargs):
        raise AssertionError("Una lista de carta no requiere inferencia")


@pytest.mark.parametrize("question", ["que tipos de latte tiene", "¿Qué variedades de lattes tienen?", "Muestrame opciones de latte"])
def test_lists_real_lattes_without_asking_for_product_first(setup,question):
    flow=ConversationFlow(setup[-1],NoGeneration())
    value=asyncio.run(flow.answer(question))
    assert value.reply.status == "catalog_options"
    assert "Calientes:" in value.reply.text and "Fríos:" in value.reply.text
    assert "Taro Latte" in value.reply.text and "Chai Latte" in value.reply.text
    assert "Chocolate Blanco" in value.reply.text  # Pertenece a la categoría Lattes.
    assert "Agotados por ahora: Iced Oreo Latte" in value.reply.text
    assert "modalidad" not in value.reply.text and value.inference is None
    assert value.reply.sources


def test_list_is_revalidated_after_stock_changes(setup):
    store=setup[-2];flow=ConversationFlow(setup[-1],NoGeneration())
    value=asyncio.run(flow.answer("que tipos de latte tienen"))
    store.save("browse-recheck","test-admin",0,[Change("product","hot_taro_latte",False)])
    updated=flow.revalidate(value)
    assert "Taro Latte" in updated.text.split("Agotados por ahora:")[1]
    assert updated.stock_revision == 1


def test_listing_does_not_claim_live_stock_when_storage_fails(setup,monkeypatch):
    flow=ConversationFlow(setup[-1],NoGeneration())
    def fail(): raise StorageUnavailable("offline")
    monkeypatch.setattr(setup[-2],"snapshot",fail)
    value=asyncio.run(flow.answer("que tipos de latte tienen"))
    assert value.failure == "facts_unavailable"
    assert "tenemos estas opciones" not in value.reply.text
