"""Settings for the Mana Ledger project (local development)."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Development-only key. Replace it before deploying anywhere public.
SECRET_KEY = "dev-only-change-me-mana-ledger"
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
CSRF_TRUSTED_ORIGINS = []

# GitHub Codespaces opens the app at https://<codespace-name>-8000.app.github.dev,
# so allow that address (and let forms post from it) when running there.
if os.environ.get("CODESPACES"):
    ALLOWED_HOSTS.append(".app.github.dev")
    CSRF_TRUSTED_ORIGINS.append("https://*.app.github.dev")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "collection",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "manaledger.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "manaledger.wsgi.application"

# Where your collection is stored.
# - Set DATABASE_URL (for example a free Neon or Supabase Postgres address) to keep
#   everything in one permanent online database, wherever the app runs.
#   In GitHub Codespaces, add it as a Codespaces secret named DATABASE_URL.
# - Without it, the app uses the local file db.sqlite3 in this folder.
from .dburl import describe as _describe_db, parse as _parse_db_url

if os.environ.get("DATABASE_URL", "").strip():
    DATABASES = {"default": _parse_db_url(os.environ["DATABASE_URL"])}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
DATABASE_DESCRIPTION = _describe_db(DATABASES["default"])

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Scryfall asks every API client to identify itself.
SCRYFALL_USER_AGENT = "ManaLedger/1.0 (personal collection tracker)"
