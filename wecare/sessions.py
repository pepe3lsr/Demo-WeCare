"""Sesiones del tótem (en memoria).

Una sesión nace cuando alguien se identifica (cara, PIN, QR) o pide el modo
general, y muere cuando la persona se va (sin caras durante un tiempo) o toca
"Terminar". Guarda el historial de conversación para el orquestador y las
banderas de privacidad del momento.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from .config import settings


@dataclass
class Session:
    id: str
    kiosk_id: str
    resident_id: str | None  # None = modo general
    via: str  # facial | pin | qr | general
    created: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    last_face_seen: float = field(default_factory=time.time)
    n_faces: int = 1
    privacy_mode: bool = False  # hay terceros cerca: no exponer datos personales
    privacy_confirmed: bool = False  # la persona pidió ver sus datos igual
    messages: list[dict[str, Any]] = field(default_factory=list)  # historial Claude
    turns: int = 0
    opened: bool = False  # ya se generó la apertura
    delivered: set[int] = field(default_factory=set)

    @property
    def identified(self) -> bool:
        return self.resident_id is not None

    def touch(self) -> None:
        self.last_activity = time.time()

    def update_presence(self, n_faces: int) -> None:
        self.n_faces = n_faces
        if n_faces > 0:
            self.last_face_seen = time.time()
        # Se activa con terceros cerca y se mantiene hasta que la persona confirme
        # o quede sola de nuevo.
        if n_faces > 1:
            self.privacy_mode = True
        elif n_faces == 1:
            self.privacy_mode = False
            self.privacy_confirmed = False

    def expired(self) -> bool:
        idle = time.time() - self.last_face_seen
        return idle > settings.presence_timeout_s and (time.time() - self.last_activity) > 15


@dataclass
class Candidate:
    resident_id: str
    count: int
    last_seen: float


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._by_kiosk: dict[str, str] = {}
        self._candidates: dict[str, Candidate] = {}  # kiosk_id -> candidato en confirmación

    def start(self, kiosk_id: str, resident_id: str | None, via: str) -> Session:
        self.end_for_kiosk(kiosk_id)
        s = Session(id=secrets.token_urlsafe(12), kiosk_id=kiosk_id, resident_id=resident_id, via=via)
        self._sessions[s.id] = s
        self._by_kiosk[kiosk_id] = s.id
        self._candidates.pop(kiosk_id, None)
        return s

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        s = self._sessions.get(session_id)
        if s and s.expired():
            self.end(s.id)
            return None
        return s

    def for_kiosk(self, kiosk_id: str) -> Session | None:
        return self.get(self._by_kiosk.get(kiosk_id))

    def end(self, session_id: str) -> None:
        s = self._sessions.pop(session_id, None)
        if s and self._by_kiosk.get(s.kiosk_id) == session_id:
            self._by_kiosk.pop(s.kiosk_id, None)

    def end_for_kiosk(self, kiosk_id: str) -> None:
        sid = self._by_kiosk.get(kiosk_id)
        if sid:
            self.end(sid)

    def track_candidate(self, kiosk_id: str, resident_id: str | None) -> bool:
        """Cuenta observaciones consecutivas del mismo candidato. True cuando se confirma."""
        if resident_id is None:
            self._candidates.pop(kiosk_id, None)
            return False
        c = self._candidates.get(kiosk_id)
        if c and c.resident_id == resident_id and time.time() - c.last_seen < 5:
            c.count += 1
            c.last_seen = time.time()
        else:
            c = Candidate(resident_id, 1, time.time())
            self._candidates[kiosk_id] = c
        if c.count >= settings.face_confirm_frames:
            self._candidates.pop(kiosk_id, None)
            return True
        return False

    def active(self) -> list[Session]:
        return [s for s in list(self._sessions.values()) if not s.expired()]
