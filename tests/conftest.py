import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Base y fotos temporales: los tests nunca tocan data/ ni .env del proyecto.
_tmp = Path(os.environ.get("PYTEST_TMP", "/tmp")) / "wecare-tests"
_tmp.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DB_PATH", str(_tmp / "test.db"))
os.environ.setdefault("PHOTOS_DIR", str(_tmp / "photos"))
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("FISH_API_KEY", "")

import pytest  # noqa: E402

from wecare import clock, seed  # noqa: E402
from wecare.db import Database, set_db  # noqa: E402


@pytest.fixture()
def db():
    d = Database(":memory:")
    set_db(d)
    seed.seed()
    yield d
    set_db(None)
    clock.set_demo_time(None)


@pytest.fixture()
def at():
    """Fija la hora de demo: at('15:40')."""
    from datetime import datetime

    def _set(hhmm: str, day_offset: int = 0):
        from datetime import timedelta
        d = clock.real_now().date() + timedelta(days=day_offset)
        clock.set_demo_time(datetime.fromisoformat(f"{d.isoformat()}T{hhmm}"))
    yield _set
    clock.set_demo_time(None)
