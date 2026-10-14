"""Orquestador conversacional sobre la API de Claude.

El modelo no recibe la base de datos: recibe el perfil autorizado de la persona,
un resumen del momento (hora, próximos eventos) y herramientas. Todo lo que
afirma sobre agenda, menú o visitas sale de una herramienta; las reglas de
cuidado viven en el system prompt y se verifican en los tests.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from . import clock, store, tools
from .config import settings
from .sessions import Session

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6

SYSTEM_PROMPT = """Sos el asistente del tótem de {institucion}, una residencia de adultos mayores. Hablás en castellano rioplatense (voseo), con calidez y respeto, como una persona del equipo que acompaña. No sos un médico ni un cuidador: acompañás, informás y facilitás; nunca reemplazás al equipo humano.

QUIÉN TE HABLA
Te habla un residente de la residencia (o una persona no identificada, en modo general). Puede tener dificultades de memoria, audición o atención. Tu texto se lee en una pantalla grande y se convierte a voz, así que escribí como se habla: frases cortas, una idea por frase, sin listas con viñetas, sin markdown, sin emojis, sin paréntesis largos.

REGLAS QUE NO SE NEGOCIAN
1. No inventes nada. Actividades, horarios, visitas, menú y recuerdos salen únicamente de las herramientas. Si una herramienta no lo tiene, decí con naturalidad que no lo tenés anotado y ofrecé consultar al equipo. Jamás nombres personas, familiares o visitas que no estén en los datos.
2. No diagnosticás, no interpretás síntomas, no opinás sobre medicación, dietas ni cuidados, ni aunque te lo pidan. Ante dolor, malestar, angustia, caída, confusión, pedidos de remedios o cualquier señal de riesgo: llamá a pedir_ayuda, decile que ya viene alguien y acompañá con calma. No des indicaciones médicas de ningún tipo.
3. No recomendás cambios de comida. Si existe una alternativa habilitada por cocina, podés mencionarla porque viene en los datos; si no, derivá la consulta a cocina con registrar_sugerencia o al equipo.
4. Privacidad: si el contexto indica que hay otras personas cerca, no menciones visitas, salidas ni datos personales hasta que la persona confirme que quiere verlos igual (entonces usá confirmar_mostrar_datos_personales). En modo general (persona no identificada) sólo compartís agenda y menú generales, nunca datos de ningún residente, y no adivinás quién es.
5. Distinguí lo confirmado de lo tentativo: si algo está "a confirmar", decilo tal cual.
6. Nunca enumeres cosas que ya pasaron salvo que la persona te pregunte por ellas.

CÓMO CONVERSÁS
- Usá el nombre preferido de la persona y el tratamiento indicado en su perfil (vos o usted). Adaptá el largo y el ritmo a sus preferencias: si prefiere frases breves, máximo dos o tres frases por turno.
- Respondé primero lo que te preguntaron; después, como mucho, una pregunta corta. No hagas más de dos preguntas seguidas: después ofrecé un cierre natural ("Cuando quieras, acá estoy").
- Si cambia de tema, cambiá vos también, sin volver a lo anterior.
- No conviertas la charla en una evaluación ni en un cuestionario. Los intereses de la persona sirven para conversar, no para indagar.
- Si no entendiste, pedí que te lo repita con amabilidad; no supongas.
- Orientá en el tiempo con referencias simples: "falta una hora", "después de merendar".
- Al terminar una charla sobre una actividad, si la persona dijo espontáneamente si le gustó o no, registralo con registrar_opinion_actividad. Si no quiso responder, también podés registrarlo como no_respondio. Nunca insistas.

