"""Servidor del tótem: API del kiosco + panel de administración.

Arranque: `uvicorn wecare.main:app --host 127.0.0.1 --port 8000`
"""

from __future__ import annotations

import base64
import io
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import clock, seed, store, tools
from .config import settings
from .db import get_db
from .identity import IdentityService, build_engine
from .orchestrator import Orchestrator, Reply
from .sessions import Session, SessionStore
from .voice import VoiceService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("wecare")

BASE = Path(__file__).resolve().parent


class Services:
    identity: IdentityService
    orchestrator: Orchestrator
    voice: VoiceService
    sessions: SessionStore


svc = Services()


@asynccontextmanager
async def lifespan(_: FastAPI):
    db = get_db()
    if settings.demo_reset_on_start or not db.get_setting("seeded_on"):
        seed.seed()
        log.info("Datos de demo sembrados")
    svc.identity = IdentityService(build_engine())
    svc.orchestrator = Orchestrator()
    svc.voice = VoiceService()
    svc.sessions = SessionStore()
    log.info("Facial: %s | Claude: %s | Voz: %s", svc.identity.available, svc.orchestrator.enabled, svc.voice.enabled)
    yield


app = FastAPI(title="WeCare · Tótem", version="0.1.0", docs_url="/api/docs", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


def status_flags() -> dict[str, Any]:
    return {
        "institucion": settings.institution_name,
        "facial": svc.identity.available,
        "claude": svc.orchestrator.enabled,
        "voz": svc.voice.enabled,
        "hora_demo": clock.is_overridden(),
        "ahora": store.now_str(),
        "galeria": svc.identity.gallery_size(),
    }


# =============================================================================== KIOSCO

@app.get("/", response_class=HTMLResponse)
def kiosk(request: Request):
    residentes = [{"id": r["id"], "nombre": r["nombre_preferido"]} for r in store.list_residents()]
    return templates.TemplateResponse(request, "kiosk.html", {
        "flags": status_flags(), "residentes": residentes, "settings": settings})


def _session_payload(s: Session | None) -> dict[str, Any] | None:
    if s is None:
        return None
    r = store.get_resident(s.resident_id)
    return {
        "id": s.id, "resident_id": s.resident_id, "via": s.via,
        "nombre": r["nombre_preferido"] if r else None,
        "tratamiento": r["tratamiento"] if r else "vos",
        "tamano_texto": r["tamano_texto"] if r else "grande",
        "ritmo": r["ritmo"] if r else "normal",
        "privacy_mode": s.privacy_mode and not s.privacy_confirmed,
        "n_faces": s.n_faces,
    }


def _home(s: Session) -> dict[str, Any]:
    """Pantalla de inicio determinista (sin modelo): saludo + tarjetas de agenda + visita si corresponde."""
    r = store.get_resident(s.resident_id)
    agenda = [e for e in store.list_events(resident_id=s.resident_id, scope="relevante" if r else "general")
              if e["tipo"] not in ("visita", "salida")]
    cards = [{"hora": e["hora"], "titulo": e["titulo"], "lugar": e.get("lugar"), "en_curso": e["en_curso"],
              "participa": e["participa"]} for e in agenda[:3]]
    visita = None
    if r and not (s.privacy_mode and not s.privacy_confirmed):
        personales = store.list_events(resident_id=s.resident_id, scope="personal", tipos=["visita", "salida"])
        if personales:
            e = personales[0]
            visita = {"id": e["id"], "hora": e["hora"], "titulo": e["titulo"], "lugar": e.get("lugar"),
                      "estado": e["estado_label"], "minutos": e["minutos_para"],
                      "con_quien": e["visitante"] if e.get("mostrar_visitante") else None}
            store.mark_reminder_delivered(s.resident_id, e["id"])
            s.delivered.add(e["id"])
    menu = store.get_menu(servicio=store.next_service(), resident_id=s.resident_id)
    return {
        "saludo": (f"Hola {r['nombre_preferido']}, {clock.saludo()}" if r else f"Hola, {clock.saludo()}"),
        "hora": clock.fmt_hora(clock.now()),
        "agenda": cards,
        "visita": visita,
        "proximo_servicio": {"servicio": menu[0]["servicio"], "plato": menu[0]["plato"]} if menu else None,
        "privacy_mode": s.privacy_mode and not s.privacy_confirmed,
    }


def _reply_payload(reply: Reply) -> dict[str, Any]:
    return {"text": reply.text, "cards": reply.cards, "tools": reply.tools_used, "derivado": reply.derivado,
            "privacy_prompt": reply.privacy_prompt, "latency_ms": reply.latency_ms, "error": reply.error}


@app.get("/api/status")
def api_status():
    return status_flags()


@app.post("/api/detect")
async def api_detect(image: UploadFile = File(...)):
    """Sólo diagnóstico: qué ve la cámara (sin abrir sesión ni registrar nada)."""
    res = svc.identity.observe(await image.read())
    return {"n_faces": res.n_faces, "face_box": res.face_box, "candidate_id": res.candidate_id,
            "score": res.score, "qr": bool(res.qr_resident_id), "facial": svc.identity.available}


@app.post("/api/observe")
async def api_observe(kiosk_id: str = Form("kiosk-1"), session_id: str | None = Form(None),
                      image: UploadFile = File(...)):
    data = await image.read()
    res = svc.identity.observe(data)
    s = svc.sessions.get(session_id) or svc.sessions.for_kiosk(kiosk_id)
    started = False
    if s is not None:
        s.update_presence(res.n_faces)
    else:
        if res.qr_resident_id:
            s = svc.sessions.start(kiosk_id, res.qr_resident_id, via="qr")
            store.log_recognition("qr", res.qr_resident_id, None, res.n_faces)
            started = True
        elif svc.sessions.track_candidate(kiosk_id, res.candidate_id):
            # Misma persona en N cuadros seguidos: identidad confirmada.
            s = svc.sessions.start(kiosk_id, res.candidate_id, via="facial")
            store.log_recognition("reconocido", res.candidate_id, res.score, res.n_faces)
            started = True
        if s is not None:
            s.update_presence(res.n_faces)
    return {"n_faces": res.n_faces, "face_box": res.face_box, "candidate": bool(res.candidate_id),
            "score": res.score, "session": _session_payload(s), "started": started}


class StartBody(BaseModel):
    kiosk_id: str = "kiosk-1"
    mode: str  # general | pin
    resident_id: str | None = None
    pin: str | None = None
    motivo: str | None = None  # no_reconocido | eleccion


@app.post("/api/session/start")
def api_session_start(body: StartBody):
    if body.mode == "pin":
        if not body.resident_id or not store.verify_pin(body.resident_id, body.pin or ""):
            store.log_recognition("pin_incorrecto", body.resident_id)
            raise HTTPException(401, "PIN incorrecto")
        s = svc.sessions.start(body.kiosk_id, body.resident_id, via="pin")
        store.log_recognition("manual", body.resident_id)
    elif body.mode == "general":
        s = svc.sessions.start(body.kiosk_id, None, via="general")
        store.log_recognition("no_reconocido" if body.motivo == "no_reconocido" else "general")
    else:
        raise HTTPException(400, "modo inválido")
    return {"session": _session_payload(s)}


def _get_session(sid: str) -> Session:
    s = svc.sessions.get(sid)
    if s is None:
        raise HTTPException(404, "La sesión terminó")
    return s


@app.post("/api/session/{sid}/open")
def api_session_open(sid: str):
    s = _get_session(sid)
    home = _home(s)
    reply = svc.orchestrator.open(s)
    return {"home": home, "reply": _reply_payload(reply), "session": _session_payload(s)}


@app.get("/api/session/{sid}/home")
def api_session_home(sid: str):
    s = _get_session(sid)
    return {"home": _home(s), "session": _session_payload(s)}


class ChatBody(BaseModel):
    text: str
    canal: str = "texto"


@app.post("/api/session/{sid}/chat")
def api_session_chat(sid: str, body: ChatBody):
    s = _get_session(sid)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "texto vacío")
    reply = svc.orchestrator.chat(s, text[:1000], canal=body.canal)
    return {"reply": _reply_payload(reply), "session": _session_payload(s)}


