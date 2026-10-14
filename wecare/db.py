"""Base de datos SQLite: única fuente de verdad del tótem.

El panel de administración y el tótem leen y escriben aquí, así cualquier cambio
de agenda, visita o menú se refleja en la próxima consulta sin reiniciar nada.
Las fechas se guardan como texto local 'YYYY-MM-DDTHH:MM' (zona de la institución).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS residents (
    id TEXT PRIMARY KEY,
    nombre_preferido TEXT NOT NULL,
    nombre_completo TEXT,
    habitacion TEXT,
    edad INTEGER,
    activo INTEGER NOT NULL DEFAULT 1,
    consentimiento_facial INTEGER NOT NULL DEFAULT 0,
    pin TEXT,                                   -- identificación alternativa (demo)
    tratamiento TEXT NOT NULL DEFAULT 'vos',    -- vos | usted
    ritmo TEXT NOT NULL DEFAULT 'normal',       -- pausado | normal
    tamano_texto TEXT NOT NULL DEFAULT 'grande',-- normal | grande | muy_grande
    volumen INTEGER NOT NULL DEFAULT 80,
    nivel_detalle TEXT NOT NULL DEFAULT 'breve',-- breve | medio
    tono TEXT NOT NULL DEFAULT 'cálido y sencillo',
    intereses TEXT NOT NULL DEFAULT '',         -- separados por coma
    accesibilidad TEXT NOT NULL DEFAULT '',     -- notas libres (p. ej. hipoacusia leve)
    apoyo_permitido TEXT NOT NULL DEFAULT 'recordatorios de agenda, menú, visitas',
    voz_reference_id TEXT,                      -- voz Fish Audio específica (opcional)
    creado TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS face_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_id TEXT NOT NULL REFERENCES residents(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL,
    dim INTEGER NOT NULL,
    fuente TEXT NOT NULL DEFAULT 'foto',
    creado TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo TEXT NOT NULL,
    tipo TEXT NOT NULL,                         -- institucional | personal | visita | salida | comida
    inicio TEXT NOT NULL,                       -- YYYY-MM-DDTHH:MM
    fin TEXT,
    lugar TEXT,
    resident_id TEXT REFERENCES residents(id) ON DELETE CASCADE,  -- NULL = toda la residencia
    estado TEXT NOT NULL DEFAULT 'confirmado',  -- confirmado | tentativo | cancelado
    detalle TEXT,
    visitante TEXT,                             -- nombre/vínculo del visitante
    mostrar_visitante INTEGER NOT NULL DEFAULT 0,
    punto_encuentro TEXT,
    avisar_equipo INTEGER NOT NULL DEFAULT 0,
    fuente TEXT NOT NULL DEFAULT 'panel',
    actualizado TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS event_participants (
    event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    resident_id TEXT NOT NULL REFERENCES residents(id) ON DELETE CASCADE,
    asistio INTEGER,                            -- NULL = aún no ocurrió
    PRIMARY KEY (event_id, resident_id)
);

CREATE TABLE IF NOT EXISTS menu (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT NOT NULL,                        -- YYYY-MM-DD
    servicio TEXT NOT NULL,                     -- desayuno | almuerzo | merienda | cena
    plato TEXT NOT NULL,
    resident_id TEXT REFERENCES residents(id) ON DELETE CASCADE,  -- NULL = menú general
    fuente TEXT NOT NULL DEFAULT 'cocina',
    actualizado TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    session_id TEXT NOT NULL,
    resident_id TEXT,
    canal TEXT NOT NULL,                        -- voz | texto | sistema
    entrada TEXT,
    respuesta TEXT,
    herramientas TEXT,                          -- JSON con las herramientas usadas
    tema TEXT,
    privacidad INTEGER NOT NULL DEFAULT 0,
    derivado INTEGER NOT NULL DEFAULT 0,
    tokens_in INTEGER, tokens_out INTEGER, latencia_ms INTEGER
);

CREATE TABLE IF NOT EXISTS recognition_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    resultado TEXT NOT NULL,                    -- reconocido | no_reconocido | manual | qr | general
    resident_id TEXT,
    score REAL,
    n_caras INTEGER
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    resident_id TEXT NOT NULL,
    event_id INTEGER,
    valor TEXT NOT NULL,                        -- le_gusto | no_le_gusto | no_respondio
    comentario TEXT
);

CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    resident_id TEXT,
    area TEXT NOT NULL,                         -- cocina | actividades | general
    tipo TEXT NOT NULL,                         -- sugerencia | reclamo | urgente
    texto TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'nueva'
);

CREATE TABLE IF NOT EXISTS help_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    resident_id TEXT,
    motivo TEXT NOT NULL,
    origen TEXT NOT NULL,                       -- boton | asistente
    estado TEXT NOT NULL DEFAULT 'abierta',     -- abierta | atendida
    atendida_ts TEXT
);

CREATE TABLE IF NOT EXISTS delivered_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_id TEXT NOT NULL,
    event_id INTEGER NOT NULL,
    fecha TEXT NOT NULL,
    ts TEXT NOT NULL,
    UNIQUE (resident_id, event_id, fecha)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


class Database:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else settings.db_path
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = _connect(self.path)
        self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        cur = self._conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        cur = self._conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    def execute(self, sql: str, params: tuple | dict = ()) -> int:
        with self.tx() as c:
            cur = c.execute(sql, params)
            return cur.lastrowid

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        with self.tx() as c:
            c.executemany(sql, rows)

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str | None) -> None:
        self.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def reset(self) -> None:
        """Borra todos los datos (para re-sembrar la demo)."""
        tables = [r["name"] for r in self.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        with self.tx() as c:
            for t in tables:
                c.execute(f"DELETE FROM {t}")

    def close(self) -> None:
        self._conn.close()


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def set_db(db: Database | None) -> None:
    """Permite inyectar una base en memoria en los tests."""
    global _db
    _db = db
