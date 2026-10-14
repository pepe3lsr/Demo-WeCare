"""Datos ficticios de la demo: 3 residentes, agenda de 2 días, menú, 1 visita y 1 salida.

Todo es inventado. Las fechas se generan relativas a "hoy" (hora de la demo), así
el guion funciona cualquier día. Las fotos de enrolamiento NO se siembran: se
cargan desde el panel con consentimiento de cada participante de la demo.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import clock, store
from .db import get_db

RESIDENTES = [
    {
        "id": "marta", "nombre_preferido": "Marta", "nombre_completo": "Marta G.", "habitacion": "12", "edad": 82,
        "activo": 1, "consentimiento_facial": 1, "pin": "1111", "tratamiento": "vos", "ritmo": "pausado",
        "tamano_texto": "muy_grande", "volumen": 90, "nivel_detalle": "breve", "tono": "cálido, frases cortas y concretas",
        "intereses": "pintura, plantas, radio, tango",
        "accesibilidad": "prefiere frases breves, texto grande y voz pausada; repetir horarios si lo pide",
        "apoyo_permitido": "agenda del día, talleres, menú",
    },
    {
        "id": "roberto", "nombre_preferido": "Roberto", "nombre_completo": "Roberto P.", "habitacion": "7", "edad": 78,
        "activo": 1, "consentimiento_facial": 1, "pin": "2222", "tratamiento": "vos", "ritmo": "normal",
        "tamano_texto": "grande", "volumen": 80, "nivel_detalle": "medio", "tono": "cercano, con humor suave",
        "intereses": "fútbol, radio, música de los años 60 y 70, taller de streaming",
        "accesibilidad": "hipoacusia leve: hablar claro, sin apuro",
        "apoyo_permitido": "agenda, talleres, menú, conversación sobre sus intereses",
    },
    {
        "id": "elena", "nombre_preferido": "Elena", "nombre_completo": "Elena R.", "habitacion": "21", "edad": 86,
        "activo": 1, "consentimiento_facial": 1, "pin": "3333", "tratamiento": "usted", "ritmo": "pausado",
        "tamano_texto": "muy_grande", "volumen": 85, "nivel_detalle": "breve", "tono": "respetuoso y sereno",
        "intereses": "tejido, cocina, su familia, boleros",
        "accesibilidad": "se orienta mejor con cuentas regresivas simples ('falta una hora')",
        "apoyo_permitido": "agenda, visitas y salidas, menú",
    },
]


def _d(day: date, hhmm: str) -> str:
    return f"{day.isoformat()}T{hhmm}"


def seed(reset: bool = True) -> None:
    db = get_db()
    if reset:
        db.reset()
    hoy = clock.today()
    ayer = hoy - timedelta(days=1)
    manana = hoy + timedelta(days=1)
    # Próximo sábado (si hoy es sábado, el siguiente).
    sabado = hoy + timedelta(days=((5 - hoy.weekday()) % 7) or 7)

    for r in RESIDENTES:
        store.upsert_resident(r)

    def ev(**kw) -> int:
        parts = kw.pop("participantes", None)
        eid = store.upsert_event(kw)
        if parts:
            store.set_participants(eid, parts)
        return eid

    # ---- Ayer: taller de streaming (Roberto y Marta fueron) --------------------------------
    e_stream_ayer = ev(titulo="Taller de streaming", tipo="institucional", inicio=_d(ayer, "17:30"),
                       fin=_d(ayer, "18:30"), lugar="Sala de TV", detalle="Aprendimos a buscar radios y videos en la tele.",
                       participantes=["roberto", "marta"], fuente="agenda institucional")
    store.mark_attendance(e_stream_ayer, "roberto", True)
    store.mark_attendance(e_stream_ayer, "marta", True)

    # ---- Hoy: agenda institucional -----------------------------------------------------------
    for day in (hoy, manana):
        ev(titulo="Desayuno", tipo="comida", inicio=_d(day, "08:00"), fin=_d(day, "09:00"), lugar="Comedor")
        ev(titulo="Almuerzo", tipo="comida", inicio=_d(day, "12:30"), fin=_d(day, "13:30"), lugar="Comedor")
        ev(titulo="Merienda", tipo="comida", inicio=_d(day, "16:00"), fin=_d(day, "16:30"), lugar="Comedor")
        ev(titulo="Cena", tipo="comida", inicio=_d(day, "20:00"), fin=_d(day, "21:00"), lugar="Comedor")

    ev(titulo="Gimnasia suave", tipo="institucional", inicio=_d(hoy, "10:00"), fin=_d(hoy, "10:45"), lugar="Salón grande",
       detalle="Movilidad y estiramientos sentados. Abierta a todos.")
    ev(titulo="Taller de arte", tipo="institucional", inicio=_d(hoy, "15:00"), fin=_d(hoy, "16:00"),
       lugar="Sala de talleres", detalle="Acuarela: flores de primavera.", participantes=["marta", "elena"])
    ev(titulo="Streaming: música de los 60", tipo="institucional", inicio=_d(hoy, "17:30"), fin=_d(hoy, "18:30"),
       lugar="Sala de TV", detalle="Seguimos con el taller de streaming: hoy elegimos música.",
       participantes=["marta", "roberto"])
    ev(titulo="Bingo", tipo="institucional", inicio=_d(hoy, "18:45"), fin=_d(hoy, "19:30"), lugar="Salón grande",
       detalle="Abierto a todos.")

    ev(titulo="Taller de memoria", tipo="institucional", inicio=_d(manana, "10:30"), fin=_d(manana, "11:15"),
       lugar="Sala de talleres", participantes=["marta", "roberto", "elena"])
    ev(titulo="Música en vivo: boleros y tangos", tipo="institucional", inicio=_d(manana, "16:30"),
       fin=_d(manana, "17:30"), lugar="SUM", detalle="Viene el dúo de guitarras. Abierto a todos y a familiares.")

    # ---- Elena: visita hoy y salida el sábado -----------------------------------------------
    ev(titulo="Visita de Laura (su hija)", tipo="visita", inicio=_d(hoy, "16:30"), fin=_d(hoy, "18:00"),
       lugar="Sala de estar de planta baja", resident_id="elena", estado="confirmado", visitante="Laura, su hija",
       mostrar_visitante=1, punto_encuentro="Sala de estar de planta baja", avisar_equipo=0,
       detalle="Confirmada por teléfono esta mañana.", fuente="recepción")
    ev(titulo="Paseo al parque", tipo="salida", inicio=_d(sabado, "10:00"), fin=_d(sabado, "12:30"),
       lugar="Parque municipal", resident_id="elena", estado="tentativo", visitante="Laura, su hija",
       mostrar_visitante=1, punto_encuentro="Recepción", avisar_equipo=1,
       detalle="Depende del clima. Hay que avisar a enfermería la noche anterior.", fuente="recepción")

    # ---- Menú ---------------------------------------------------------------------------------
    menu_hoy = {"desayuno": "Café con leche, tostadas con queso y mermelada",
                "almuerzo": "Milanesa al horno con puré de calabaza",
                "merienda": "Té o mate cocido con bizcochuelo de vainilla",
                "cena": "Sopa de verduras y tortilla de papa"}
    menu_manana = {"desayuno": "Café con leche y medialunas",
                   "almuerzo": "Pollo al horno con arroz primavera",
                   "merienda": "Té con tostadas y dulce de membrillo",
                   "cena": "Fideos con salsa de tomate y queso"}
    for s, p in menu_hoy.items():
        store.upsert_menu(hoy.isoformat(), s, p)
    for s, p in menu_manana.items():
        store.upsert_menu(manana.isoformat(), s, p)
    # Alternativa autorizada por cocina para Elena (no la decide el asistente).
    store.upsert_menu(hoy.isoformat(), "almuerzo", "Pollo al horno con puré de calabaza (alternativa habilitada por cocina)",
                      resident_id="elena")

    db.set_setting("seeded_on", hoy.isoformat())


if __name__ == "__main__":  # pragma: no cover
    seed()
    print("Datos de demo cargados.")
