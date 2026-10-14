"""Descarga los modelos faciales de InsightFace (GitHub Releases) y los deja en models/.

Uso: python scripts/download_models.py [buffalo_sc|buffalo_l]
Los modelos buffalo_* se publican para uso no comercial/investigación; para un
despliegue comercial hay que revisar la licencia o cambiar el pack.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
BASE_URL = "https://github.com/deepinsight/insightface/releases/download/v0.7/"
PACKS = {
    "buffalo_sc": {"sha256": "57d31b56b6ffa911c8a73cfc1707c73cab76efe7f13b675a05223bf42de47c72",
                   "files": ["det_500m.onnx", "w600k_mbf.onnx"]},
    "buffalo_l": {"sha256": "80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f",
                  "files": ["det_10g.onnx", "w600k_r50.onnx"]},
}


def download(pack: str) -> None:
    info = PACKS[pack]
    MODELS.mkdir(exist_ok=True)
    if all((MODELS / f).exists() for f in info["files"]):
        print(f"[{pack}] ya está en {MODELS}")
        return
    zip_path = MODELS / f"{pack}.zip"
    url = BASE_URL + f"{pack}.zip"
    print(f"[{pack}] descargando {url} ...")

    def hook(blocks, block_size, total):
        done = blocks * block_size
        if total > 0:
            print(f"\r  {done / 1e6:6.1f} / {total / 1e6:6.1f} MB", end="", flush=True)

    urllib.request.urlretrieve(url, zip_path, hook)
    print()
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if digest != info["sha256"]:
        zip_path.unlink()
        raise SystemExit(f"Checksum incorrecto para {pack}: {digest}")
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            base = Path(name).name
            if base in info["files"]:
                (MODELS / base).write_bytes(z.read(name))
                print(f"  extraído {base}")
    zip_path.unlink()
    print(f"[{pack}] listo")


if __name__ == "__main__":
    pack = sys.argv[1] if len(sys.argv) > 1 else None
    if pack is None:
        try:
            sys.path.insert(0, str(ROOT))
            from wecare.config import settings
            pack = settings.face_pack
        except Exception:  # noqa: BLE001
            pack = "buffalo_sc"
    if pack not in PACKS:
        raise SystemExit(f"Pack desconocido: {pack}. Opciones: {', '.join(PACKS)}")
    download(pack)
