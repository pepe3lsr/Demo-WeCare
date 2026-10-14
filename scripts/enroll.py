"""Enrola fotos de un residente desde la línea de comandos.

Uso: python scripts/enroll.py marta fotos/marta1.jpg fotos/marta2.jpg
Cada foto debe tener exactamente una cara. Requiere consentimiento registrado en el panel.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wecare import store  # noqa: E402
from wecare.identity import IdentityService, build_engine  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    rid, paths = argv[0], argv[1:]
    if not store.get_resident(rid):
        print(f"No existe el residente '{rid}'. Creálo primero en el panel.")
        return 1
    svc = IdentityService(build_engine())
    if not svc.available:
        print("Faltan los modelos: python scripts/download_models.py")
        return 1
    for p in paths:
        res = svc.enroll(rid, Path(p).read_bytes(), fuente="foto")
        print(p, "->", "OK" if res.get("ok") else res.get("error"))
    print("Plantillas de", rid, ":", store.count_embeddings(rid))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
