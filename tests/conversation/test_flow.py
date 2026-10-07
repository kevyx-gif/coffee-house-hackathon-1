import asyncio

import pytest

from coffee_house.conversation import ConversationFlow
from coffee_house.domain.pricing import Selection
from coffee_house.inference.client import GeneratedText, InferenceError
from coffee_house.storage.sqlite import Change


class DraftClient:
    def __init__(self, hook=None):
        self.messages = []
        self.hook = hook

    async def generate(self, messages, **limits):
        self.messages.append(messages)
        if self.hook:
            await self.hook()
        return GeneratedText("Sí hay de todo, cuesta $999 y ya compré tu pedido", 90, 20, .01, .001)


@pytest.mark.parametrize("question,status,fragment", [
    ("¿Todavía tienen latte caliente?", "available", "todavía tenemos"),
    ("¿Tienen iced oreo latte?", "out", "Se nos terminó"),
    ("¿Cuánto cuesta latte caliente mediano?", "available", "$70.00 MXN"),
    ("¿Cuánto cuesta latte caliente?", "clarification", "tamaño"),
    ("¿Tienen latte?", "clarification", "modalidad"),
    ("ola", "greeting", "¡Hola!"),
    ("¿Tienen wifi?", "missing_information", "personal"),
    ("latte caliente mediano con avena", "clarification", "Confirma"),
])
def test_complete_contract_never_publishes_draft(setup, question, status, fragment):
    _, _, _, _, queries = setup
    client = DraftClient()
    result = asyncio.run(ConversationFlow(queries, client).answer(question))
    assert result.reply.status == status and fragment in result.reply.text
    assert result.inference and len(client.messages) == 1
    assert "$999" not in result.reply.text and "compré" not in result.reply.text
    assert client.messages[0][-1]["content"] == question


def test_stock_changes_while_model_generates(setup):
    _, _, _, store, queries = setup
    async def change():
        store.save("generation", "test-admin", 0, [Change("product", "hot_latte", False)])
    result = asyncio.run(ConversationFlow(queries, DraftClient(change)).answer("¿Tienen latte caliente?"))
    assert result.reply.status == "out" and "Disculpa" in result.reply.text
    assert result.reply.stock_revision == 1


def test_confirmed_extras_and_centavos_not_assumed(setup):
    _, _, _, _, queries = setup
    confirmed = Selection("hot_latte", "mediano", ("espresso_extra",))
    result = asyncio.run(ConversationFlow(queries, DraftClient()).answer("¿Cuál sería el total?", confirmed=confirmed))
    assert "$85.00 MXN" in result.reply.text
    assert result.reply.status == "available"


def test_new_product_does_not_inherit_old_selection(setup):
    _, _, _, _, queries = setup
    result = asyncio.run(ConversationFlow(queries, DraftClient()).answer("¿Tienen iced oreo latte?",
        confirmed=Selection("hot_latte", "mediano", ())))
    assert result.reply.status == "out" and "Oreo" in result.reply.text


def test_model_failure_reports_unavailable(setup):
    _, _, _, _, queries = setup
    async def broken():
        raise InferenceError("test failure")
    result = asyncio.run(ConversationFlow(queries, DraftClient(broken)).answer("¿Tienen latte caliente?"))
    assert result.reply.status == "model_unavailable" and result.failure
    assert "todavía tenemos" not in result.reply.text


def test_database_lost_during_generation(setup):
    _, _, _, store, queries = setup
    async def drop():
        store.path.unlink()
    result = asyncio.run(ConversationFlow(queries, DraftClient(drop)).answer("¿Cuánto cuesta latte caliente mediano?"))
    assert result.reply.status == "stock_unavailable" and "$70.00 MXN" in result.reply.text


def test_total_deadline_and_cancellation(setup):
    _, _, _, _, queries = setup
    async def slow():
        await asyncio.sleep(10)
    async def run():
        flow = ConversationFlow(queries, DraftClient(slow))
        result = await flow.answer("¿Tienen latte caliente?", seconds=.05)
        assert result.reply.status == "model_unavailable"
        task = asyncio.create_task(flow.answer("¿Tienen latte caliente?"))
        await asyncio.sleep(.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())


def test_unrelated_typo_never_adopts_prior_drink(setup):
    _, _, _, _, queries = setup
    result = asyncio.run(ConversationFlow(queries, DraftClient()).answer("capuccinno iced",
        confirmed=Selection("hot_latte", "mediano", ())))
    assert result.reply.status == "clarification" and "$70" not in result.reply.text


def test_missing_confirmed_size_is_question(setup):
    _, _, _, _, queries = setup
    result = asyncio.run(ConversationFlow(queries, DraftClient()).answer("¿Cuánto cuesta?",
        confirmed=Selection("hot_latte", None, ())))
    assert result.reply.status == "clarification" and "tamaño" in result.reply.text


def test_sync_composition_cannot_publish_after_deadline(setup):
    _, _, _, _, queries = setup
    flow = ConversationFlow(queries, DraftClient())
    original = flow.composer.stock
    def slow(*args, **kwargs):
        import time
        time.sleep(.1)
        return original(*args, **kwargs)
    flow.composer.stock = slow
    result = asyncio.run(flow.answer("¿Tienen latte caliente?", seconds=.05))
    assert result.reply.status == "model_unavailable" and "todavía tenemos" not in result.reply.text


@pytest.mark.parametrize("question,total", [("¿Cuánto cuesta latte caliente?", "$85.00 MXN"),
                                           ("¿Cuánto cuesta latte caliente grande?", "$95.00 MXN")])
def test_same_product_keeps_known_size_and_extras(setup,question,total):
    _,_,_,_,queries=setup
    confirmed=Selection("hot_latte","mediano",("espresso_extra",))
    result=asyncio.run(ConversationFlow(queries,DraftClient()).answer(question,confirmed=confirmed))
    assert total in result.reply.text and result.selection.extras==confirmed.extras


def test_stock_followup_does_not_erase_known_selection(setup):
    _,_,_,_,queries=setup
    confirmed=Selection("hot_latte","mediano",("espresso_extra",))
    result=asyncio.run(ConversationFlow(queries,DraftClient()).answer("¿Todavía hay latte caliente?",confirmed=confirmed))
    assert result.selection==confirmed and result.reply.status=="available"



def test_conflicting_sizes_are_not_resolved_from_prior_choice(setup):
    _,_,_,_,queries=setup
    result=asyncio.run(ConversationFlow(queries,DraftClient()).answer("latte caliente mediano grande",
        confirmed=Selection("hot_latte","mediano",())))
    assert result.reply.status=="clarification" and "$70" not in result.reply.text


def test_followup_with_new_preferences_asks_confirmation_instead_of_unknown(setup):
    _, _, _, _, queries = setup
    result = asyncio.run(ConversationFlow(queries, DraftClient()).answer(
        "¿Y grande con shot extra?", confirmed=Selection("hot_latte", "mediano", ())))
    assert result.reply.status == "clarification"
    assert "¿Te refieres a Latte?" in result.reply.text
    assert result.selection is None