class HelpBody(BaseModel):
    motivo: str = "Tocó el botón 'Necesito ayuda'"


@app.post("/api/session/{sid}/help")
def api_session_help(sid: str, body: HelpBody):
    s = _get_session(sid)
    hid = store.add_help_request(s.resident_id, body.motivo, origen="boton")
    store.log_interaction(s.id, s.resident_id, "touch", "[BOTÓN AYUDA]", "Aviso enviado a recepción",
                          ["pedir_ayuda"], tema="ayuda", derivado=True)
    return {"ok": True, "numero": hid,
            "card": {"type": "ayuda", "titulo": "Ya avisamos al equipo", "texto": "Alguien viene a ayudarte en un momento."}}


@app.post("/api/session/{sid}/end")
def api_session_end(sid: str):
    svc.sessions.end(sid)
    return {"ok": True}


@app.post("/api/asr")
async def api_asr(audio: UploadFile = File(...)):
    if not svc.voice.enabled:
        raise HTTPException(503, "Voz no configurada")
    data = await audio.read()
    try:
        text = svc.voice.asr(data)
    except Exception as e:  # noqa: BLE001
        log.exception("ASR")
        raise HTTPException(502, f"No pude transcribir: {e}")
    return {"text": text}


class TTSBody(BaseModel):
    text: str
    session_id: str | None = None


