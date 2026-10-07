"""Una cuenta propia; sesiones opacas y actividad humana explícita."""
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError


class AuthError(ValueError):
    pass


@dataclass
class Session:
    username: str
    csrf: str
    last_human: float
    reviews: dict = field(default_factory=dict)


class AuthService:
    def __init__(self, username, password_hash, *, clock=time.monotonic):
        if not username or not password_hash.startswith("$argon2id$"):
            raise ValueError("Configura una cuenta propia con hash Argon2id")
        self.username, self.password_hash = username, password_hash
        self.clock = clock
        self.hasher = PasswordHasher()
        self.sessions = {}
        self.attempts = {}
        self.lock = threading.RLock()

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        service = cls(data["username"], data["password_hash"])
        service.credential_path = Path(path).absolute()
        return service

    def login(self, username, password, address):
        now = self.clock()
        with self.lock:
            attempts = [stamp for stamp in self.attempts.get(address, []) if now - stamp < 300]
            self.attempts[address] = attempts
            if len(attempts) >= 5:
                raise AuthError("Espera unos minutos antes de volver a intentar")
            attempts.append(now)
            try:
                valid = self.hasher.verify(self.password_hash, password)
            except (VerificationError, InvalidHashError):
                valid = False
            if not valid or not secrets.compare_digest(username.encode("utf-8"), self.username.encode("utf-8")):
                raise AuthError("Usuario o contraseña incorrectos")
            self.attempts.pop(address, None)
            self.sessions = {key: value for key, value in self.sessions.items() if now - value.last_human < 1800}
            sid = secrets.token_urlsafe(32)
            self.sessions[sid] = Session(self.username, secrets.token_urlsafe(32), now)
            return sid, self.sessions[sid]

    def require(self, sid, csrf=None, *, write=False, human=False):
        with self.lock:
            session = self.sessions.get(sid)
            if session is None or self.clock() - session.last_human >= 1800:
                self.sessions.pop(sid, None)
                raise AuthError("Inicia sesión para continuar; tu borrador no se ha guardado")
            if write and (not isinstance(csrf, str) or not secrets.compare_digest(csrf.encode("utf-8"), session.csrf.encode("utf-8"))):
                raise AuthError("La solicitud no pudo verificarse; vuelve a iniciar sesión")
            if human:
                session.last_human = self.clock()
            return session

    def logout(self, sid, csrf):
        self.require(sid, csrf, write=True)
        with self.lock:
            self.sessions.pop(sid, None)
