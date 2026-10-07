"""Streaming acotado y posiciones retenidas hasta comprobar cancelación física."""

import asyncio
import json
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx


class InferenceError(RuntimeError):
    pass


class Busy(InferenceError):
    pass


class ContextExceeded(InferenceError):
    pass


class IncompleteGeneration(InferenceError):
    pass


@dataclass(frozen=True)
class GeneratedText:
    text: str
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    first_token_seconds: float | None


class LlamaClient:
    """Un único propietario por servidor; la cola FIFO pertenece al backend futuro.

    Un fallo previo a recibir cabeceras mantiene la posición en cuarentena: no
    sabemos si una petición tardía alcanzó el servidor. Recrear el cliente exige
    reiniciar o verificar externamente el servicio, nunca liberar por suposición.
    """

    def __init__(self, url: str, *, transport=None, cleanup_seconds: float = 5):
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/")):
            raise ValueError("Inferencia requiere loopback HTTP sin credenciales en URL")
        self.http = httpx.AsyncClient(base_url=url, transport=transport,
                                     trust_env=False, timeout=None)
        self.free = {0, 1, 2}
        self.quarantined: set[int] = set()
        self.accepted_slots: set[int] = set()
        self.cleanups: set[asyncio.Task] = set()
        self.cleanup_seconds = cleanup_seconds

    async def close(self):
        if self.cleanups:
            await asyncio.gather(*tuple(self.cleanups), return_exceptions=True)
        await self.http.aclose()

    async def verify_server(self):
        """Ejecutar al iniciar el propietario único, antes de aceptar consultas."""
        async with asyncio.timeout(5):
            response = await self.http.get("/slots")
            response.raise_for_status()
            states = response.json()
            if (len(states) != 3 or {s["id"] for s in states} != {0, 1, 2}
                    or any(s["n_ctx"] != 4096 or s["is_processing"] for s in states)):
                raise InferenceError("Servidor no cumple tres posiciones libres de 4096 tokens")

    async def _release_when_idle(self, slot: int):
        try:
            async with asyncio.timeout(self.cleanup_seconds):
                while True:
                    response = await self.http.get("/slots")
                    response.raise_for_status()
                    states = response.json()
                    current = next((s for s in states if s["id"] == slot), None)
                    if current is None:
                        raise ValueError("Monitor no devuelve la posición propia")
                    if current["is_processing"] is False:
                        self.free.add(slot)
                        return
                    await asyncio.sleep(.05)
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError,
                RuntimeError, asyncio.CancelledError):
            self.quarantined.add(slot)

    async def generate(self, messages: list[dict[str, str]], *, seconds: float = 120,
                       max_tokens: int = 256, on_slot=None) -> GeneratedText:
        if not 0 < seconds <= 120 or type(max_tokens) is not int or not 1 <= max_tokens <= 256:
            raise ValueError("Límites: 120 segundos y 256 tokens")
        if not messages or any(m.get("role") not in {"system", "user", "assistant"}
                               or not isinstance(m.get("content"), str) for m in messages):
            raise ValueError("Conversación inválida")
        if not self.free:
            raise Busy("Tres posiciones ocupadas; no se añade una cola interna")
        slot = min(self.free)
        self.free.remove(slot)  # Sin await: admisión atómica en este event loop.
        if on_slot is not None:
            try:
                on_slot(slot)
            except Exception:
                self.free.add(slot)
                raise
        dispatched = accepted = False
        started = time.monotonic()
        first = None
        fragments = []
        final = None
        try:
            async with asyncio.timeout(seconds):
                template = await self.http.post("/apply-template", json={"messages": messages})
                template.raise_for_status()
                prompt = template.json()["prompt"]
                tokenized = await self.http.post("/tokenize", json={"content": prompt,
                                                "add_special": True, "parse_special": True})
                tokenized.raise_for_status()
                prompt_tokens = tokenized.json()["tokens"]
                count = len(prompt_tokens)
                if count + max_tokens > 4096:
                    raise ContextExceeded("Reducir contexto explícitamente; no se trunca")
                body = {"prompt": prompt_tokens, "id_slot": slot, "stream": True,
                        "n_predict": max_tokens, "temperature": 0, "seed": 42,
                        "repeat_penalty": 1, "cache_prompt": False}
                dispatched = True
                async with self.http.stream("POST", "/completion", json=body) as response:
                    response.raise_for_status()
                    accepted = True
                    self.accepted_slots.add(slot)
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        event = json.loads(line[6:])
                        if "error" in event:
                            raise InferenceError("El motor rechazó la generación")
                        content = event.get("content", "")
                        if content:
                            if first is None:
                                first = time.monotonic() - started
                            fragments.append(content)
                        if event.get("stop") is True:
                            final = event
                if (final is None or final.get("truncated") or final.get("stop_type") == "limit"
                        or final.get("stopped_limit")):
                    raise IncompleteGeneration("Respuesta incompleta; no publicarla como final")
                return GeneratedText("".join(fragments), count, final["tokens_predicted"],
                                     time.monotonic() - started, first)
        finally:
            self.accepted_slots.discard(slot)
            # El stream ya está cerrado aquí, incluido en cancelación/timeout.
            if not dispatched:
                self.free.add(slot)
            elif not accepted:
                self.quarantined.add(slot)
            else:
                cleanup = asyncio.create_task(self._release_when_idle(slot))
                self.cleanups.add(cleanup)
                cleanup.add_done_callback(self.cleanups.discard)
                # La comprobación sigue en segundo plano: no prolonga el plazo
                # del usuario; la posición permanece retenida hasta idle.
