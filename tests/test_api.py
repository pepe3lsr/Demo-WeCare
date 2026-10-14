"""API del tótem de punta a punta (sin claves externas): PIN, modo general, ayuda, admin."""

import pytest
from fastapi.testclient import TestClient

from wecare import clock, store
from wecare.db import Database, set_db


@pytest.fixture()
def client():
    set_db(Database(":memory:"))
    from wecare.main import app
    with TestClient(app) as c:
        yield c
    clock.set_demo_time(None)
    set_db(None)


def test_paginas_cargan(client):
    for p in ["/", "/admin", "/admin/residentes", "/admin/residentes/marta", "/admin/agenda", "/admin/menu",
              "/admin/registro", "/admin/sugerencias"]:
        assert client.get(p).status_code == 200, p
    assert client.get("/admin/residentes/marta/qr.png").headers["content-type"] == "image/png"


def test_flujo_pin_apertura_chat_ayuda(client):
    assert client.post("/api/session/start", json={"mode": "pin", "resident_id": "elena", "pin": "0000"}).status_code == 401
    s = client.post("/api/session/start", json={"mode": "pin", "resident_id": "elena", "pin": "3333"}).json()["session"]
    assert s["nombre"] == "Elena" and s["via"] == "pin" and s["tratamiento"] == "usted"
    r = client.post(f"/api/session/{s['id']}/open").json()
    assert r["home"]["saludo"].startswith("Hola Elena") and r["reply"]["text"]
    r = client.post(f"/api/session/{s['id']}/chat", json={"text": "¿qué comemos hoy?"}).json()
    assert r["reply"]["cards"][0]["type"] == "menu"
    r = client.post(f"/api/session/{s['id']}/help", json={}).json()
    assert r["ok"] and store.list_help_requests(open_only=True)[0]["origen"] == "boton"
    assert client.post(f"/api/session/{s['id']}/end").json()["ok"]
    assert client.post(f"/api/session/{s['id']}/chat", json={"text": "hola"}).status_code == 404


def test_modo_general_no_expone_visitas(client):
    s = client.post("/api/session/start", json={"mode": "general", "motivo": "no_reconocido"}).json()["session"]
    r = client.post(f"/api/session/{s['id']}/open").json()
    assert r["home"]["visita"] is None and s["nombre"] is None
    assert store.metrics()["reconocimiento"]["no_reconocido"] == 1


def test_reloj_de_demo_cambia_la_agenda(client):
    hoy = store.today_str()
    client.post("/admin/reloj", data={"hora": f"{hoy}T15:40", "accion": "fijar"}, follow_redirects=False)
    s = client.post("/api/session/start", json={"mode": "pin", "resident_id": "marta", "pin": "1111"}).json()["session"]
    home = client.get(f"/api/session/{s['id']}/home").json()["home"]
    assert home["agenda"][0]["hora"] == "15:00" and home["proximo_servicio"]["servicio"] == "merienda"


def test_admin_edita_agenda_y_menu(client):
    hoy = store.today_str()
    r = client.post("/admin/agenda", data={"titulo": "Cine", "tipo": "institucional", "fecha": hoy, "hora_inicio": "19:00",
                                           "estado": "confirmado", "participantes": ["marta"]}, follow_redirects=False)
    assert r.status_code == 303
    assert any(e["titulo"] == "Cine" for e in store.list_events(resident_id="marta", incluir_pasados=True))
    assert not any(e["titulo"] == "Cine" for e in store.list_events(resident_id="roberto", incluir_pasados=True))
    client.post("/admin/menu", data={"fecha": hoy, "servicio": "cena", "plato": "Guiso"}, follow_redirects=False)
    assert store.get_menu(hoy, "cena")[0]["plato"] == "Guiso"


def test_voz_y_asr_sin_clave_devuelven_503(client):
    assert client.post("/api/tts", json={"text": "hola"}).status_code == 503
    assert client.post("/api/asr", files={"audio": ("a.wav", b"x", "audio/wav")}).status_code == 503
