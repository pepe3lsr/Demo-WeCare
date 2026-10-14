"""El orquestador con un cliente Claude simulado: bucle de herramientas, contexto y registro."""

import copy
from types import SimpleNamespace

import pytest

from wecare import orchestrator as orch
from wecare import store
from wecare.sessions import Session


class Block(SimpleNamespace):
    def model_dump(self, **_):
        return dict(self.__dict__)


def text(t):
    return Block(type="text", text=t)


def tool_use(name, inp, id_="tu1"):
    return Block(type="tool_use", name=name, input=inp, id=id_)


class FakeClaude:
    """Devuelve respuestas guionadas y guarda cada request para inspeccionarlo."""

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # copia profunda: el orquestador sigue mutando la lista de mensajes después de la llamada
        self.requests.append(copy.deepcopy(kwargs))
        content, stop = self.scripted.pop(0)
        return SimpleNamespace(content=content, stop_reason=stop,
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20))


def ses(rid="marta"):
    return Session(id="s1", kiosk_id="k", resident_id=rid, via="facial")


def test_apertura_usa_contexto_y_registra(db, at):
    at("09:00")
    fake = FakeClaude([([text("Hola Marta, buenos días. Hoy a las diez tenés gimnasia suave.")], "end_turn")])
    r = orch.Orchestrator(client=fake).open(ses())
    assert "Hola Marta" in r.text and not r.tools_used
    req = fake.requests[0]
    assert req["model"] and req["fallbacks"] == "default" and req["output_config"]["effort"]
    system = "\n".join(b["text"] for b in req["system"])
    assert "Marta" in system and "Gimnasia suave" in system and "no inventes" in system.lower()
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    log = store.list_interactions()[0]
    assert log["tema"] == "apertura" and log["resident_id"] == "marta" and log["canal"] == "sistema"


def test_bucle_de_herramientas_devuelve_tarjetas(db, at):
    at("11:00")
    fake = FakeClaude([
        ([tool_use("consultar_menu", {"fecha": "hoy", "servicio": "proximo"})], "tool_use"),
        ([text("Para el almuerzo hay milanesa al horno con puré de calabaza.")], "end_turn"),
    ])
    r = orch.Orchestrator(client=fake).chat(ses("marta"), "¿qué comemos hoy?", canal="voz")
    assert r.tools_used == ["consultar_menu"] and r.cards[0]["type"] == "menu"
    # el segundo request lleva el tool_result con el id correcto
    msgs = fake.requests[1]["messages"]
    assert msgs[-1]["role"] == "user" and msgs[-1]["content"][0]["tool_use_id"] == "tu1"
    assert store.list_interactions()[0]["tema"] == "menu"


def test_pedir_ayuda_marca_derivacion(db):
    fake = FakeClaude([
        ([text("Ya aviso al equipo."), tool_use("pedir_ayuda", {"motivo": "me duele la cabeza"})], "tool_use"),
        ([text("Ya viene alguien, Marta. Quedate tranquila.")], "end_turn"),
    ])
    r = orch.Orchestrator(client=fake).chat(ses(), "me duele la cabeza")
    assert r.derivado and store.list_help_requests(open_only=True)
    assert store.list_interactions()[0]["derivado"] == 1


def test_contexto_privacidad_y_modo_general(db, at):
    at("09:00")
    s = ses("elena")
    s.update_presence(2)
    ctx = orch.build_context(s)
    assert "ATENCIÓN" in ctx and "Laura" not in ctx
    s.update_presence(1)
    ctx = orch.build_context(s)
    assert "Laura" in ctx and "NO se le recordó" in ctx
    assert "Modo general" in orch.build_context(Session(id="g", kiosk_id="k", resident_id=None, via="general"))


def test_contexto_roberto_tiene_actividad_reciente_sin_opinion(db, at):
    at("09:00")
    ctx = orch.build_context(ses("roberto"))
    assert "ayer Taller de streaming" in ctx and "todavía no dijo" in ctx


def test_error_del_modelo_no_deja_mudo_al_totem(db):
    class Boom:
        beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **k: (_ for _ in ()).throw(RuntimeError("caído"))))
    s = ses()
    r = orch.Orchestrator(client=Boom()).chat(s, "hola")
    assert r.error and "Necesito ayuda" in r.text and s.messages == []


def test_sin_api_key_responde_desde_la_base(db, at):
    at("11:00")
    r = orch.Orchestrator(client=None).chat(ses("marta"), "¿qué hay de almuerzo?")
    assert "Milanesa" in r.text and r.cards[0]["type"] == "menu"


@pytest.mark.parametrize("texto,esperado", [("me siento mal", True), ("qué hora es", False)])
def test_fallback_deriva_malestar(db, texto, esperado):
    r = orch.Orchestrator(client=None).chat(ses(), texto)
    assert r.derivado is esperado
