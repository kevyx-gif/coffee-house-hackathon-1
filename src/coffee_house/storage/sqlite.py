"""Disponibilidad simulada: transacciones completas, revisión e idempotencia.

No es una API pública. El futuro servicio debe aportar la identidad autenticada
y autorizar lecturas del historial/escrituras antes de invocar este repositorio.
"""

import hashlib
import json
import os
import sqlite3
import time
from collections.abc import Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from coffee_house.domain.catalog import Catalog, nonnegative_integer
from coffee_house.domain.pricing import Availability, initial_simulation

SCHEMA_VERSION = 1


class StorageUnavailable(RuntimeError):
    """No hay confirmación; el consumidor conserva su borrador."""


class RevisionConflict(ValueError):
    def __init__(self, current_revision: int):
        super().__init__("La disponibilidad cambió; revisa tu borrador antes de guardar.")
        self.current_revision = current_revision


class OperationConflict(ValueError):
    """Un identificador de operación no puede reutilizarse para otro guardado."""


def wal_supported(version: tuple[int, ...]) -> bool:
    """Corrección WAL-reset publicada; no presume parches del distribuidor."""
    return version >= (3, 51, 3) or (3, 50, 7) <= version < (3, 51, 0) or (3, 44, 6) <= version < (3, 45, 0)


@dataclass(frozen=True)
class Change:
    kind: str
    identifier: str
    available: bool


@dataclass(frozen=True)
class SaveResult:
    operation_id: str
    revision: int
    changed: int
    saved_at: str


@dataclass(frozen=True)
class HistoryEntry:
    sequence: int
    operation_id: str
    revision: int
    saved_at: str
    administrator: str
    kind: str
    identifier: str
    before: bool
    after: bool


def _identity(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label} inválido.")
    return value