@app.post("/api/tts")
def api_tts(body: TTSBody):
    if not svc.voice.enabled:
        raise HTTPException(503, "Voz no configurada")
    s = svc.sessions.get(body.session_id)
    r = store.get_resident(s.resident_id) if s else None
    speed = 0.9 if (r and r["ritmo"] == "pausado") else None
    voice = r["voz_reference_id"] if r and r.get("voz_reference_id") else None
    try:
        audio = svc.voice.tts(body.text[:1500], reference_id=voice, speed=speed)
    except Exception as e:  # noqa: BLE001
        log.exception("TTS")
        raise HTTPException(502, f"No pude sintetizar: {e}")
    return Response(content=audio, media_type="audio/mpeg")


# =============================================================================== ADMIN

def _render(request: Request, name: str, **ctx: Any):
    ctx.update({"flags": status_flags(), "settings": settings, "hoy": store.today_str(),
                "ahora": clock.now(), "active": name.split(".")[0]})
    return templates.TemplateResponse(request, f"admin/{name}", ctx)


@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request):
    return _render(request, "dashboard.html", metrics=store.metrics(), ayuda=store.list_help_requests(open_only=True),
                   sesiones=[_session_payload(s) for s in svc.sessions.active()])


@app.post("/admin/reloj")
def admin_clock(hora: str = Form(""), accion: str = Form("fijar")):
    if accion == "real" or not hora:
        clock.set_demo_time(None)
    else:
        clock.set_demo_time(clock.parse_local(hora))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/reset")
def admin_reset():
    seed.seed()
    svc.identity.reload_gallery()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/ayuda/{hid}/atender")
def admin_attend(hid: int):
    store.attend_help(hid)
    return RedirectResponse("/admin", status_code=303)


@app.get("/admin/residentes", response_class=HTMLResponse)
def admin_residents(request: Request):
    return _render(request, "residentes.html", residentes=store.list_residents(active_only=False))


@app.get("/admin/residentes/{rid}", response_class=HTMLResponse)
def admin_resident(request: Request, rid: str):
    r = store.get_resident(rid) or {"id": rid}
    r["n_embeddings"] = store.count_embeddings(rid)
    return _render(request, "residente.html", r=r, nuevo=rid == "nuevo")


