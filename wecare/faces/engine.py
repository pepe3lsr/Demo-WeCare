"""Motor facial: detección + embeddings + comparación contra una galería.

Todo corre en CPU y en local. La galería es un diccionario en memoria
`resident_id -> lista de embeddings` que el servicio de identidad carga desde la
base de datos; aquí no se conoce ningún dato personal, sólo IDs internos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .arcface import ArcFace, cosine
from .scrfd import SCRFD, Face

# Paquetes de modelos de InsightFace (GitHub Releases, v0.7).
MODEL_PACKS = {
    "buffalo_sc": {"detector": "det_500m.onnx", "recognizer": "w600k_mbf.onnx"},
    "buffalo_l": {"detector": "det_10g.onnx", "recognizer": "w600k_r50.onnx"},
}


@dataclass
class Match:
    resident_id: str
    score: float  # similitud coseno (−1..1); típico: >0.45 misma persona, <0.3 distinta


@dataclass
class Observation:
    faces: list[Face]
    main_face: Face | None
    main_embedding: np.ndarray | None
    matches: list[Match] = field(default_factory=list)

    @property
    def n_faces(self) -> int:
        return len(self.faces)

    @property
    def best(self) -> Match | None:
        return self.matches[0] if self.matches else None


class FaceEngine:
    def __init__(self, models_dir: str | Path, pack: str = "buffalo_sc",
                 det_size: tuple[int, int] = (640, 640), min_face_px: int = 60):
        files = MODEL_PACKS[pack]
        models_dir = Path(models_dir)
        det_path = models_dir / files["detector"]
        rec_path = models_dir / files["recognizer"]
        for p in (det_path, rec_path):
            if not p.exists():
                raise FileNotFoundError(
                    f"Falta el modelo {p}. Ejecutá `python scripts/download_models.py`.")
        self.detector = SCRFD(str(det_path), det_size=det_size)
        self.recognizer = ArcFace(str(rec_path))
        self.min_face_px = min_face_px

    def detect(self, img_bgr: np.ndarray) -> list[Face]:
        return [f for f in self.detector.detect(img_bgr)
                if min(f.width, f.height) >= self.min_face_px]

    def embed(self, img_bgr: np.ndarray, face: Face) -> np.ndarray:
        return self.recognizer.embed(img_bgr, face.kps)

    @staticmethod
    def pick_main(faces: list[Face]) -> Face | None:
        """La cara 'principal' es la más grande (la persona más cerca del tótem)."""
        if not faces:
            return None
        return max(faces, key=lambda f: f.width * f.height)

    def observe(self, img_bgr: np.ndarray, gallery: dict[str, list[np.ndarray]],
                threshold: float = 0.40) -> Observation:
        faces = self.detect(img_bgr)
        main = self.pick_main(faces)
        if main is None:
            return Observation(faces=[], main_face=None, main_embedding=None)
        emb = self.embed(img_bgr, main)
        matches = []
        for rid, embs in gallery.items():
            if not embs:
                continue
            score = max(cosine(emb, e) for e in embs)
            if score >= threshold:
                matches.append(Match(resident_id=rid, score=score))
        matches.sort(key=lambda m: -m.score)
        return Observation(faces=faces, main_face=main, main_embedding=emb, matches=matches)
