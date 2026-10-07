"""Operaciones administrativas autenticadas; eventos públicos solo de revisión."""
import asyncio
import hashlib
import json
import secrets
from dataclasses import asdict

from coffee_house.auth import AuthService
from coffee_house.storage.sqlite import RevisionConflict

COOKIE = "coffee_admin"


class Events:
    def __init__(self, revision):
        self.revision = revision
        self.listeners = set()

    def publish(self, revision):
        if revision <= self.revision:
            return
        self.revision = revision
        for queue in tuple(self.listeners):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(revision)

    async def stream(self):
        queue = asyncio.Queue(maxsize=1)
        self.listeners.add(queue)
        try:
            yield {"event": "stock", "data": str(self.revision)}
            while True:
                try:
                    revision = await asyncio.wait_for(queue.get(), 2)
                    yield {"event": "stock", "data": str(revision)}
                except TimeoutError:
                    yield {"event": "heartbeat", "data": str(self.revision)}
        finally:
            self.listeners.discard(queue)


def fingerprint(changes):
    return hashlib.sha256(json.dumps([asdict(c) for c in changes], sort_keys=True).encode()).hexdigest()


class AdminService:
    def __init__(self, store, auth: AuthService):
        self.store, self.auth = store, auth
        self.events = Events(store.snapshot().revision)
        self.save_lock = asyncio.Lock()

    def review(self, sid, csrf, revision, changes):
        session = self.auth.require(sid, csrf, write=True, human=True)
        changes = self.store._changes(changes)
        snapshot = self.store.snapshot()
        if snapshot.revision != revision:
            raise RevisionConflict(snapshot.revision)
        token = secrets.token_urlsafe(24)
        if len(session.reviews) >= 50:
            session.reviews.pop(next(iter(session.reviews)))
        session.reviews[token] = (revision, fingerprint(changes))
        return token

    async def save(self, sid, csrf, token, revision, changes, operation_id):
        async with self.save_lock:
            session = self.auth.require(sid, csrf, write=True, human=True)
            changes = self.store._changes(changes)
            if session.reviews.get(token) != (revision, fingerprint(changes)):
                raise ValueError("Revisa los cambios antes de guardar")
            result = self.store.save(operation_id, session.username, revision, changes)
            # Conservar token permite reconciliar reintento idéntico si se perdió respuesta.
            self.events.publish(result.revision)
            return result

    def history(self, sid, *, after_sequence=0, limit=100):
        self.auth.require(sid)
        return self.store.history(after_sequence=after_sequence, limit=limit)