@app.post("/admin/residentes/{rid}")
async def admin_resident_save(request: Request, rid: str):
    form = dict(await request.form())
    data = {k: form.get(k) for k in form}
    data["id"] = (form.get("id") or rid).strip().lower()
    if not data["id"] or data["id"] == "nuevo":
        raise HTTPException(400, "Falta el identificador")
    data["activo"] = form.get("activo") == "on"
    data["consentimiento_facial"] = form.get("consentimiento_facial") == "on"
    store.upsert_resident(data)
    svc.identity.reload_gallery()
    return RedirectResponse(f"/admin/residentes/{data['id']}", status_code=303)


@app.post("/admin/residentes/{rid}/enrolar")
async def admin_enroll(rid: str, foto: UploadFile | None = File(None), captura: str = Form("")):
    if not store.get_resident(rid):
        raise HTTPException(404)
    if captura.startswith("data:image"):
        data = base64.b64decode(captura.split(",", 1)[1])
        fuente = "camara_panel"
    elif foto is not None:
        data = await foto.read()
        fuente = "foto"
    else:
        raise HTTPException(400, "Falta la imagen")
    photos = settings.photos_dir / rid
    photos.mkdir(parents=True, exist_ok=True)
    photo = photos / f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.jpg"
    photo.write_bytes(data)
    res = svc.identity.enroll(rid, data, fuente, photo_path=str(photo))
    if not res.get("ok"):
        photo.unlink(missing_ok=True)
        return JSONResponse(res, status_code=422)
    return res


def _remove_photo(row: dict[str, Any] | None) -> None:
    if row and row.get("photo_path"):
        try:
            Path(row["photo_path"]).unlink(missing_ok=True)
        except OSError:
            log.warning("No pude borrar la foto %s", row["photo_path"])


@app.get("/admin/residentes/{rid}/plantillas")
def admin_templates(rid: str):
    return {"plantillas": [{"id": e["id"], "fuente": e["fuente"], "creado": e["creado"],
                            "foto": bool(e["photo_path"] and Path(e["photo_path"]).exists())}
                           for e in store.list_embeddings(rid)]}


