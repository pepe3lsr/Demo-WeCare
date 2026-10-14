"""Herramientas del asistente: la única vía por la que el modelo accede a datos.

Cada herramienta consulta o escribe en la base con el `resident_id` de la sesión
(nunca uno que proponga el modelo) y aplica las reglas de privacidad del momento.
Devuelve (texto_para_el_modelo, tarjeta_para_la_pantalla | None).
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from . import clock, store
from .sessions import Session

TOOLS: list[dict[str, Any]] = [
    {
        "name": "consultar_agenda",
        "description": ("Agenda del día de la residencia y, si la persona está identificada, sus talleres y "
                        "eventos personales. Por defecto devuelve sólo lo que falta; pedí incluir_pasados "
                        "únicamente si la persona pregunta por algo que ya ocurrió."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "'hoy', 'mañana' o una fecha YYYY-MM-DD."},
                "incluir_pasados": {"type": "boolean", "description": "Incluir eventos ya terminados."},
            },
            "required": ["fecha", "incluir_pasados"],
            "additionalProperties": False,
        },
    },
    {
        "name": "consultar_menu",
        "description": ("Menú institucional vigente. Usá servicio='proximo' salvo que la persona pregunte por "
                        "una comida concreta. Si hay una alternativa habilitada por cocina para la persona, "
                        "viene indicada; no inventes otras ni des consejos de dieta."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "'hoy', 'mañana' o YYYY-MM-DD."},
                "servicio": {"type": "string", "enum": ["proximo", "todos", "desayuno", "almuerzo", "merienda", "cena"]},
            },
            "required": ["fecha", "servicio"],
            "additionalProperties": False,
        },
    },
    {
        "name": "consultar_visitas_y_salidas",
        "description": ("Visitas familiares y salidas programadas de la persona identificada en los próximos días, "
                        "con estado (confirmado / a confirmar) y datos logísticos autorizados."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"dias": {"type": "integer", "minimum": 1, "maximum": 14,
                                    "description": "Horizonte en días a partir de hoy."}},
            "required": ["dias"],
            "additionalProperties": False,
        },
    },
    {
        "name": "confirmar_mostrar_datos_personales",
        "description": ("Llamala sólo cuando hay otras personas cerca y la persona dijo explícitamente que quiere "
                        "ver igual su información personal (visitas, salidas). Habilita mostrarla en esta sesión."),
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "consultar_actividades_recientes",
        "description": ("Talleres y actividades de los últimos días en los que participó la persona, y si ya "
                        "dijo qué le parecieron. Sirve para abrir una charla sin inventar recuerdos."),
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "registrar_opinion_actividad",
        "description": ("Guarda qué le pareció una actividad a la persona, sólo si lo dijo voluntariamente. "
                        "No es una evaluación clínica."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "integer"},
                "valor": {"type": "string", "enum": ["le_gusto", "no_le_gusto", "no_respondio"]},
                "comentario": {"type": "string", "description": "Palabras textuales breves, o cadena vacía."},
            },
            "required": ["event_id", "valor", "comentario"],
            "additionalProperties": False,
        },
    },
    {
        "name": "registrar_sugerencia",
        "description": ("Envía una sugerencia o un reclamo al área correspondiente. Si es algo urgente o de "
                        "salud, usá pedir_ayuda en lugar de esta herramienta."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "area": {"type": "string", "enum": ["cocina", "actividades", "general"]},
                "tipo": {"type": "string", "enum": ["sugerencia", "reclamo"]},
                "texto": {"type": "string"},
            },
            "required": ["area", "tipo", "texto"],
            "additionalProperties": False,
        },
    },
    {
        "name": "pedir_ayuda",
        "description": ("Avisa a recepción/cuidadores para que vengan. Usala ante cualquier malestar, dolor, "
                        "angustia, caída, confusión, pedido de ayuda, o dudas sobre medicación, salud o "
                        "cuidados. Vos no resolvés eso: lo derivás."),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"motivo": {"type": "string", "description": "Resumen breve y literal de lo que dijo."}},
            "required": ["motivo"],
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}


def _resolve_fecha(s: str) -> str:
    s = (s or "hoy").strip().lower()
    if s in ("hoy", "today", ""):
        return store.today_str()
    if s in ("mañana", "manana", "tomorrow"):
        return (clock.today() + timedelta(days=1)).isoformat()
    if s in ("ayer", "yesterday"):
        return (clock.today() - timedelta(days=1)).isoformat()
    return s[:10]


def _fecha_humana(iso: str) -> str:
    if iso == store.today_str():
        return "hoy"
    if iso == (clock.today() + timedelta(days=1)).isoformat():
        return "mañana"
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    from datetime import date
    d = date.fromisoformat(iso)
    return f"el {dias[d.weekday()]} {d.day}/{d.month}"


def _falta(minutos: int) -> str:
    if minutos < -5:
        return "ya pasó"
    if minutos <= 5:
        return "es ahora"
    if minutos < 60:
        return f"faltan {minutos} minutos"
    h, m = divmod(minutos, 60)
    if h < 24:
        return f"falta {h} hora{'s' if h > 1 else ''}" + (f" y {m} minutos" if m >= 10 else "")
    return f"faltan {h // 24} día{'s' if h // 24 > 1 else ''}"


def _event_public(e: dict[str, Any]) -> dict[str, Any]:
    out = {"id": e["id"], "hora": e["hora"], "titulo": e["titulo"], "tipo": e["tipo"], "lugar": e.get("lugar")}
    if e.get("hora_fin"):
        out["hasta"] = e["hora_fin"]
    if e.get("detalle"):
        out["detalle"] = e["detalle"]
    if e.get("pasado"):
        out["estado_temporal"] = "ya terminó"
    elif e.get("en_curso"):
        out["estado_temporal"] = "en curso"
    else:
        out["cuanto_falta"] = _falta(e["minutos_para"])
    if e.get("participa") and e.get("resident_id") is None:
        out["inscripta"] = True
    return out


def _event_personal(e: dict[str, Any]) -> dict[str, Any]:
    out = _event_public(e)
    out["fecha"] = _fecha_humana(e["fecha"])
    out["estado"] = e["estado_label"]
    if e.get("visitante") and e.get("mostrar_visitante"):
        out["con_quien"] = e["visitante"]
    if e.get("punto_encuentro"):
        out["punto_de_encuentro"] = e["punto_encuentro"]
    if e.get("avisar_equipo"):
        out["hay_que_avisar_al_equipo"] = True
    return out


def _privacy_block(session: Session) -> str | None:
    if session.privacy_mode and not session.privacy_confirmed:
        return ("Hay otras personas cerca del tótem. Por privacidad no se muestran visitas, salidas ni datos "
                "personales. Preguntale a la persona si quiere verlos igual; si dice que sí, llamá a "
                "confirmar_mostrar_datos_personales y volvé a consultar.")
    return None


# ----------------------------------------------------------------------------- ejecución

def execute(name: str, args: dict[str, Any], session: Session) -> tuple[str, dict[str, Any] | None]:
    rid = session.resident_id
    args = args or {}

    if name == "consultar_agenda":
        fecha = _resolve_fecha(args.get("fecha", "hoy"))
        incluir_pasados = bool(args.get("incluir_pasados", False))
        scope = "relevante" if rid else "general"
        events = store.list_events(fecha=fecha, resident_id=rid, scope=scope, incluir_pasados=incluir_pasados)
        privado = _privacy_block(session)
        items, ocultos = [], 0
        for e in events:
            if e["tipo"] in ("visita", "salida"):
                if privado:
                    ocultos += 1
                    continue
                items.append(_event_personal(e))
            else:
                items.append(_event_public(e))
        payload: dict[str, Any] = {"fecha": _fecha_humana(fecha), "hora_actual": clock.fmt_hora(clock.now()),
                                   "eventos": items}
        if ocultos:
            payload["aviso_privacidad"] = privado
        if not items:
            payload["nota"] = "No queda nada en la agenda para ese día." if not incluir_pasados else "No hay eventos."
        card = {"type": "agenda", "titulo": f"Agenda de {payload['fecha']}", "items": items[:6]}
        return json.dumps(payload, ensure_ascii=False), card

    if name == "consultar_menu":
        fecha = _resolve_fecha(args.get("fecha", "hoy"))
        servicio = args.get("servicio", "proximo")
        if servicio == "proximo":
            servicio = store.next_service() if fecha == store.today_str() else "desayuno"
        menu = store.get_menu(fecha, None if servicio == "todos" else servicio, resident_id=rid)
        items = []
        for m in menu:
            it = {"servicio": m["servicio"], "plato": m["plato"]}
            if m.get("alternativa"):
                it["alternativa_habilitada_por_cocina"] = m["alternativa"]
            items.append(it)
        payload = {"fecha": _fecha_humana(fecha), "menu": items,
                   "fuente": "menú institucional vigente"}
        if not items:
            payload["nota"] = "Cocina todavía no cargó el menú de ese día."
        card = {"type": "menu", "titulo": f"Menú de {payload['fecha']}", "items": items}
        return json.dumps(payload, ensure_ascii=False), card

    if name == "consultar_visitas_y_salidas":
        if not rid:
            return json.dumps({"nota": "En modo general no hay información personal. Para ver sus visitas, "
                                       "la persona debe identificarse."}, ensure_ascii=False), None
        privado = _privacy_block(session)
        if privado:
            return json.dumps({"aviso_privacidad": privado}, ensure_ascii=False), {"type": "privacidad"}
        dias = max(1, min(int(args.get("dias", 7)), 14))
        items = []
        for i in range(dias):
            fecha = (clock.today() + timedelta(days=i)).isoformat()
            for e in store.list_events(fecha=fecha, resident_id=rid, scope="personal", incluir_pasados=(i == 0),
                                       tipos=["visita", "salida"]):
                items.append(_event_personal(e))
                if not e["pasado"]:
                    store.mark_reminder_delivered(rid, e["id"])
                    session.delivered.add(e["id"])
        payload = {"visitas_y_salidas": items}
        if not items:
            payload["nota"] = "No hay visitas ni salidas programadas en esos días."
        card = {"type": "visitas", "titulo": "Visitas y salidas", "items": items}
        return json.dumps(payload, ensure_ascii=False), card

    if name == "confirmar_mostrar_datos_personales":
        session.privacy_confirmed = True
        return json.dumps({"ok": True, "nota": "La persona confirmó. Ya podés consultar sus datos personales."},
                          ensure_ascii=False), None

    if name == "consultar_actividades_recientes":
        if not rid:
            return json.dumps({"nota": "Sin persona identificada no hay historial."}, ensure_ascii=False), None
        acts = store.recent_activities(rid, dias=3)
        items = [{"event_id": a["id"], "titulo": a["titulo"], "cuando": a["cuando"], "hora": a["hora"],
                  "lugar": a.get("lugar"), "detalle": a.get("detalle"),
                  "opinion_registrada": a.get("feedback") or "todavía no dijo"} for a in acts]
        payload = {"actividades_recientes": items}
        if not items:
            payload["nota"] = "No hay actividades registradas en los últimos días."
        return json.dumps(payload, ensure_ascii=False), None

    if name == "registrar_opinion_actividad":
        if not rid:
            return json.dumps({"error": "Sin persona identificada no se registra opinión."}, ensure_ascii=False), None
        event_id = int(args.get("event_id", 0))
        ev = store.get_event(event_id)
        if not ev or rid not in (ev.get("participantes") or []) and ev.get("resident_id") != rid:
            return json.dumps({"error": "Ese evento no corresponde a la persona."}, ensure_ascii=False), None
        store.add_feedback(rid, event_id, args.get("valor", "no_respondio"), (args.get("comentario") or "")[:200] or None)
        return json.dumps({"ok": True}, ensure_ascii=False), {"type": "gracias", "titulo": "¡Gracias por contarme!"}

    if name == "registrar_sugerencia":
        texto = (args.get("texto") or "").strip()[:500]
        if not texto:
            return json.dumps({"error": "Falta el texto de la sugerencia."}, ensure_ascii=False), None
        sid = store.add_suggestion(rid, args.get("area", "general"), args.get("tipo", "sugerencia"), texto)
        return (json.dumps({"ok": True, "numero": sid, "nota": "Enviada al área correspondiente."}, ensure_ascii=False),
                {"type": "sugerencia", "titulo": "Sugerencia enviada", "texto": texto, "area": args.get("area", "general")})

    if name == "pedir_ayuda":
        motivo = (args.get("motivo") or "pedido de ayuda").strip()[:300]
        hid = store.add_help_request(rid, motivo, origen="asistente")
        return (json.dumps({"ok": True, "numero": hid,
                            "nota": "Aviso enviado a recepción. Decile que ya viene alguien del equipo y "
                                    "quedate acompañando con calma; no des indicaciones médicas."}, ensure_ascii=False),
                {"type": "ayuda", "titulo": "Ya avisamos al equipo", "texto": "Alguien viene a ayudarte en un momento."})

    return json.dumps({"error": f"Herramienta desconocida: {name}"}, ensure_ascii=False), None
