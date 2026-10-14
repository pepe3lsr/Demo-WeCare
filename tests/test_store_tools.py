"""Reglas de negocio verificables sin modelo: agenda relevante, privacidad, menú, recordatorios."""

import json

from wecare import store, tools
from wecare.sessions import Session


def ses(rid=None, via="pin"):
    return Session(id="s1", kiosk_id="k", resident_id=rid, via=via)


def agenda(session, **kw):
    out, card = tools.execute("consultar_agenda", {"fecha": "hoy", "incluir_pasados": False, **kw}, session)
    return json.loads(out), card


def test_agenda_solo_eventos_relevantes(db, at):
    at("09:00")
    titulos = [e["titulo"] for e in agenda(ses("roberto"))[0]["eventos"]]
    assert "Streaming: música de los 60" in titulos  # inscripto
    assert "Taller de arte" not in titulos  # no inscripto
    assert "Gimnasia suave" in titulos  # abierto a todos


def test_agenda_no_enumera_pasados_por_defecto(db, at):
    at("15:40")
    data, _ = agenda(ses("marta"))
    horas = [e["hora"] for e in data["eventos"]]
    assert "08:00" not in horas and "10:00" not in horas
    assert "15:00" in horas  # en curso se muestra
    data, _ = agenda(ses("marta"), incluir_pasados=True)
    assert "08:00" in [e["hora"] for e in data["eventos"]]


def test_modo_general_sin_datos_personales(db, at):
    at("09:00")
    data, _ = agenda(ses(None, "general"))
    assert all(e["tipo"] not in ("visita", "salida") for e in data["eventos"])
    out, _ = tools.execute("consultar_visitas_y_salidas", {"dias": 7}, ses(None, "general"))
    assert "identificarse" in json.loads(out)["nota"]


def test_privacidad_con_terceros_cerca(db, at):
    at("09:00")
    s = ses("elena")
    s.update_presence(2)
    out, card = tools.execute("consultar_visitas_y_salidas", {"dias": 7}, s)
    assert "aviso_privacidad" in json.loads(out) and card["type"] == "privacidad"
    data, _ = agenda(s)
    assert all(e["tipo"] != "visita" for e in data["eventos"]) and "aviso_privacidad" in data
    tools.execute("confirmar_mostrar_datos_personales", {}, s)
    out, _ = tools.execute("consultar_visitas_y_salidas", {"dias": 7}, s)
    items = json.loads(out)["visitas_y_salidas"]
    assert items[0]["con_quien"] == "Laura, su hija"
    s.update_presence(1)  # se queda sola: se limpia la confirmación
    assert not s.privacy_mode and not s.privacy_confirmed


def test_visitas_distinguen_confirmado_de_tentativo(db, at):
    at("09:00")
    out, _ = tools.execute("consultar_visitas_y_salidas", {"dias": 10}, ses("elena"))
    items = json.loads(out)["visitas_y_salidas"]
    estados = {i["tipo"]: i["estado"] for i in items}
    assert estados["visita"] == "confirmado" and estados["salida"] == "a confirmar"
    salida = next(i for i in items if i["tipo"] == "salida")
    assert salida["hay_que_avisar_al_equipo"] and salida["punto_de_encuentro"] == "Recepción"


def test_recordatorio_entregado_no_se_repite(db, at):
    at("09:00")
    s = ses("elena")
    tools.execute("consultar_visitas_y_salidas", {"dias": 1}, s)
    assert store.delivered_today("elena")  # quedó marcado como entregado


def test_cambios_de_agenda_se_reflejan_sin_reiniciar(db, at):
    at("09:00")
    visita = store.list_events(resident_id="elena", scope="personal", tipos=["visita"])[0]
    visita["estado"] = "cancelado"
    store.upsert_event(visita)
    out, _ = tools.execute("consultar_visitas_y_salidas", {"dias": 1}, ses("elena"))
    assert all(i["tipo"] != "visita" for i in json.loads(out)["visitas_y_salidas"])


def test_menu_proximo_servicio_y_alternativa(db, at):
    at("11:00")
    out, _ = tools.execute("consultar_menu", {"fecha": "hoy", "servicio": "proximo"}, ses("elena"))
    m = json.loads(out)["menu"][0]
    assert m["servicio"] == "almuerzo" and "alternativa_habilitada_por_cocina" in m
    out, _ = tools.execute("consultar_menu", {"fecha": "hoy", "servicio": "proximo"}, ses("marta"))
    assert "alternativa_habilitada_por_cocina" not in json.loads(out)["menu"][0]
    at("18:00")
    assert store.next_service() == "cena"


def test_actividades_recientes_y_opinion(db, at):
    at("10:00")
    out, _ = tools.execute("consultar_actividades_recientes", {}, ses("roberto"))
    acts = json.loads(out)["actividades_recientes"]
    assert acts[0]["cuando"] == "ayer" and acts[0]["opinion_registrada"] == "todavía no dijo"
    out, card = tools.execute("registrar_opinion_actividad",
                              {"event_id": acts[0]["event_id"], "valor": "le_gusto", "comentario": "me divertí"}, ses("roberto"))
    assert json.loads(out)["ok"] and card["type"] == "gracias"
    # Elena no participó: no puede opinar sobre ese evento
    out, _ = tools.execute("registrar_opinion_actividad", {"event_id": acts[0]["event_id"], "valor": "le_gusto", "comentario": ""}, ses("elena"))
    assert "error" in json.loads(out)


def test_sugerencia_y_ayuda_quedan_registradas(db):
    out, card = tools.execute("registrar_sugerencia", {"area": "cocina", "tipo": "sugerencia", "texto": "más fruta"}, ses("marta"))
    assert json.loads(out)["ok"] and store.list_suggestions()[0]["texto"] == "más fruta"
    out, card = tools.execute("pedir_ayuda", {"motivo": "me duele el pecho"}, ses("marta"))
    assert card["type"] == "ayuda" and store.list_help_requests(open_only=True)[0]["motivo"] == "me duele el pecho"


def test_pin_y_galeria(db):
    assert store.verify_pin("marta", "1111") and not store.verify_pin("marta", "9999")
    assert store.load_gallery() == {}  # sin fotos sembradas