APERTURA DE LA CONVERSACIÓN
Cuando recibas el mensaje [APERTURA], saludá según el momento del día usando el nombre preferido, y elegí UNA sola puerta de entrada según el contexto que te dan:
- Si hoy tiene una visita o salida y todavía no se le recordó, recordásela con el horario y preguntale si quiere saber cuánto falta o dónde es.
- Si no, y participó de una actividad en los últimos días sobre la que todavía no opinó, abrí con eso ("Ayer tenías streaming. ¿Cómo te fue?") y seguí con una o dos preguntas como mucho.
- Si no, resumí sus próximos dos o tres eventos del día y preguntale si quiere que le repitas algún horario o ver la agenda completa.
En modo general, saludá sin nombre, decí que podés contar la agenda y el menú del día, y ofrecé la identificación con tarjeta o PIN si quiere ver su información personal.
Si el perfil dice que prefiere frases breves, la apertura entera cabe en tres frases.
"""


@dataclass
class Reply:
    text: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    derivado: bool = False
    privacy_prompt: bool = False
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    latency_ms: int = 0


def _tema(tools_used: list[str], text: str) -> str:
    for t, tema in (("pedir_ayuda", "ayuda"), ("consultar_visitas_y_salidas", "visitas"),
                    ("consultar_menu", "menu"), ("registrar_sugerencia", "sugerencia"),
                    ("registrar_opinion_actividad", "opinion_actividad"), ("consultar_agenda", "agenda"),
                    ("consultar_actividades_recientes", "actividades")):
        if t in tools_used:
            return tema
    return "apertura" if text == "[APERTURA]" else "conversacion"


def build_context(session: Session) -> str:
    """Bloque de contexto por turno: quién es, qué hora es y qué hay en el día (sólo datos autorizados)."""
    ahora = clock.now()
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    lines = [f"Hora actual: {clock.fmt_hora(ahora)} ({clock.momento_del_dia(ahora)}), "
             f"{dias[ahora.weekday()]} {ahora.day}/{ahora.month}."]
    r = store.get_resident(session.resident_id)
    if not r:
        lines.append("Modo general: persona NO identificada. Sólo agenda y menú institucionales; sin datos personales.")
        return "\n".join(lines)

    lines.append(f"Persona identificada (vía {session.via}). Nombre preferido: {r['nombre_preferido']}. "
                 f"Tratamiento: {r['tratamiento']}. Ritmo: {r['ritmo']}. Nivel de detalle: {r['nivel_detalle']}. "
                 f"Tono: {r['tono']}.")
    if r.get("intereses"):
        lines.append(f"Intereses autorizados para conversar: {r['intereses']}.")
    if r.get("accesibilidad"):
        lines.append(f"Accesibilidad: {r['accesibilidad']}.")
    if r.get("apoyo_permitido"):
        lines.append(f"Apoyo permitido: {r['apoyo_permitido']}.")

    # Resumen del día (lo que falta) para que la apertura no necesite herramientas.
    proximos = [e for e in store.list_events(resident_id=session.resident_id)
                if e["tipo"] not in ("visita", "salida")][:3]
    if proximos:
        lines.append("Próximos eventos de hoy para esta persona: " + "; ".join(
            f"{e['hora']} {e['titulo']}" + (f" ({e['lugar']})" if e.get("lugar") else "") for e in proximos) + ".")
    else:
        lines.append("No le queda nada en la agenda de hoy.")

    delivered = store.delivered_today(session.resident_id) | session.delivered
    personales = store.list_events(resident_id=session.resident_id, scope="personal", tipos=["visita", "salida"])
    if session.privacy_mode and not session.privacy_confirmed:
        lines.append("ATENCIÓN: hay otras personas cerca del tótem. No menciones visitas, salidas ni datos personales "
                     "sin que la persona confirme que quiere verlos igual.")
        if personales:
            lines.append("(Tiene información personal pendiente de mostrar, oculta por privacidad.)")
    else:
        for e in personales:
            quien = f" con {e['visitante']}" if e.get("visitante") and e.get("mostrar_visitante") else ""
            estado = "" if e["estado"] == "confirmado" else f" [{e['estado_label']}]"
            ya = " (ya se le recordó hoy)" if e["id"] in delivered else " (todavía NO se le recordó)"
            lines.append(f"Hoy tiene {e['tipo']}: {e['titulo']}{quien} a las {e['hora']}"
                         + (f" en {e['lugar']}" if e.get("lugar") else "") + f"{estado}{ya}.")
    recientes = store.recent_activities(session.resident_id, dias=2)
    if recientes:
        lines.append("Actividades recientes: " + "; ".join(
            f"{a['cuando']} {a['titulo']} (event_id {a['id']}, opinión: {a['feedback'] or 'todavía no dijo'})"
            for a in recientes[:3]) + ".")
    return "\n".join(lines)


class Orchestrator:
    def __init__(self, client: Any = None):
        self._client = client
        if self._client is None and settings.claude_enabled:
            import anthropic
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def _system(self, session: Session) -> list[dict[str, Any]]:
        return [
            {"type": "text", "text": SYSTEM_PROMPT.format(institucion=settings.institution_name),
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "CONTEXTO DE ESTA SESIÓN\n" + build_context(session)},
        ]

    def _call(self, session: Session, messages: list[dict[str, Any]]):
        return self._client.beta.messages.create(
            model=settings.claude_model,
            max_tokens=settings.claude_max_tokens,
            system=self._system(session),
            messages=messages,
            tools=tools.TOOLS,
            output_config={"effort": settings.claude_effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

    def check(self) -> dict[str, Any]:
        """Llamada mínima para diagnosticar credenciales, red y formato de la petición desde el panel."""
        if not self.enabled:
            return {"ok": False, "error": "ANTHROPIC_API_KEY no configurada en .env"}
        try:
            t0 = time.time()
            resp = self._client.beta.messages.create(
                model=settings.claude_model, max_tokens=64,
                system="Respondé en una sola frase corta en castellano rioplatense.",
                messages=[{"role": "user", "content": "Decí hola."}],
                output_config={"effort": settings.claude_effort},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
            text = " ".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
            return {"ok": True, "modelo": resp.model, "respuesta": text, "ms": int((time.time() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001
            detail = getattr(e, "message", None) or str(e)
            body = getattr(e, "body", None)
            return {"ok": False, "tipo": type(e).__name__, "status": getattr(e, "status_code", None),
                    "error": detail, "detalle": body}

    def open(self, session: Session) -> Reply:
        session.opened = True
        return self.chat(session, "[APERTURA]", canal="sistema")

    def chat(self, session: Session, text: str, canal: str = "texto") -> Reply:
        t0 = time.time()
        if not self.enabled:
            return self._fallback_reply(session, text, canal)
        session.touch()
        session.turns += 1
        history = session.messages
        history.append({"role": "user", "content": text})
        reply = Reply(text="")
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                resp = self._call(session, history)
                reply.usage["in"] = reply.usage.get("in", 0) + (resp.usage.input_tokens or 0)
                reply.usage["out"] = reply.usage.get("out", 0) + (resp.usage.output_tokens or 0)
                content = [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b for b in resp.content]
                history.append({"role": "assistant", "content": content})

                if resp.stop_reason == "refusal":
                    reply.text = "Eso no lo puedo responder yo. Si querés, aviso a alguien del equipo para que te ayude."
                    break
                tool_uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
                texts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
                if not tool_uses:
                    reply.text = "\n".join(t.strip() for t in texts if t.strip())
                    if resp.stop_reason == "max_tokens":
                        reply.text += "…"
                    break
                results = []
                for tu in tool_uses:
                    args = tu.input if isinstance(tu.input, dict) else json.loads(tu.input or "{}")
                    out, card = tools.execute(tu.name, args, session)
                    reply.tools_used.append(tu.name)
                    if card:
                        reply.cards.append(card)
                        if card["type"] == "ayuda":
                            reply.derivado = True
                        if card["type"] == "privacidad":
                            reply.privacy_prompt = True
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": out})
                history.append({"role": "user", "content": results})
            else:
                reply.text = reply.text or "Perdoná, me enredé. ¿Me lo repetís con otras palabras?"
        except Exception as e:  # noqa: BLE001 - el tótem nunca debe quedarse mudo
            log.exception("Fallo en la conversación")
            reply.error = f"{type(e).__name__}: {e}"
            reply.text = "Disculpá, en este momento no puedo responder. Si necesitás algo, tocá 'Necesito ayuda'."
            history.pop()  # no dejar un turno de usuario sin respuesta en el historial
        reply.latency_ms = int((time.time() - t0) * 1000)
        store.log_interaction(session.id, session.resident_id, canal, text, reply.text, reply.tools_used,
                              tema=_tema(reply.tools_used, text), privacidad=session.privacy_mode,
                              derivado=reply.derivado, tokens_in=reply.usage.get("in"),
                              tokens_out=reply.usage.get("out"), latencia_ms=reply.latency_ms)
        return reply

    # Sin API key: el tótem sigue siendo útil con respuestas armadas desde la base.
    def _fallback_reply(self, session: Session, text: str, canal: str) -> Reply:
        r = store.get_resident(session.resident_id)
        nombre = r["nombre_preferido"] if r else None
        low = text.lower()
        cards: list[dict[str, Any]] = []
        used: list[str] = []
        if text == "[APERTURA]":
            out, card = tools.execute("consultar_agenda", {"fecha": "hoy", "incluir_pasados": False}, session)
            used.append("consultar_agenda")
            items = json.loads(out).get("eventos", [])[:3]
            resumen = "; ".join(f"{i['hora']} {i['titulo']}" for i in items) or "no queda nada en la agenda"
            saludo = f"Hola {nombre}, {clock.saludo()}." if nombre else f"Hola, {clock.saludo()}."
            msg = f"{saludo} Hoy tenés: {resumen}. ¿Querés ver la agenda completa o el menú?"
            # Sin tarjeta: la pantalla de inicio ya muestra la agenda y la visita del día.
        elif any(k in low for k in ("com", "menú", "menu", "cena", "almuerzo", "desayun", "meriend")):
            out, card = tools.execute("consultar_menu", {"fecha": "hoy", "servicio": "proximo"}, session)
            used.append("consultar_menu")
            m = json.loads(out).get("menu", [])
            msg = (f"Para {m[0]['servicio']} hay {m[0]['plato']}." if m else "Cocina todavía no cargó el menú.")
            if card:
                cards.append(card)
        elif any(k in low for k in ("ayuda", "duele", "mal", "mareo", "caí", "cai")):
            out, card = tools.execute("pedir_ayuda", {"motivo": text}, session)
            used.append("pedir_ayuda")
            msg = "Ya avisé al equipo, en un momento viene alguien a acompañarte."
            cards.append(card)
        else:
            out, card = tools.execute("consultar_agenda", {"fecha": "hoy", "incluir_pasados": False}, session)
            used.append("consultar_agenda")
            items = json.loads(out).get("eventos", [])[:3]
            msg = "Te cuento lo que viene: " + ("; ".join(f"{i['hora']} {i['titulo']}" for i in items) or "nada más por hoy") + "."
            if card:
                cards.append(card)
        reply = Reply(text=msg, cards=cards, tools_used=used, derivado="pedir_ayuda" in used,
                      error="Asistente sin ANTHROPIC_API_KEY: respuestas básicas desde la base")
        store.log_interaction(session.id, session.resident_id, canal, text, msg, used, tema=_tema(used, text),
                              privacidad=session.privacy_mode, derivado=reply.derivado)
        return reply