@app.get("/admin/plantillas/{eid}/foto")
def admin_template_photo(eid: int):
    row = store.get_embedding(eid)
    if not row or not row.get("photo_path") or not Path(row["photo_path"]).exists():
        raise HTTPException(404)
    return Response(Path(row["photo_path"]).read_bytes(), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.post("/admin/plantillas/{eid}/borrar")
def admin_template_delete(eid: int):
    row = store.delete_embedding(eid)
    _remove_photo(row)
    svc.identity.reload_gallery()
    return {"ok": True, "n_embeddings": store.count_embeddings(row["resident_id"]) if row else 0}


@app.post("/admin/residentes/{rid}/borrar-plantillas")
def admin_delete_templates(rid: str):
    for row in store.delete_embeddings(rid):
        _remove_photo(row)
    svc.identity.reload_gallery()
    return RedirectResponse(f"/admin/residentes/{rid}", status_code=303)


@app.get("/admin/residentes/{rid}/qr.png")
def admin_qr(rid: str):
    import segno
    r = store.get_resident(rid)
    if not r or not r.get("pin"):
        raise HTTPException(404, "El residente no tiene PIN")
    buf = io.BytesIO()
    segno.make(IdentityService.qr_payload(rid, r["pin"]), error="m").save(buf, kind="png", scale=8, border=2)
    return Response(buf.getvalue(), media_type="image/png")


@app.get("/admin/agenda", response_class=HTMLResponse)
def admin_agenda(request: Request, fecha: str | None = None):
    fecha = fecha or store.today_str()
    eventos = store.list_events(fecha=fecha, scope="general", incluir_pasados=True, incluir_cancelados=True)
    personales = get_db().query("SELECT e.*, r.nombre_preferido FROM events e JOIN residents r ON r.id = e.resident_id "
                                "WHERE date(e.inicio) = ? ORDER BY e.inicio", (fecha,))
    return _render(request, "agenda.html", fecha=fecha, eventos=eventos, personales=personales,
                   residentes=store.list_residents())


@app.post("/admin/agenda")
async def admin_agenda_save(request: Request):
    form = await request.form()
    data = {k: form.get(k) for k in ("id", "titulo", "tipo", "lugar", "estado", "detalle", "visitante", "punto_encuentro", "fuente")}
    data["resident_id"] = form.get("resident_id") or None
    data["inicio"] = f"{form.get('fecha')}T{form.get('hora_inicio')}"
    data["fin"] = f"{form.get('fecha')}T{form.get('hora_fin')}" if form.get("hora_fin") else None
    data["mostrar_visitante"] = form.get("mostrar_visitante") == "on"
    data["avisar_equipo"] = form.get("avisar_equipo") == "on"
    data["participantes"] = form.getlist("participantes")
    if not data["titulo"] or not form.get("hora_inicio"):
        raise HTTPException(400, "Faltan título u hora")
    store.upsert_event(data)
    return RedirectResponse(f"/admin/agenda?fecha={form.get('fecha')}", status_code=303)


@app.post("/admin/agenda/{eid}/estado")
def admin_agenda_estado(eid: int, estado: str = Form(...), fecha: str = Form("")):
    e = store.get_event(eid)
    if e:
        e["estado"] = estado
        e["fuente"] = "panel"
        store.upsert_event(e)
    return RedirectResponse(f"/admin/agenda?fecha={fecha or store.today_str()}", status_code=303)


@app.post("/admin/agenda/{eid}/borrar")
def admin_agenda_delete(eid: int, fecha: str = Form("")):
    store.delete_event(eid)
    return RedirectResponse(f"/admin/agenda?fecha={fecha or store.today_str()}", status_code=303)


@app.get("/admin/menu", response_class=HTMLResponse)
def admin_menu(request: Request, fecha: str | None = None):
    fecha = fecha or store.today_str()
    return _render(request, "menu.html", fecha=fecha, items=store.list_menu(fecha), servicios=store.SERVICIOS,
                   residentes=store.list_residents())


@app.post("/admin/menu")
def admin_menu_save(fecha: str = Form(...), servicio: str = Form(...), plato: str = Form(...),
                    resident_id: str = Form("")):
    store.upsert_menu(fecha, servicio, plato.strip(), resident_id or None, fuente="panel")
    return RedirectResponse(f"/admin/menu?fecha={fecha}", status_code=303)


@app.post("/admin/menu/{mid}/borrar")
def admin_menu_delete(mid: int, fecha: str = Form("")):
    store.delete_menu(mid)
    return RedirectResponse(f"/admin/menu?fecha={fecha or store.today_str()}", status_code=303)


@app.get("/admin/registro", response_class=HTMLResponse)
def admin_log(request: Request):
    return _render(request, "registro.html", interacciones=store.list_interactions(150),
                   reconocimientos=get_db().query("SELECT * FROM recognition_events ORDER BY id DESC LIMIT 100"),
                   feedback=store.list_feedback())


@app.get("/admin/sugerencias", response_class=HTMLResponse)
def admin_suggestions(request: Request):
    return _render(request, "sugerencias.html", sugerencias=store.list_suggestions(), ayuda=store.list_help_requests())


@app.post("/admin/sugerencias/{sid}/estado")
def admin_suggestion_state(sid: int, estado: str = Form(...)):
    store.set_suggestion_estado(sid, estado)
    return RedirectResponse("/admin/sugerencias", status_code=303)


@app.get("/admin/voz/probar")
def admin_voice_check():
    return svc.voice.check()


@app.get("/admin/asistente/probar")
def admin_assistant_check():
    return svc.orchestrator.check()
