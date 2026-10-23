"""Start the web app. `--sqlite` uses a throw-away SQLite file instead of PostgreSQL (for trying the UI)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
if "--sqlite" in sys.argv:
    os.environ["WEBAPP_DATABASE_URL"] = f"sqlite:///{(ROOT / 'data' / 'dev_web.db').as_posix()}"

import uvicorn  # noqa: E402

uvicorn.run("webapp.main:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
