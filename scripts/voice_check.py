"""Prueba las credenciales de Fish Audio: sintetiza una frase y la transcribe de vuelta.

Uso: python scripts/voice_check.py  (lee FISH_API_KEY del .env)
Guarda el audio en data/voice_check.mp3 para que puedas escuchar la voz elegida.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wecare.config import settings  # noqa: E402
from wecare.voice import VoiceService  # noqa: E402

FRASE = "Hola Marta, buenas tardes. Hoy a las tres tenés taller de arte en la sala de talleres. Si querés, te muestro también la agenda completa."

if __name__ == "__main__":
    voice = VoiceService()
    if not voice.enabled:
        raise SystemExit("FISH_API_KEY no configurada en .env")
    audio = voice.tts(FRASE)
    out = settings.db_path.parent / "voice_check.mp3"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(audio)
    print(f"TTS OK: {len(audio)} bytes -> {out}")
    texto = voice.asr(audio)
    print(f"ASR OK: «{texto}»")
