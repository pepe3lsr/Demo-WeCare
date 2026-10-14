"""Configuración por variables de entorno (.env en la raíz del proyecto)."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8",
                                      extra="ignore")

    # Institución
    institution_name: str = "Residencia Demo"
    timezone: str = "America/Argentina/Buenos_Aires"

    # Rutas
    db_path: Path = ROOT / "data" / "wecare.db"
    models_dir: Path = ROOT / "models"
    photos_dir: Path = ROOT / "data" / "photos"  # fotos de enrolamiento (consentidas)

    # Reconocimiento facial
    face_pack: str = "buffalo_sc"          # buffalo_sc (rápido) | buffalo_l (más preciso)
    face_threshold: float = 0.40           # similitud coseno mínima para aceptar identidad
    face_confirm_frames: int = 2           # observaciones consecutivas antes de confirmar
    face_min_px: int = 60                  # caras más chicas se ignoran (gente lejos)
    presence_timeout_s: int = 25           # sin caras durante este tiempo → cierra sesión

    # Asistente conversacional (Claude)
    anthropic_api_key: str | None = None
    anthropic_workspace_id: str | None = None  # sólo si la clave no está asociada a un workspace
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "low"
    claude_max_tokens: int = 2048

    # Voz (Fish Audio)
    fish_api_key: str | None = None
    fish_voice_id: str | None = None       # reference_id de la voz elegida en fish.audio
    fish_tts_model: str = "s2-pro"
    fish_asr_language: str = "es"

    # Demo
    demo_reset_on_start: bool = False

    @property
    def claude_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def voice_enabled(self) -> bool:
        return bool(self.fish_api_key)


settings = Settings()
