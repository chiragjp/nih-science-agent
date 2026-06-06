"""Runtime configuration for the NIH Science Agent.

Loads paths and log level from environment variables with sensible defaults.
Override via shell exports or a `.env` file (loaded by the user, not by this
module — keeping things explicit).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel


class Settings(BaseModel):
    data_dir: Path = Path("data")
    cache_dir: Path = Path("data/cache")
    raw_dir: Path = Path("data/raw")
    interim_dir: Path = Path("data/interim")
    processed_dir: Path = Path("data/processed")
    log_level: str = "INFO"

    def model_post_init(self, __context: object) -> None:
        for d in (
            self.data_dir,
            self.cache_dir,
            self.raw_dir,
            self.interim_dir,
            self.processed_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        data_dir=Path(os.environ.get("NSA_DATA_DIR", "data")),
        cache_dir=Path(os.environ.get("NSA_CACHE_DIR", "data/cache")),
        raw_dir=Path(os.environ.get("NSA_RAW_DIR", "data/raw")),
        interim_dir=Path(os.environ.get("NSA_INTERIM_DIR", "data/interim")),
        processed_dir=Path(os.environ.get("NSA_PROCESSED_DIR", "data/processed")),
        log_level=os.environ.get("NSA_LOG_LEVEL", "INFO"),
    )
