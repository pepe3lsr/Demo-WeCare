"""Servicio de identidad: reconocimiento consentido + alternativas no biométricas.

Devuelve siempre IDs internos. La galería se recarga desde la base cuando el
panel enrola o desactiva a alguien; nunca sale del servidor.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import cv2
import numpy as np

from . import store
from .config import settings

log = logging.getLogger(__name__)

QR_PREFIX = "WECARE:"


@dataclass
class IdentityResult:
    n_faces: int
    candidate_id: str | None  # mejor coincidencia por encima del umbral
    score: float | None
    qr_resident_id: str | None  # credencial QR válida leída en el cuadro
    face_box: list[float] | None


class IdentityService:
    def __init__(self, engine=None):
        self.engine = engine
        self.threshold = settings.face_threshold
        self._gallery: dict[str, list[np.ndarray]] = {}
        self._lock = threading.Lock()
        self._qr = cv2.QRCodeDetector()
        self.reload_gallery()

    # ------------------------------------------------------------------ galería
    @property
    def available(self) -> bool:
        return self.engine is not None

    def reload_gallery(self) -> None:
        with self._lock:
            self._gallery = store.load_gallery()
        log.info("Galería facial: %d residentes con plantillas", len(self._gallery))

    def gallery_size(self) -> dict[str, int]:
        return {k: len(v) for k, v in self._gallery.items()}

    # ------------------------------------------------------------------ utilidades
    @staticmethod
    def decode_image(data: bytes) -> np.ndarray | None:
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img

    def decode_qr(self, img: np.ndarray) -> str | None:
        try:
            payload, points, _ = self._qr.detectAndDecode(img)
        except cv2.error:
            return None
        if not payload or not payload.startswith(QR_PREFIX):
            return None
        parts = payload[len(QR_PREFIX):].split(":")
        if len(parts) != 2:
            return None
        rid, pin = parts
        return rid if store.verify_pin(rid, pin) else None

    @staticmethod
    def qr_payload(rid: str, pin: str) -> str:
        return f"{QR_PREFIX}{rid}:{pin}"

    # ------------------------------------------------------------------ enrolamiento
    def enroll(self, rid: str, image: bytes, fuente: str = "foto", photo_path: str | None = None) -> dict:
        if not self.available:
            return {"ok": False, "error": "Reconocimiento facial no disponible (faltan modelos)."}
        img = self.decode_image(image)
        if img is None:
            return {"ok": False, "error": "No pude leer la imagen."}
        faces = self.engine.detect(img)
        if len(faces) != 1:
            return {"ok": False, "error": f"Se necesita exactamente una cara en la foto (encontré {len(faces)})."}
        face = faces[0]
        emb = self.engine.embed(img, face)
        # Score contra las plantillas previas de la misma persona: sirve para ver si la foto es consistente.
        with self._lock:
            previas = self._gallery.get(rid, [])
        from .faces.arcface import cosine
        consistencia = round(max(cosine(emb, e) for e in previas), 3) if previas else None
        eid = store.add_embedding(rid, emb, fuente, photo_path)
        self.reload_gallery()
        return {"ok": True, "id": eid, "n_embeddings": store.count_embeddings(rid),
                "tamano_px": int(min(face.width, face.height)), "consistencia": consistencia}

    # ------------------------------------------------------------------ observación
    def observe(self, image: bytes) -> IdentityResult:
        img = self.decode_image(image)
        if img is None:
            return IdentityResult(0, None, None, None, None)
        qr_rid = self.decode_qr(img)
        if not self.available:
            return IdentityResult(0, None, None, qr_rid, None)
        with self._lock:
            gallery = self._gallery
        obs = self.engine.observe(img, gallery, threshold=self.threshold)
        best = obs.best
        box = obs.main_face.bbox.tolist() if obs.main_face is not None else None
        return IdentityResult(
            n_faces=obs.n_faces,
            candidate_id=best.resident_id if best else None,
            score=round(best.score, 3) if best else None,
            qr_resident_id=qr_rid,
            face_box=box,
        )


def build_engine():
    """Carga el motor facial; si faltan los modelos, el tótem sigue funcionando sin biometría."""
    try:
        from .faces import FaceEngine
        return FaceEngine(settings.models_dir, pack=settings.face_pack, min_face_px=settings.face_min_px)
    except FileNotFoundError as e:
        log.warning("%s — el tótem arranca en modo sin reconocimiento facial.", e)
    except Exception:  # pragma: no cover - entorno sin onnxruntime
        log.exception("No se pudo iniciar el motor facial")
    return None