class AvailabilityStore:
    def __init__(self, path: str | Path, catalog: Catalog, *, busy_timeout_ms: int = 1000,
                 journal_mode: str = "DELETE"):
        self.path = Path(path).absolute()
        self.catalog = catalog
        nonnegative_integer(busy_timeout_ms, "Timeout")
        if busy_timeout_ms > 10000 or journal_mode not in ("DELETE", "WAL"):
            raise ValueError("Configuración SQLite no admitida.")
        if journal_mode == "WAL" and not wal_supported(sqlite3.sqlite_version_info):
            raise ValueError("WAL bloqueado: requiere corrección WAL-reset comprobada.")
        self.busy_timeout_ms = busy_timeout_ms
        self.journal_mode = journal_mode
        self.catalog_identity = json.dumps({"products": sorted(catalog.products), "extras": sorted(catalog.extras)},
                                           sort_keys=True, separators=(",", ":"))

    @contextmanager
    def _connection(self):
        # mode=rw impide recrear una base perdida en una lectura o un guardado.
        connection = None
        try:
            connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                                         timeout=self.busy_timeout_ms / 1000, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
            actual = connection.execute("PRAGMA journal_mode").fetchone()[0]
            if actual.lower() != self.journal_mode.lower():
                raise StorageUnavailable("Modo SQLite diferente del configurado; no se cambia automáticamente.")
            yield connection
        except sqlite3.Error as exc:
            raise StorageUnavailable("No se pudo confirmar la operación de almacenamiento.") from exc
        finally:
            if connection is not None:
                if connection.in_transaction:
                    connection.rollback()
                connection.close()

    def initialize(self) -> None:
        """Inicialización explícita. No migra ni restaura semillas al reiniciar."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            with self._connection() as connection:
                self._check(connection)
            return
        else:
            os.close(descriptor)
        # El archivo nuevo está vacío; configurar modo solo en inicialización.
        try:
            with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
                actual = connection.execute(f"PRAGMA journal_mode={self.journal_mode}").fetchone()[0]
                if actual.lower() != self.journal_mode.lower():
                    raise StorageUnavailable("No se pudo activar el modo SQLite solicitado.")
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                for statement in (
                    "CREATE TABLE metadata (singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL CHECK(revision>=0), catalog_identity TEXT NOT NULL)",
                    "CREATE TABLE availability (kind TEXT NOT NULL CHECK(kind IN ('product','extra')), identifier TEXT NOT NULL, available INTEGER NOT NULL CHECK(available IN (0,1)), PRIMARY KEY(kind,identifier))",
                    "CREATE TABLE operations (operation_id TEXT PRIMARY KEY, administrator TEXT NOT NULL, fingerprint TEXT NOT NULL, revision INTEGER NOT NULL, changed INTEGER NOT NULL, saved_at TEXT NOT NULL)",
                    "CREATE TABLE history (sequence INTEGER PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operations(operation_id), revision INTEGER NOT NULL, saved_at TEXT NOT NULL, administrator TEXT NOT NULL, kind TEXT NOT NULL, identifier TEXT NOT NULL, before_state INTEGER NOT NULL CHECK(before_state IN (0,1)), after_state INTEGER NOT NULL CHECK(after_state IN (0,1)), FOREIGN KEY(kind,identifier) REFERENCES availability(kind,identifier))",
                ):
                    connection.execute(statement)
                seed = initial_simulation(self.catalog)
                connection.execute("INSERT INTO metadata VALUES (1,0,?)", (self.catalog_identity,))
                connection.executemany("INSERT INTO availability VALUES (?,?,?)",
                                       [(kind, key, int(value)) for kind, values in
                                        (("product", seed.products), ("extra", seed.extras)) for key, value in values.items()])
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.commit()
        except (sqlite3.Error, OSError) as exc:
            raise StorageUnavailable("Inicialización fallida; no se confirmó una base nueva.") from exc

    def _check(self, connection) -> int:
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise StorageUnavailable("Esquema no admitido; requiere revisión, no se reinicializa.")
        row = connection.execute("SELECT revision,catalog_identity FROM metadata WHERE singleton=1").fetchone()
        if row is None or row["catalog_identity"] != self.catalog_identity:
            raise StorageUnavailable("Catálogo distinto o estado incompleto; requiere revisión.")
        identifiers = {(row[0], row[1]) for row in connection.execute("SELECT kind,identifier FROM availability")}
        expected = {("product", key) for key in self.catalog.products} | {("extra", key) for key in self.catalog.extras}
        if identifiers != expected:
            raise StorageUnavailable("Disponibilidad incompleta; no se reconstruye con semillas.")
        return row["revision"]

    def snapshot(self) -> Availability:
        with self._connection() as connection:
            connection.execute("BEGIN")
            revision = self._check(connection)
            rows = connection.execute("SELECT kind,identifier,available FROM availability").fetchall()
            result = Availability({r["identifier"]: bool(r["available"]) for r in rows if r["kind"] == "product"},
                                  {r["identifier"]: bool(r["available"]) for r in rows if r["kind"] == "extra"}, revision, True)
            connection.commit()
            return result

    def _changes(self, changes: Sequence[Change]) -> tuple[Change, ...]:
        if not isinstance(changes, (tuple, list)) or not changes or len(changes) > len(self.catalog.products) + len(self.catalog.extras):
            raise ValueError("Proporciona un lote explícito no vacío.")
        seen = set()
        for change in changes:
            if not isinstance(change, Change) or change.kind not in ("product", "extra") or type(change.available) is not bool:
                raise ValueError("Cambio de disponibilidad inválido.")
            keys = self.catalog.products if change.kind == "product" else self.catalog.extras
            if not isinstance(change.identifier, str) or change.identifier not in keys:
                raise ValueError("Producto o extra no reconocido.")
            key = (change.kind, change.identifier)
            if key in seen:
                raise ValueError("Un elemento aparece varias veces en el lote.")
            seen.add(key)
        return tuple(sorted(changes, key=lambda c: (c.kind, c.identifier)))

    @staticmethod
    def _result(row) -> SaveResult:
        return SaveResult(row["operation_id"], row["revision"], row["changed"], row["saved_at"])

    def save(self, operation_id: str, administrator: str, expected_revision: int,
             changes: Sequence[Change]) -> SaveResult:
        _identity(operation_id, "Operación")
        _identity(administrator, "Administrador")
        nonnegative_integer(expected_revision, "Revisión esperada")
        changes = self._changes(changes)
        payload = json.dumps([administrator, expected_revision,
                              [(c.kind, c.identifier, c.available) for c in changes]], separators=(",", ":"))
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._check(connection)
            previous = connection.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
            if previous is not None:
                if previous["fingerprint"] != fingerprint or previous["administrator"] != administrator:
                    raise OperationConflict("Identificador usado para otro guardado.")
                connection.commit()
                return self._result(previous)
            if current != expected_revision:
                raise RevisionConflict(current)
            effective = []
            for change in changes:
                before = connection.execute("SELECT available FROM availability WHERE kind=? AND identifier=?",
                                            (change.kind, change.identifier)).fetchone()[0]
                if bool(before) != change.available:
                    effective.append((change, before))
            revision = current + bool(effective)
            saved_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            result = SaveResult(operation_id, revision, len(effective), saved_at)
            connection.execute("INSERT INTO operations VALUES (?,?,?,?,?,?)",
                               (operation_id, administrator, fingerprint, revision, len(effective), saved_at))
            for change, before in effective:
                connection.execute("UPDATE availability SET available=? WHERE kind=? AND identifier=?",
                                   (int(change.available), change.kind, change.identifier))
                connection.execute("INSERT INTO history (operation_id,revision,saved_at,administrator,kind,identifier,before_state,after_state) VALUES (?,?,?,?,?,?,?,?)",
                                   (operation_id, revision, saved_at, administrator, change.kind, change.identifier, before, int(change.available)))
            connection.execute("UPDATE metadata SET revision=? WHERE singleton=1", (revision,))
            connection.commit()
            return result

    def operation(self, operation_id: str, administrator: str) -> SaveResult | None:
        _identity(operation_id, "Operación")
        _identity(administrator, "Administrador")
        with self._connection() as connection:
            self._check(connection)
            row = connection.execute("SELECT * FROM operations WHERE operation_id=? AND administrator=?",
                                     (operation_id, administrator)).fetchone()
            return None if row is None else self._result(row)

    def history(self, *, after_sequence: int = 0, limit: int = 100) -> tuple[HistoryEntry, ...]:
        nonnegative_integer(after_sequence, "Cursor")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Límite de historial inválido.")
        with self._connection() as connection:
            self._check(connection)
            rows = connection.execute("SELECT * FROM history WHERE sequence>? ORDER BY sequence LIMIT ?",
                                      (after_sequence, limit)).fetchall()
            return tuple(HistoryEntry(r["sequence"], r["operation_id"], r["revision"], r["saved_at"],
                                      r["administrator"], r["kind"], r["identifier"], bool(r["before_state"]),
                                      bool(r["after_state"])) for r in rows)

    def configuration(self) -> Mapping[str, object]:
        with self._connection() as connection:
            self._check(connection)
            return {"sqlite_version": sqlite3.sqlite_version, "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
                    "synchronous": connection.execute("PRAGMA synchronous").fetchone()[0],
                    "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0],
                    "busy_timeout_ms": connection.execute("PRAGMA busy_timeout").fetchone()[0],
                    "schema_version": connection.execute("PRAGMA user_version").fetchone()[0]}

    def backup(self, destination: str | Path) -> Path:
        """Copia consistente a archivo NUEVO; también permite restaurar sin sobrescribir."""
        target = Path(destination).absolute()
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        deadline = time.monotonic() + 10

        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise StorageUnavailable("Respaldo sin confirmar dentro del plazo.")

        try:
            with self._connection() as source:
                self._check(source)
                with closing(sqlite3.connect(target, isolation_level=None)) as copy:
                    source.backup(copy, pages=64, progress=progress, sleep=0.01)
                    if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise StorageUnavailable("No se confirmó la integridad del respaldo.")
            # Verifica catálogo/esquema/estado del respaldo antes de confirmarlo.
            AvailabilityStore(target, self.catalog, journal_mode=self.journal_mode).snapshot()
            return target
        except Exception:
            target.unlink()  # solo el archivo nuevo creado por esta llamada
            raise
