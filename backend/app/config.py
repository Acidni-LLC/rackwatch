"""Environment, paths, budgets and the price table. Replay is automatic when no key is set."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
GENERATED_DIR = DATA_DIR / "generated"
REPLAY_DIR = DATA_DIR / "replay"
RUNS_DIR = DATA_DIR / "runs"
UPLOADS_DIR = DATA_DIR / "uploads"


def load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    """Minimal .env reader: KEY=value, `#` comments, inline comments after whitespace. Never logs values."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


@dataclass(frozen=True)
class Prices:
    """USD. Published Meta Model API standard-mode rates, read Nov 2 2026 (docs/preflight.json)."""

    input_per_m: float = 1.25
    cached_per_m: float = 0.15
    output_per_m: float = 4.25
    sam_per_image: float = 0.0025
    voice_per_hour: float = 0.18


@dataclass(frozen=True)
class Settings:
    api_key: str = field(default="", repr=False)  # repr=False keeps it out of any log or traceback
    base_url: str = "https://api.meta.ai/v1"
    asr_url: str = "https://api.meta.ai/v1/asr/transcribe"
    spark_model: str = "muse-spark-1.3"
    sam_model: str = "sam-3.1"
    asr_model: str = "muse-voice-transcribe-1.0"
    mode: str = "replay"  # live | record | replay
    budget_total: float = 120.0
    budget_per_run: float = 1.50
    max_tokens_per_run: int = 600_000
    prices: Prices = field(default_factory=Prices)
    db_path: Path = field(default_factory=lambda: RUNS_DIR / "rackwatch.sqlite")
    generated_dir: Path = GENERATED_DIR
    replay_dir: Path = REPLAY_DIR
    ffmpeg: str = "ffmpeg"


def get_settings(**overrides) -> Settings:
    load_dotenv()
    env = os.environ
    key = env.get("MODEL_API_KEY", "").strip()
    mode = env.get("META_MODE", "live").strip().lower() or "live"
    if env.get("REPLAY") == "1" or not key:
        mode = "replay"
    prices = Prices(
        input_per_m=float(env.get("PRICE_IN_PER_M", 1.25)),
        cached_per_m=float(env.get("PRICE_CACHED_PER_M", 0.15)),
        output_per_m=float(env.get("PRICE_OUT_PER_M", 4.25)),
    )
    values = dict(
        api_key=key,
        base_url=env.get("MODEL_API_BASE_URL") or "https://api.meta.ai/v1",
        asr_url=env.get("ASR_URL") or "https://api.meta.ai/v1/asr/transcribe",
        spark_model=env.get("SPARK_MODEL") or "muse-spark-1.3",
        sam_model=env.get("SAM_MODEL") or "sam-3.1",
        asr_model=env.get("ASR_MODEL") or "muse-voice-transcribe-1.0",
        mode=mode,
        budget_total=float(env.get("BUDGET_USD_TOTAL", 120)),
        budget_per_run=float(env.get("BUDGET_USD_PER_RUN", 1.50)),
        max_tokens_per_run=int(env.get("MAX_TOKENS_PER_RUN", 600_000)),
        prices=prices,
        ffmpeg=env.get("FFMPEG_PATH") or "ffmpeg",
    )
    values.update(overrides)
    return Settings(**values)
