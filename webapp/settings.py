import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from core.config import settings as core_settings

log = logging.getLogger("webapp")
ROOT = Path(__file__).resolve().parent.parent


def _secret() -> str:
    key = os.getenv("SECRET_KEY", "")
    if len(key) >= 32:
        return key
    log.warning("SECRET_KEY is not set (or shorter than 32 characters): using a random key, sessions end on restart.")
    return secrets.token_hex(32)


@dataclass(frozen=True)
class WebSettings:
    brand: str = os.getenv("BRAND_NAME", "Research Reviewer")
    tagline: str = "Evidence-first assistance for peer reviewers"
    secret_key: str = _secret()
    database_url: str = os.getenv("WEBAPP_DATABASE_URL") or core_settings.database_url
    upload_dir: Path = ROOT / "data" / "uploads"
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "25"))
    max_files_per_request: int = int(os.getenv("MAX_FILES_PER_REQUEST", "10"))
    job_mode: str = os.getenv("JOB_MODE", "thread")  # thread | sync (sync is for tests)
    job_workers: int = int(os.getenv("JOB_WORKERS", "1"))
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"  # set true behind HTTPS
    allow_signup: bool = os.getenv("ALLOW_SIGNUP", "true").lower() == "true"
    support_email: str = os.getenv("SUPPORT_EMAIL", core_settings.contact_email or "support@example.com")


web = WebSettings()
