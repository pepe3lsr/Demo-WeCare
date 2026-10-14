"""Reconocimiento facial con fotos reales (se salta si no hay modelos o fotos).

Para correrlo: python scripts/download_models.py y definir WECARE_FACE_SAMPLES con una
carpeta que tenga a.jpg y b.jpg (misma persona) y c.jpg (otra persona).
"""

import io
import os
from pathlib import Path

import pytest

from wecare import store
from wecare.config import settings
from wecare.identity import IdentityService, build_engine

SAMPLES = Path(os.environ.get("WECARE_FACE_SAMPLES", ""))
pytestmark = pytest.mark.skipif(
    not (SAMPLES / "a.jpg").exists() or not (settings.models_dir / "det_500m.onnx").exists(),
    reason="faltan modelos o fotos de muestra")


def _qr_jpg(payload: str) -> bytes:
    import cv2
    import numpy as np
    import segno
    buf = io.BytesIO()
    segno.make(payload).save(buf, kind="png", scale=8, border=4)
    img = cv2.imdecode(np.frombuffer(buf.getvalue(), np.uint8), cv2.IMREAD_COLOR)
    ok, jpg = cv2.imencode(".jpg", img)
    return jpg.tobytes()


def test_enrolar_y_reconocer(db):
    svc = IdentityService(build_engine())
    assert svc.available
    res = svc.enroll("marta", (SAMPLES / "a.jpg").read_bytes())
    assert res["ok"] and store.count_embeddings("marta") == 1
    same = svc.observe((SAMPLES / "b.jpg").read_bytes())
    other = svc.observe((SAMPLES / "c.jpg").read_bytes())
    assert same.candidate_id == "marta" and same.score >= settings.face_threshold
    assert other.candidate_id is None


def test_sin_consentimiento_no_se_reconoce(db):
    svc = IdentityService(build_engine())
    svc.enroll("marta", (SAMPLES / "a.jpg").read_bytes())
    r = store.get_resident("marta")
    r["consentimiento_facial"] = 0
    store.upsert_resident(r)
    svc.reload_gallery()
    assert svc.observe((SAMPLES / "b.jpg").read_bytes()).candidate_id is None


def test_qr_valido(db):
    svc = IdentityService(build_engine())
    assert svc.observe(_qr_jpg(IdentityService.qr_payload("roberto", "2222"))).qr_resident_id == "roberto"
    assert svc.observe(_qr_jpg(IdentityService.qr_payload("roberto", "9999"))).qr_resident_id is None
