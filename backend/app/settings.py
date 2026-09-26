"""Process-wide configuration and filesystem paths."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    """Settings, overridable by environment variables prefixed with ``MOC_``."""

    model_config = SettingsConfigDict(env_prefix="MOC_", extra="ignore")

    # The engine is a pure function of (data, rules, as_of); it never calls now().
    #
    # The supplied dataset was generated around early April 2026: the latest lab is
    # 2026-04-07, the latest diagnosis 2026-03-11, and encounters run forward to
    # 2026-08-12 because the file mixes completed visits with future appointments.
    # Anchoring to the day after the last clinical data point is what makes those
    # future rows behave as "scheduled" rather than as history. Evaluating at the
    # real wall-clock date instead would leave zero upcoming encounters and would
    # age out all but a handful of A1C results, so every diabetic would fall to the
    # Unmonitored tier. Overridable per request via ?as_of=.
    default_as_of: date = date(2026, 4, 8)

    data_dir: Path = REPO_DIR / "data"
    config_dir: Path = BACKEND_DIR / "config"
    database_url: str = f"sqlite:///{BACKEND_DIR / 'model_of_care.db'}"

    # Rebuild source tables from the CSVs on startup. The database is a derived
    # artifact here, not a system of record, so it is disposable by design.
    rebuild_on_startup: bool = True


settings = Settings()
