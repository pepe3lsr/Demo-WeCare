"""Embeddings faciales ArcFace (InsightFace) sobre onnxruntime.

Alineación por los 5 puntos clave al patrón estándar ArcFace de 112x112 y
normalización L2 del vector resultante. Compatible con `w600k_mbf.onnx`
(buffalo_sc) y `w600k_r50.onnx` (buffalo_l).
"""

from __future__ import annotations

import cv2
import numpy as np
import onnxruntime as ort

# Posiciones canónicas de ojos, nariz y comisuras en una cara de 112x112.
ARCFACE_DST = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
     [41.5493, 92.3655], [70.7299, 92.2041]],
    dtype=np.float32,
)


def align_face(img: np.ndarray, kps: np.ndarray, size: int = 112) -> np.ndarray:
    """Recorta y endereza la cara según sus landmarks (transformación de similitud)."""
    dst = ARCFACE_DST * (size / 112.0)
    M, _ = cv2.estimateAffinePartial2D(kps.astype(np.float32), dst, method=cv2.LMEDS)
    if M is None:  # degenerado: caer a un recorte simple
        M = np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float32)
    return cv2.warpAffine(img, M, (size, size), borderValue=0)


class ArcFace:
    INPUT_MEAN = 127.5
    INPUT_STD = 127.5

    def __init__(self, model_path: str):
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(model_path, sess_options=opts,
                                            providers=["CPUExecutionProvider"])
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        shape = inp.shape  # (N, 3, 112, 112)
        self.size = int(shape[2]) if isinstance(shape[2], int) else 112
        self.output_name = self.session.get_outputs()[0].name

    def embed_aligned(self, aligned: np.ndarray) -> np.ndarray:
        blob = cv2.dnn.blobFromImage(aligned, 1.0 / self.INPUT_STD, (self.size, self.size),
                                     (self.INPUT_MEAN,) * 3, swapRB=True)
        feat = self.session.run([self.output_name], {self.input_name: blob})[0][0]
        norm = np.linalg.norm(feat)
        return (feat / norm).astype(np.float32) if norm > 0 else feat.astype(np.float32)

    def embed(self, img: np.ndarray, kps: np.ndarray) -> np.ndarray:
        return self.embed_aligned(align_face(img, kps, self.size))


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Similitud coseno entre dos embeddings ya normalizados."""
    return float(np.dot(a, b))
