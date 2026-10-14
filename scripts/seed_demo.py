"""Vuelve a cargar los datos ficticios de la demo (borra lo anterior, incluidas las plantillas faciales)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wecare import seed  # noqa: E402

if __name__ == "__main__":
    seed.seed(reset=True)
    print("Datos de demo cargados: Marta, Roberto y Elena; agenda de hoy y mañana; menú; visita y salida de Elena.")
    print("Las fotos para el reconocimiento se enrolan desde el panel (http://127.0.0.1:8000/admin/residentes).")
