"""Consultas de dominio sobre la base de datos.

Todas las funciones que exponen datos de un residente reciben el `resident_id`
desde la sesión del servidor, nunca desde el modelo ni desde el front.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from . import clock
from .db import get_db

SERVICIOS = ["desayuno", "almuerzo", "merienda", "cena"]
# Hora límite (HH:MM) hasta la que cada servicio se considera "el próximo".
SERVICIO_LIMITE = {"desayuno": "09:30", "almuerzo": "13:45", "merienda": "17:00", "cena": "21:00"}
ESTADO_LABEL = {"confirmado": "confirmado", "tentativo": "a confirmar", "cancelado": "cancelado"}


def now_str(dt: datetime | None = None) -> str:
    return (dt or clock.now()).strftime("%Y-%m-%dT%H:%M")


def today_str(d: date | None = None) -> str:
    return (d or clock.today()).isoformat()


def ts_str() -> str:
    return clock.real_now().strftime("%Y-%m-%dT%H:%M:%S")


# ----------------------------------------------------------------------------- residentes

def list_residents(active_only: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM residents" + (" WHERE activo = 1" if active_only else "") + " ORDER BY nombre_preferido"
    rows = get_db().query(sql)
    for r in rows:
        r["n_embeddings"] = count_embeddings(r["id"])
    return rows


def get_resident(rid: str | None) -> dict[str, Any] | None:
    if not rid:
        return None
    return get_db().one("SELECT * FROM residents WHERE id = ?", (rid,))


def upsert_resident(data: dict[str, Any]) -> None:
    cols = ["id", "nombre_preferido", "nombre_completo", "habitacion", "edad", "activo",
            "consentimiento_facial", "pin", "tratamiento", "ritmo", "tamano_texto", "volumen",
            "nivel_detalle", "tono", "intereses", "accesibilidad", "apoyo_permitido", "voz_reference_id"]
    row = {c: data.get(c) for c in cols}
    row["activo"] = 1 if row.get("activo") in (1, True, "1", "on") else 0
    row["consentimiento_facial"] = 1 if row.get("consentimiento_facial") in (1, True, "1", "on") else 0
    for k, default in (("tratamiento", "vos"), ("ritmo", "normal"), ("tamano_texto", "grande"),
                       ("nivel_detalle", "breve"), ("tono", "cálido y sencillo"), ("intereses", ""),
                       ("accesibilidad", ""), ("apoyo_permitido", "recordatorios de agenda, menú, visitas")):
        if not row.get(k):
            row[k] = default
    row["volumen"] = int(row.get("volumen") or 80)
    sets = ", ".join(f"{c} = :{c}" for c in cols if c != "id")
    get_db().execute(
        f"INSERT INTO residents ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
        f"ON CONFLICT(id) DO UPDATE SET {sets}", row)


def verify_pin(rid: str, pin: str) -> bool:
    r = get_resident(rid)
    return bool(r and r["activo"] and r["pin"] and str(r["pin"]) == str(pin).strip())


# ----------------------------------------------------------------------------- biometría

def add_embedding(rid: str, vec: np.ndarray, fuente: str = "foto") -> None:
    vec = np.asarray(vec, dtype=np.float32)
    get_db().execute("INSERT INTO face_embeddings (resident_id, embedding, dim, fuente) VALUES (?, ?, ?, ?)",
                     (rid, vec.tobytes(), int(vec.shape[0]), fuente))


def count_embeddings(rid: str) -> int:
    row = get_db().one("SELECT COUNT(*) AS n FROM face_embeddings WHERE resident_id = ?", (rid,))
    return int(row["n"]) if row else 0


def delete_embeddings(rid: str) -> None:
    get_db().execute("DELETE FROM face_embeddings WHERE resident_id = ?", (rid,))


def load_gallery() -> dict[str, list[np.ndarray]]:
    """Sólo residentes activos con consentimiento facial vigente."""
    rows = get_db().query(
        "SELECT e.resident_id, e.embedding, e.dim FROM face_embeddings e "
        "JOIN residents r ON r.id = e.resident_id WHERE r.activo = 1 AND r.consentimiento_facial = 1")
    gallery: dict[str, list[np.ndarray]] = {}
    for r in rows:
        gallery.setdefault(r["resident_id"], []).append(
            np.frombuffer(r["embedding"], dtype=np.float32, count=r["dim"]))
    return gallery


# ----------------------------------------------------------------------------- agenda

def _enrich_event(e: dict[str, Any]) -> dict[str, Any]:
    ini = datetime.fromisoformat(e["inicio"])
    e["hora"] = ini.strftime("%H:%M")
    e["fecha"] = ini.date().isoformat()
    e["hora_fin"] = datetime.fromisoformat(e["fin"]).strftime("%H:%M") if e.get("fin") else None
    e["estado_label"] = ESTADO_LABEL.get(e["estado"], e["estado"])
    e["pasado"] = (e.get("fin") or e["inicio"]) < now_str()
    e["en_curso"] = e["inicio"] <= now_str() <= (e.get("fin") or e["inicio"])
    e["minutos_para"] = int((ini.replace(tzinfo=clock.now().tzinfo) - clock.now()).total_seconds() // 60)
    return e


def _participants(event_id: int) -> list[str]:
    return [r["resident_id"] for r in get_db().query(
        "SELECT resident_id FROM event_participants WHERE event_id = ?", (event_id,))]


def list_events(*, fecha: str | None = None, resident_id: str | None = None, scope: str = "relevante",
                incluir_pasados: bool = False, incluir_cancelados: bool = False,
                tipos: list[str] | None = None, desde: str | None = None, hasta: str | None = None
                ) -> list[dict[str, Any]]:
    """Eventos de un día.

    scope:
      - 'general'   : sólo eventos de toda la residencia (sin datos personales).
      - 'relevante' : generales abiertos a todos + aquellos donde el residente está
                      inscripto + sus eventos personales (visitas, salidas).
      - 'personal'  : sólo personales del residente.
    """
    fecha = fecha or today_str()
    where = ["date(inicio) = ?"]
    params: list[Any] = [fecha]
    if not incluir_cancelados:
        where.append("estado != 'cancelado'")
    if tipos:
        where.append(f"tipo IN ({','.join('?' * len(tipos))})")
        params += tipos
    if desde:
        where.append("inicio >= ?")
        params.append(desde)
    if hasta:
        where.append("inicio <= ?")
        params.append(hasta)
    if scope == "general" or not resident_id:
        where.append("resident_id IS NULL")
    elif scope == "personal":
        where.append("resident_id = ?")
        params.append(resident_id)
    else:
        where.append("(resident_id IS NULL OR resident_id = ?)")
        params.append(resident_id)
    rows = get_db().query(f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY inicio", tuple(params))
    out = []
    for e in rows:
        parts = _participants(e["id"])
        if e["resident_id"] is None and parts and resident_id and scope == "relevante" and resident_id not in parts:
            continue  # taller con inscripción en el que no participa
        e = _enrich_event(e)
        e["participa"] = bool(resident_id and (e["resident_id"] == resident_id or resident_id in parts))
        e["participantes"] = parts
        if not incluir_pasados and e["pasado"]:
            continue
        out.append(e)
    return out


def get_event(event_id: int) -> dict[str, Any] | None:
    e = get_db().one("SELECT * FROM events WHERE id = ?", (event_id,))
    if e:
        e = _enrich_event(e)
        e["participantes"] = _participants(event_id)
    return e


def upsert_event(data: dict[str, Any]) -> int:
    cols = ["titulo", "tipo", "inicio", "fin", "lugar", "resident_id", "estado", "detalle", "visitante",
            "mostrar_visitante", "punto_encuentro", "avisar_equipo", "fuente"]
    row = {c: (data.get(c) or None) for c in cols}
    row["estado"] = row["estado"] or "confirmado"
    row["fuente"] = row["fuente"] or "panel"
    row["mostrar_visitante"] = 1 if data.get("mostrar_visitante") in (1, True, "1", "on") else 0
    row["avisar_equipo"] = 1 if data.get("avisar_equipo") in (1, True, "1", "on") else 0
    row["actualizado"] = ts_str()
    db = get_db()
    if data.get("id"):
        row["id"] = int(data["id"])
        sets = ", ".join(f"{c} = :{c}" for c in cols + ["actualizado"])
        db.execute(f"UPDATE events SET {sets} WHERE id = :id", row)
        event_id = row["id"]
    else:
        event_id = db.execute(
            f"INSERT INTO events ({', '.join(cols + ['actualizado'])}) "
            f"VALUES ({', '.join(':' + c for c in cols + ['actualizado'])})", row)
    if "participantes" in data:
        set_participants(event_id, data["participantes"] or [])
    return event_id


def delete_event(event_id: int) -> None:
    get_db().execute("DELETE FROM events WHERE id = ?", (event_id,))


def set_participants(event_id: int, rids: list[str]) -> None:
    db = get_db()
    existing = {r: None for r in _participants(event_id)}
    with db.tx() as c:
        c.execute("DELETE FROM event_participants WHERE event_id = ?", (event_id,))
        for rid in rids:
            c.execute("INSERT OR IGNORE INTO event_participants (event_id, resident_id, asistio) VALUES (?, ?, ?)",
                      (event_id, rid, existing.get(rid)))


def mark_attendance(event_id: int, rid: str, asistio: bool | None) -> None:
    get_db().execute("INSERT INTO event_participants (event_id, resident_id, asistio) VALUES (?, ?, ?) "
                     "ON CONFLICT(event_id, resident_id) DO UPDATE SET asistio = excluded.asistio",
                     (event_id, rid, None if asistio is None else int(asistio)))


def recent_activities(rid: str, dias: int = 2) -> list[dict[str, Any]]:
    """Actividades pasadas en las que participó (o estaba inscripto) el residente, con su feedback."""
    desde = (clock.now() - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M")
    rows = get_db().query(
        "SELECT e.*, p.asistio FROM events e JOIN event_participants p ON p.event_id = e.id "
        "WHERE p.resident_id = ? AND e.inicio >= ? AND e.inicio < ? AND e.estado != 'cancelado' "
        "AND (p.asistio IS NULL OR p.asistio = 1) ORDER BY e.inicio DESC", (rid, desde, now_str()))
    out = []
    for e in rows:
        e = _enrich_event(e)
        fb = feedback_for(rid, e["id"])
        e["feedback"] = fb["valor"] if fb else None
        e["cuando"] = "hoy" if e["fecha"] == today_str() else (
            "ayer" if e["fecha"] == (clock.today() - timedelta(days=1)).isoformat() else e["fecha"])
        out.append(e)
    return out


# ----------------------------------------------------------------------------- menú

def next_service(dt: datetime | None = None) -> str:
    hhmm = (dt or clock.now()).strftime("%H:%M")
    for s in SERVICIOS:
        if hhmm <= SERVICIO_LIMITE[s]:
            return s
    return "desayuno"


def get_menu(fecha: str | None = None, servicio: str | None = None, resident_id: str | None = None
             ) -> list[dict[str, Any]]:
    fecha = fecha or today_str()
    where, params = ["fecha = ?", "resident_id IS NULL"], [fecha]
    if servicio:
        where.append("servicio = ?")
        params.append(servicio)
    rows = get_db().query(f"SELECT * FROM menu WHERE {' AND '.join(where)}", tuple(params))
    order = {s: i for i, s in enumerate(SERVICIOS)}
    rows.sort(key=lambda r: order.get(r["servicio"], 9))
    if resident_id:
        alts = {a["servicio"]: a["plato"] for a in get_db().query(
            "SELECT servicio, plato FROM menu WHERE fecha = ? AND resident_id = ?", (fecha, resident_id))}
        for r in rows:
            r["alternativa"] = alts.get(r["servicio"])
    return rows


def list_menu(fecha: str | None = None) -> list[dict[str, Any]]:
    rows = get_db().query("SELECT m.*, r.nombre_preferido FROM menu m LEFT JOIN residents r ON r.id = m.resident_id "
                          "WHERE fecha = ? ORDER BY servicio, resident_id", (fecha or today_str(),))
    order = {s: i for i, s in enumerate(SERVICIOS)}
    rows.sort(key=lambda r: (order.get(r["servicio"], 9), r["resident_id"] or ""))
    return rows


def upsert_menu(fecha: str, servicio: str, plato: str, resident_id: str | None = None, fuente: str = "cocina") -> None:
    db = get_db()
    existing = db.one("SELECT id FROM menu WHERE fecha = ? AND servicio = ? AND resident_id IS ?",
                      (fecha, servicio, resident_id))
    if existing:
        db.execute("UPDATE menu SET plato = ?, fuente = ?, actualizado = ? WHERE id = ?",
                   (plato, fuente, ts_str(), existing["id"]))
    else:
        db.execute("INSERT INTO menu (fecha, servicio, plato, resident_id, fuente, actualizado) VALUES (?, ?, ?, ?, ?, ?)",
                   (fecha, servicio, plato, resident_id, fuente, ts_str()))


def delete_menu(menu_id: int) -> None:
    get_db().execute("DELETE FROM menu WHERE id = ?", (menu_id,))


# ----------------------------------------------------------------------------- registro

def log_interaction(session_id: str, resident_id: str | None, canal: str, entrada: str | None,
                    respuesta: str | None, herramientas: list[str] | None = None, tema: str | None = None,
                    privacidad: bool = False, derivado: bool = False, tokens_in: int | None = None,
                    tokens_out: int | None = None, latencia_ms: int | None = None) -> int:
    return get_db().execute(
        "INSERT INTO interactions (ts, session_id, resident_id, canal, entrada, respuesta, herramientas, tema, "
        "privacidad, derivado, tokens_in, tokens_out, latencia_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ts_str(), session_id, resident_id, canal, entrada, respuesta,
         json.dumps(herramientas or [], ensure_ascii=False), tema, int(privacidad), int(derivado),
         tokens_in, tokens_out, latencia_ms))


def list_interactions(limit: int = 100) -> list[dict[str, Any]]:
    rows = get_db().query("SELECT i.*, r.nombre_preferido FROM interactions i LEFT JOIN residents r ON r.id = i.resident_id "
                          "ORDER BY i.id DESC LIMIT ?", (limit,))
    for r in rows:
        r["herramientas"] = json.loads(r["herramientas"] or "[]")
    return rows


def log_recognition(resultado: str, resident_id: str | None = None, score: float | None = None,
                    n_caras: int | None = None) -> None:
    get_db().execute("INSERT INTO recognition_events (ts, resultado, resident_id, score, n_caras) VALUES (?,?,?,?,?)",
                     (ts_str(), resultado, resident_id, score, n_caras))


def add_feedback(rid: str, event_id: int | None, valor: str, comentario: str | None = None) -> None:
    get_db().execute("INSERT INTO feedback (ts, resident_id, event_id, valor, comentario) VALUES (?,?,?,?,?)",
                     (ts_str(), rid, event_id, valor, comentario))


def feedback_for(rid: str, event_id: int) -> dict[str, Any] | None:
    return get_db().one("SELECT * FROM feedback WHERE resident_id = ? AND event_id = ? ORDER BY id DESC LIMIT 1",
                        (rid, event_id))


def list_feedback(limit: int = 100) -> list[dict[str, Any]]:
    return get_db().query("SELECT f.*, r.nombre_preferido, e.titulo FROM feedback f "
                          "LEFT JOIN residents r ON r.id = f.resident_id LEFT JOIN events e ON e.id = f.event_id "
                          "ORDER BY f.id DESC LIMIT ?", (limit,))


def add_suggestion(rid: str | None, area: str, tipo: str, texto: str) -> int:
    return get_db().execute("INSERT INTO suggestions (ts, resident_id, area, tipo, texto) VALUES (?,?,?,?,?)",
                            (ts_str(), rid, area, tipo, texto))


def list_suggestions() -> list[dict[str, Any]]:
    return get_db().query("SELECT s.*, r.nombre_preferido FROM suggestions s LEFT JOIN residents r ON r.id = s.resident_id "
                          "ORDER BY s.id DESC")


def set_suggestion_estado(sid: int, estado: str) -> None:
    get_db().execute("UPDATE suggestions SET estado = ? WHERE id = ?", (estado, sid))


def add_help_request(rid: str | None, motivo: str, origen: str) -> int:
    return get_db().execute("INSERT INTO help_requests (ts, resident_id, motivo, origen) VALUES (?,?,?,?)",
                            (ts_str(), rid, motivo, origen))


def list_help_requests(open_only: bool = False) -> list[dict[str, Any]]:
    sql = ("SELECT h.*, r.nombre_preferido, r.habitacion FROM help_requests h LEFT JOIN residents r ON r.id = h.resident_id "
           + ("WHERE h.estado = 'abierta' " if open_only else "") + "ORDER BY h.id DESC")
    return get_db().query(sql)


def attend_help(hid: int) -> None:
    get_db().execute("UPDATE help_requests SET estado = 'atendida', atendida_ts = ? WHERE id = ?", (ts_str(), hid))


def mark_reminder_delivered(rid: str, event_id: int) -> None:
    get_db().execute("INSERT OR IGNORE INTO delivered_reminders (resident_id, event_id, fecha, ts) VALUES (?,?,?,?)",
                     (rid, event_id, today_str(), ts_str()))


def delivered_today(rid: str) -> set[int]:
    return {r["event_id"] for r in get_db().query(
        "SELECT event_id FROM delivered_reminders WHERE resident_id = ? AND fecha = ?", (rid, today_str()))}


def metrics() -> dict[str, Any]:
    db = get_db()
    rec = {r["resultado"]: r["n"] for r in db.query(
        "SELECT resultado, COUNT(*) AS n FROM recognition_events GROUP BY resultado")}
    temas = db.query("SELECT tema, COUNT(*) AS n FROM interactions WHERE tema IS NOT NULL AND canal != 'sistema' "
                     "GROUP BY tema ORDER BY n DESC LIMIT 8")
    fb = {r["valor"]: r["n"] for r in db.query("SELECT valor, COUNT(*) AS n FROM feedback GROUP BY valor")}
    lat = db.one("SELECT AVG(latencia_ms) AS ms FROM interactions WHERE latencia_ms IS NOT NULL")
    return {
        "interacciones": db.one("SELECT COUNT(*) AS n FROM interactions WHERE canal != 'sistema'")["n"],
        "sesiones": db.one("SELECT COUNT(DISTINCT session_id) AS n FROM interactions")["n"],
        "reconocimiento": rec,
        "temas": temas,
        "derivaciones": db.one("SELECT COUNT(*) AS n FROM help_requests")["n"],
        "derivaciones_abiertas": db.one("SELECT COUNT(*) AS n FROM help_requests WHERE estado = 'abierta'")["n"],
        "feedback": fb,
        "sugerencias": db.one("SELECT COUNT(*) AS n FROM suggestions")["n"],
        "latencia_media_ms": int(lat["ms"]) if lat and lat["ms"] else None,
    }
