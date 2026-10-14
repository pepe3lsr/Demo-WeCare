"""Reloj de la aplicación, con hora de demo configurable desde el panel.

Toda la lógica de agenda usa `now()`, así el equipo puede fijar "hoy a las 15:40"
para mostrar la escena "¿qué tengo después de merendar?" a cualquier hora real.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import settings

log = logging.getLogger(__name__)


def _load_tz():
    try:
        return ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        # Windows no trae la base IANA; si falta el paquete `tzdata`, usamos un offset fijo.
        log.warning("Zona horaria %s no disponible (instalá el paquete tzdata). Uso UTC-3 fijo.", settings.timezone)
        return timezone(timedelta(hours=-3), "ART")


_TZ = _load_tz()
_override: datetime | None = None
_override_set_at: datetime | None = None


def real_now() -> datetime:
    return datetime.now(_TZ)


def now() -> datetime:
    """Hora actual de la demo. Si hay una hora fijada, avanza desde ese punto."""
    if _override is None:
        return real_now()
    return _override + (real_now() - _override_set_at)


def set_demo_time(value: datetime | None) -> None:
    global _override, _override_set_at
    if value is None:
        _override = None
        _override_set_at = None
        return
    if value.tzinfo is None:
        value = value.replace(tzinfo=_TZ)
    _override = value
    _override_set_at = real_now()


def is_overridden() -> bool:
    return _override is not None


def today():
    return now().date()


def parse_local(s: str) -> datetime:
    """Parsea 'YYYY-MM-DDTHH:MM' o ISO completo en la zona de la institución."""
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=_TZ) if dt.tzinfo is None else dt.astimezone(_TZ)


def fmt_hora(dt: datetime) -> str:
    return dt.astimezone(_TZ).strftime("%H:%M")


def fmt_fecha(dt: datetime) -> str:
    return dt.astimezone(_TZ).strftime("%d/%m")


def momento_del_dia(dt: datetime | None = None) -> str:
    h = (dt or now()).hour
    if h < 12:
        return "mañana"
    if h < 19:
        return "tarde"
    return "noche"


def saludo(dt: datetime | None = None) -> str:
    return {"mañana": "buenos días", "tarde": "buenas tardes", "noche": "buenas noches"}[momento_del_dia(dt)]


def in_minutes(dt: datetime) -> int:
    return int((dt - now()) / timedelta(minutes=1))
