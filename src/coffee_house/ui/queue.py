"""Tres activos, diez FIFO; cancelación no equivale a liberación física."""
import asyncio
import secrets
import time
from collections import deque
from dataclasses import dataclass


class AdmissionError(ValueError):
    pass


@dataclass
class Ticket:
    id: str
    session: str
    question: str
    deadline: float
    confirmed: object = None
    candidates: tuple = ()
    budget_cents: int | None = None
    status: str = "waiting"
    result: object = None
    slot: int | None = None
    held: bool = True
    task: object = None
    expiry: object = None


class QueryQueue:
    def __init__(self, flow, *, limit_seconds=120):
        if not 0 < limit_seconds <= 120:
            raise ValueError("Plazo máximo120s")
        self.closed = False
        self.flow = flow
        self.limit_seconds = limit_seconds
        self.waiting = deque()
        self.running = set()
        self.tickets = {}
        self.by_session = {}

    def submit(self, session, question, confirmed=None, *, candidates=(), budget_cents=None, submitted_at=None):
        if self.closed:
            raise AdmissionError("El servicio se reinició; no se reenviará la consulta")
        self.prune()
        if not isinstance(question, str) or not question.strip() or len(question) > 2000:
            raise AdmissionError("Escribe una consulta breve, de hasta2000caracteres")
        previous = self.tickets.get(self.by_session.get(session))
        if previous and previous.held:
            raise AdmissionError("Ya tienes una consulta pendiente; espera o cancélala")
        free = self.flow.client.free
        can_start = not self.waiting and len(self.running) < 3 and free
        if not can_start and len(self.waiting) >= 10:
            raise AdmissionError("El asistente está recibiendo muchas consultas. Inténtalo de nuevo en un rato; mientras tanto, puedes explorar el menú")
        remaining = self.limit_seconds
        if submitted_at is not None:
            remaining = min(remaining,submitted_at/1000 + self.limit_seconds - time.time())
        if remaining <= 0:
            raise AdmissionError("La consulta demoró más de dos minutos. Conserva tu texto para reintentar; no se reenviará automáticamente")
        ticket = Ticket(secrets.token_urlsafe(18), session, question, time.monotonic() + remaining, confirmed)
        ticket.candidates,ticket.budget_cents = tuple(candidates),budget_cents
        self.tickets[ticket.id] = ticket
        self.by_session[session] = ticket.id
        self.waiting.append(ticket.id)
        ticket.expiry = asyncio.create_task(self._expire(ticket))
        self._start()
        return ticket

    def _start(self):
        if self.closed:
            return
        while self.waiting and len(self.running) < 3 and len(self.flow.client.free) > sum(self.tickets[key].slot is None for key in self.running):
            ticket = self.tickets[self.waiting.popleft()]
            if not ticket.held or ticket.status != "waiting":
                continue
            if time.monotonic() >= ticket.deadline:
                ticket.status, ticket.held = "expired", False
                continue
            self.running.add(ticket.id)
            ticket.status = "running"
            ticket.task = asyncio.create_task(self._run(ticket))

    async def _run(self, ticket):
        try:
            def assigned(slot):
                ticket.slot = slot
            result = await self.flow.answer(ticket.question, confirmed=ticket.confirmed,
                seconds=max(.001, ticket.deadline-time.monotonic()), on_slot=assigned,
                candidates=ticket.candidates,budget_cents=ticket.budget_cents)
            if time.monotonic() < ticket.deadline and ticket.status == "running":
                ticket.result, ticket.status = result, "done"
            elif ticket.status == "running":
                ticket.status = "expired"
        except asyncio.CancelledError:
            if ticket.status == "running":
                ticket.status = "cancelled"
        except (ValueError, RuntimeError, OSError):
            ticket.status = "failed"
        finally:
            if self.flow.client.cleanups:
                await asyncio.gather(*tuple(self.flow.client.cleanups), return_exceptions=True)
            uncertain = ticket.slot is not None and ticket.slot not in self.flow.client.free
            ticket.held = uncertain
            if uncertain:
                ticket.status = "uncertain"
                ticket.result = None
            self.running.discard(ticket.id)
            if ticket.expiry:
                ticket.expiry.cancel()
            self._start()

    async def _expire(self, ticket):
        await asyncio.sleep(max(0, ticket.deadline-time.monotonic()))
        if ticket.status in {"waiting", "running"}:
            self.cancel(ticket.session, ticket.id, expired=True)

    def cancel(self, session, identifier, *, expired=False):
        ticket = self.get(session, identifier)
        if ticket.status == "waiting":
            self.waiting.remove(identifier)
            ticket.held = False
            if ticket.expiry:
                ticket.expiry.cancel()
        elif ticket.status == "running":
            ticket.task.cancel()
        else:
            return ticket
        ticket.status = "expired" if expired else "cancelled"
        ticket.result = None
        self._start()
        return ticket

    def get(self, session, identifier):
        ticket = self.tickets.get(identifier)
        if ticket is None or ticket.session != session:
            raise AdmissionError("La consulta ya no está disponible; no se reenviará automáticamente")
        return ticket

    def prune(self):
        for key,ticket in tuple(self.tickets.items()):
            if not ticket.held and time.monotonic() > ticket.deadline + 60:
                self.tickets.pop(key)
                if self.by_session.get(ticket.session) == key:
                    self.by_session.pop(ticket.session,None)

    async def close(self):
        self.closed = True
        self.waiting.clear()
        tasks = []
        for ticket in self.tickets.values():
            if ticket.expiry:
                ticket.expiry.cancel()
                tasks.append(ticket.expiry)
            if ticket.status == "waiting":
                ticket.status,ticket.held = "cancelled",False
            if ticket.task and not ticket.task.done():
                ticket.task.cancel()
                tasks.append(ticket.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
