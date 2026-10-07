"""Dobles HTTP del contrato; las mediciones reales viven en reports/benchmark-base."""

import asyncio
import json

import httpx
import pytest

from coffee_house.inference.client import (
    Busy,
    ContextExceeded,
    IncompleteGeneration,
    InferenceError,
    LlamaClient,
)

MESSAGES = [{"role": "user", "content": "Hola"}]


class Stream(httpx.AsyncByteStream):
    def __init__(self, *, delay=0, final=True, limited=False):
        self.delay, self.final, self.limited = delay, final, limited
        self.closed = False

    async def __aiter__(self):
        yield b'data: {"content":"Hola","stop":false}\n\n'
        await asyncio.sleep(self.delay)
        if self.final:
            event = {"content": "", "stop": True, "tokens_predicted": 1,
                     "stop_type": "limit" if self.limited else "eos", "truncated": False}
            yield ("data: " + json.dumps(event) + "\n\n").encode()

    async def aclose(self):
        self.closed = True


def engine(*, tokens=5, stream=None, idle=True, before_headers=0, template_delay=0):
    seen = []
    stream = stream or Stream()

    async def handler(request):
        seen.append(request)
        if request.url.path == "/apply-template":
            await asyncio.sleep(template_delay)
            return httpx.Response(200, json={"prompt": "formatted"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1] * tokens})
        if request.url.path == "/slots":
            return httpx.Response(200, json=[{"id": i, "is_processing": not idle}
                                            for i in range(3)])
        if request.url.path == "/completion":
            await asyncio.sleep(before_headers)
            return httpx.Response(200, stream=stream)
        raise AssertionError(request.url.path)

    return httpx.MockTransport(handler), seen, stream


def test_success_and_generation_controls():
    async def case():
        transport, seen, stream = engine()
        client = LlamaClient("http://127.0.0.1:18085", transport=transport)
        result = await client.generate(MESSAGES)
        await client.close()
        assert result.text == "Hola" and result.input_tokens == 5
        assert result.first_token_seconds is not None
        assert client.free == {0, 1, 2} and stream.closed
        body = json.loads(next(r.content for r in seen if r.url.path == "/completion"))
        assert body["id_slot"] == 0 and body["n_predict"] == 256
        assert body["temperature"] == 0 and not body["cache_prompt"]
        await client.close()
    asyncio.run(case())


def test_no_silent_context_truncation():
    async def case():
        transport, seen, _ = engine(tokens=3841)
        client = LlamaClient("http://127.0.0.1", transport=transport)
        with pytest.raises(ContextExceeded):
            await client.generate(MESSAGES)
        assert not any(r.url.path == "/completion" for r in seen)
        assert len(client.free) == 3
        await client.close()
    asyncio.run(case())


@pytest.mark.parametrize("final,limited", [(False, False), (True, True)])
def test_incomplete_is_not_a_final_answer(final, limited):
    async def case():
        transport, _, _ = engine(stream=Stream(final=final, limited=limited))
        client = LlamaClient("http://127.0.0.1", transport=transport)
        with pytest.raises(IncompleteGeneration):
            await client.generate(MESSAGES)
        await client.close()
        assert len(client.free) == 3
        await client.close()
    asyncio.run(case())


def test_timeout_closes_stream_and_waits_for_physical_idle():
    async def case():
        transport, _, stream = engine(stream=Stream(delay=10), idle=False)
        client = LlamaClient("http://127.0.0.1", transport=transport, cleanup_seconds=.02)
        with pytest.raises(TimeoutError):
            await client.generate(MESSAGES, seconds=.03)
        assert stream.closed and 0 not in client.free
        await client.close()
        assert stream.closed and client.quarantined == {0} and 0 not in client.free
        await client.close()
    asyncio.run(case())


def test_cancel_releases_only_after_idle():
    async def case():
        transport, _, stream = engine(stream=Stream(delay=10))
        client = LlamaClient("http://127.0.0.1", transport=transport)
        task = asyncio.create_task(client.generate(MESSAGES))
        await asyncio.sleep(.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await client.close()
        assert stream.closed and client.free == {0, 1, 2}
        await client.close()
    asyncio.run(case())


def test_uncertain_request_remains_quarantined():
    async def case():
        transport, _, _ = engine(before_headers=10)
        client = LlamaClient("http://127.0.0.1", transport=transport)
        with pytest.raises(TimeoutError):
            await client.generate(MESSAGES, seconds=.01)
        assert client.quarantined == {0} and 0 not in client.free
        await client.close()
    asyncio.run(case())


def test_fourth_request_never_reaches_engine():
    async def case():
        transport, seen, _ = engine(template_delay=10)
        client = LlamaClient("http://127.0.0.1", transport=transport)
        tasks = [asyncio.create_task(client.generate(MESSAGES)) for _ in range(3)]
        await asyncio.sleep(.01)
        with pytest.raises(Busy):
            await client.generate(MESSAGES)
        assert len(seen) == 3
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert client.free == {0, 1, 2}
        await client.close()
    asyncio.run(case())


@pytest.mark.parametrize("url", ["http://192.0.2.1:18085", "https://127.0.0.1",
                                 "http://secret@127.0.0.1", "http://127.0.0.1/path"])
def test_reject_non_private_urls(url):
    with pytest.raises(ValueError):
        LlamaClient(url)


@pytest.mark.parametrize("seconds,tokens", [(121, 256), (0, 256), (120, 257), (120, 0), (120, 1.5)])
def test_generation_limits(seconds, tokens):
    async def case():
        client = LlamaClient("http://127.0.0.1", transport=engine()[0])
        with pytest.raises(ValueError):
            await client.generate(MESSAGES, seconds=seconds, max_tokens=tokens)
        await client.close()
    asyncio.run(case())


@pytest.mark.parametrize("context,busy", [(4096, False), (2048, False), (4096, True)])
def test_server_contract_is_checked(context, busy):
    async def case():
        def handler(request):
            return httpx.Response(200, json=[{"id": i, "n_ctx": context,
                                             "is_processing": busy} for i in range(3)])
        client = LlamaClient("http://127.0.0.1", transport=httpx.MockTransport(handler))
        if context == 4096 and not busy:
            await client.verify_server()
        else:
            with pytest.raises(InferenceError):
                await client.verify_server()
        await client.close()
    asyncio.run(case())


def test_total_deadline_includes_preprocessing():
    async def case():
        transport, seen, _ = engine(template_delay=10)
        client = LlamaClient("http://127.0.0.1", transport=transport)
        with pytest.raises(TimeoutError):
            await client.generate(MESSAGES, seconds=.02)
        assert len(seen) == 1 and client.free == {0, 1, 2}
        await client.close()
    asyncio.run(case())


def test_missing_monitor_slot_never_restores_capacity():
    async def case():
        transport, _, _ = engine()
        original = transport.handler
        async def handler(request):
            if request.url.path == "/slots":
                return httpx.Response(200, json=[])
            return await original(request)
        client = LlamaClient("http://127.0.0.1", transport=httpx.MockTransport(handler))
        await client.generate(MESSAGES)
        await client.close()
        assert client.quarantined == {0} and 0 not in client.free
    asyncio.run(case())
