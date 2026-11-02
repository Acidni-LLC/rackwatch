"""Cross-platform task runner. The Makefile wraps these, and they also run directly on Windows.

python scripts/rw.py setup     create .venv, install Python and Node dependencies, build the frontend
python scripts/rw.py replay    serve the app on recorded runs (no key needed)
python scripts/rw.py dev       serve the app live, using MODEL_API_KEY from .env
python scripts/rw.py test      pytest, ruff, secret scan
python scripts/rw.py build     build the frontend
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
NPM = shutil.which("npm") or "npm"


def run(args: list[str], cwd: Path = ROOT, env: dict | None = None) -> None:
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], cwd=cwd, check=True, env=env)


def python() -> str:
    return str(PY) if PY.exists() else sys.executable


def setup() -> None:
    if not PY.exists():
        venv.create(VENV, with_pip=True)
    run([PY, "-m", "pip", "install", "-q", "-r", "backend/requirements-dev.txt"])
    build()


def build() -> None:
    frontend = ROOT / "frontend"
    if not (frontend / "node_modules").exists():
        run([NPM, "ci"], cwd=frontend)
    run([NPM, "run", "build"], cwd=frontend)


def serve(replay: bool) -> None:
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        build()
    env = dict(os.environ)
    if replay:
        env["REPLAY"] = "1"
    print("RackWatch: http://127.0.0.1:8000", flush=True)
    run([python(), "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--port", "8000"], env=env)


def test() -> None:
    run([python(), "-m", "pytest"])
    run([python(), "-m", "ruff", "check", "backend", "scripts", "datagen"])
    run([python(), "scripts/secret_scan.py"])


COMMANDS = {
    "setup": setup,
    "build": build,
    "replay": lambda: serve(True),
    "dev": lambda: serve(False),
    "test": test,
}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        raise SystemExit(__doc__)
    COMMANDS[sys.argv[1]]()
