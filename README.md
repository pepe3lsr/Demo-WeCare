# WeCare · Tótem inteligente para residencias de adultos mayores (demo)

Un tótem con cámara, identificación **consentida** y un asistente conversacional que reconoce a un
residente, arma el contexto con la información autorizada (agenda, visitas, menú, preferencias) y
conversa por voz y pantalla. **Acompaña, informa y deriva; no reemplaza al equipo ni toma decisiones
clínicas.**

Implementa la propuesta funcional "Tótem inteligente para residencia de adultos mayores": los cuatro
casos de uso (agenda personal, conversación contextual, visitas y salidas, menú y sugerencias), el
perfil mínimo de datos, las reglas de privacidad y cuidado, y el panel para que el equipo edite todo.
El guion de presentación y la tabla de criterios de aceptación están en [`docs/guion_demo.md`](docs/guion_demo.md).

## Cómo funciona

```
 cámara ──► reconocimiento facial local ──► ID interno ──┐
            (SCRFD + ArcFace, onnxruntime)                │
 tarjeta QR / PIN ────────────────────────────────────────┤
                                                          ▼
 micrófono ──► Fish Audio ASR ──► texto ──► Orquestador (Claude + herramientas) ──► texto ──► Fish Audio TTS ──► parlante
                                               │  consultar_agenda · consultar_menu · consultar_visitas_y_salidas
                                               │  consultar_actividades_recientes · registrar_opinion · registrar_sugerencia · pedir_ayuda
                                               ▼
                                     SQLite (única fuente de verdad)  ◄──  Panel de administración
```

- **Identidad**: la cara se compara en el propio equipo contra las plantillas de residentes con
  consentimiento. Sólo sale un ID interno. Alternativas equivalentes: PIN o credencial QR impresa.
  Si no reconoce a nadie, pasa a *modo general* sin decir nombres.
- **Contexto**: el asistente recibe el nombre preferido, el trato (vos/usted), ritmo, intereses
  autorizados y un resumen del día. Nunca información clínica.
- **Conversación**: Claude sólo puede afirmar lo que devuelven las herramientas; el system prompt
  codifica las reglas (no inventar, no diagnosticar, derivar ante malestar, privacidad con terceros
  cerca, confirmado vs. tentativo, no repetir recordatorios, cerrar con naturalidad).
- **Acción y registro**: pedidos de ayuda, sugerencias y opiniones quedan en el panel. Se guarda
  texto, resultado del reconocimiento y derivaciones; **no audio ni video**.

## Puesta en marcha

Requisitos: Python 3.10+, una notebook con cámara, micrófono y parlantes, Chrome.

```bash
git clone https://github.com/pepe3lsr/Demo-WeCare.git && cd Demo-WeCare
./run.sh          # Windows: run.bat
```

El script crea el entorno, instala dependencias, descarga los modelos faciales (16 MB), siembra los
datos ficticios y levanta el servidor:

- Tótem: <http://127.0.0.1:8000/> — en modo kiosco:
  `chrome --kiosk --use-fake-ui-for-media-stream http://127.0.0.1:8000/`
- Panel: <http://127.0.0.1:8000/admin>

Claves (en `.env`, copiado de `.env.example`):

| Variable | Para qué | Si falta |
|---|---|---|
| `ANTHROPIC_API_KEY` | Asistente conversacional (Claude) | Responde con frases básicas armadas desde la base |
| `FISH_API_KEY` | Voz: transcripción y síntesis (Fish Audio) | Funciona con pantalla y teclado |
| `FISH_VOICE_ID` | `reference_id` de la voz elegida en fish.audio | Voz por defecto del modelo |

Probar la voz: `python scripts/voice_check.py` (genera `data/voice_check.mp3`).

### Enrolar las caras de la demo

Panel → **Residentes** → elegir a Marta, Roberto o Elena → *Encender cámara* → *Tomar foto y
enrolar* (2 o 3 fotos). También por línea de comandos: `python scripts/enroll.py marta foto1.jpg foto2.jpg`.
Las plantillas se guardan como vectores en SQLite y las fotos en `data/photos/` (fuera del repo).
Desmarcar "Consentimiento" o borrar las plantillas saca a la persona de la galería al instante.

## Datos de la demo

Tres residentes ficticios (Marta, Roberto, Elena), agenda institucional de hoy y mañana con talleres
con inscripción, menú de dos días con una alternativa habilitada por cocina, una visita confirmada y
una salida tentativa. Todo relativo a "hoy"; el **reloj de demo** del panel permite fijar la hora
(por ejemplo 15:40) para ensayar cualquier escena. `python scripts/seed_demo.py` vuelve a cargar todo.

## Estructura

```
wecare/
  main.py          API del tótem + panel (FastAPI)
  orchestrator.py  Claude: system prompt, bucle de herramientas, contexto por sesión
  tools.py         Herramientas (agenda, menú, visitas, opinión, sugerencia, ayuda) con reglas de privacidad
  identity.py      Reconocimiento consentido, QR, PIN, galería
  faces/           SCRFD + ArcFace sobre onnxruntime (sin dependencias nativas)
  store.py / db.py Dominio y esquema SQLite
  seed.py          Datos ficticios
  voice.py         Fish Audio TTS/ASR
  sessions.py      Sesiones del kiosco, presencia y modo privacidad
  templates/ static/  Tótem (kiosco) y panel
scripts/           download_models · seed_demo · enroll · voice_check
tests/             Reglas de negocio, orquestador con cliente simulado, API, identidad
docs/guion_demo.md Guion de las 4 escenas y criterios de aceptación
```

Tests: `python -m pytest -q` (no necesitan claves; los de identidad se saltan si no hay fotos de muestra).

## Decisiones y límites

- **Modelo**: `claude-opus-5-5` con esfuerzo bajo (configurable con `CLAUDE_MODEL` / `CLAUDE_EFFORT`).
  Se usa el parámetro `fallbacks="default"` para que una negativa de seguridad se reintente en otro modelo.
- **Reconocimiento facial**: paquete `buffalo_sc` de InsightFace (16 MB, ~30 ms por cuadro en CPU);
  `buffalo_l` es más preciso y más lento. Los modelos se publican para uso **no comercial**: para un
  despliegue real hay que revisar la licencia o cambiarlos.
- **Privacidad física**: con más de una cara frente al tótem se ocultan visitas y salidas hasta que la
  persona lo pida; en modo general no hay datos personales.
- **Voz en la nube**: Fish Audio recibe el audio para transcribirlo y sintetizar; el tótem no lo
  almacena. Si la institución exige audio local, el ASR puede reemplazarse por `faster-whisper`.
- **Sin autenticación en el panel**: la demo escucha sólo en `127.0.0.1`. Un piloto necesita
  usuarios, roles y registro de cambios.
- **No es un dispositivo médico.** El asistente no diagnostica ni opina sobre medicación, dieta o
  cuidados; ante cualquier señal de malestar deriva al equipo.
