"""Voz con Fish Audio: síntesis (TTS) y transcripción (ASR).

El audio se envía a los servidores de Fish Audio para procesarlo; el tótem no lo
guarda. Si no hay `FISH_API_KEY`, el tótem funciona sólo con pantalla y teclado.
"""

from __future__ import annotations

import logging

from .config import settings

log = logging.getLogger(__name__)


class VoiceService:
    def __init__(self):
        self._client = None
        if settings.fish_api_key:
            try:
                from fishaudio import FishAudio
                self._client = FishAudio(api_key=settings.fish_api_key, timeout=60.0)
            except Exception:  # pragma: no cover
                log.exception("No se pudo iniciar Fish Audio")

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def tts(self, text: str, reference_id: str | None = None, speed: float | None = None) -> bytes:
        """Devuelve MP3. `speed` < 1 habla más pausado."""
        if not self.enabled:
            raise RuntimeError("Voz no configurada (FISH_API_KEY)")
        kwargs = {"text": text, "format": "mp3", "latency": "balanced", "model": settings.fish_tts_model}
        voice = reference_id or settings.fish_voice_id
        if voice:
            kwargs["reference_id"] = voice
        if speed and abs(speed - 1.0) > 0.01:
            kwargs["speed"] = speed
        return self._client.tts.convert(**kwargs)

    def asr(self, audio: bytes, language: str | None = None) -> str:
        if not self.enabled:
            raise RuntimeError("Voz no configurada (FISH_API_KEY)")
        res = self._client.asr.transcribe(audio=audio, language=language or settings.fish_asr_language,
                                          include_timestamps=False)
        return (res.text or "").strip()

    def check(self) -> dict:
        """Prueba rápida de credenciales (usada por scripts/voice_check.py y el panel)."""
        if not self.enabled:
            return {"ok": False, "error": "FISH_API_KEY no configurada"}
        try:
            audio = self.tts("Hola, soy el asistente de la residencia. ¿Cómo estás hoy?")
            return {"ok": True, "bytes": len(audio)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
