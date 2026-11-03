"""Turn a DATABASE_URL into Django's DATABASES setting (no extra package needed).

    postgresql://user:password@host:5432/dbname?sslmode=require
    postgres://...   (same thing)
    sqlite:///path/to/file.sqlite3

Free Postgres hosts such as Neon and Supabase show an address like the first one.
"""
from urllib.parse import parse_qsl, unquote, urlsplit

POSTGRES_SCHEMES = {"postgres", "postgresql", "pgsql"}


def parse(url):
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme == "sqlite":
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": unquote(parts.path) or ":memory:"}
    if scheme not in POSTGRES_SCHEMES:
        raise ValueError(f"DATABASE_URL must start with postgresql:// or sqlite:///, not {scheme or 'nothing'}://")
    name = unquote(parts.path.lstrip("/"))
    if not name or not parts.hostname:
        raise ValueError("DATABASE_URL needs a host and a database name, like postgresql://user:pass@host/dbname")
    options = dict(parse_qsl(parts.query))
    options.pop("channel_binding", None)  # Neon adds this; libpq handles it, but it's not needed
    if "sslmode" not in options and parts.hostname not in {"localhost", "127.0.0.1"}:
        options["sslmode"] = "require"  # hosted databases need an encrypted connection
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": name,
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname,
        "PORT": str(parts.port or ""),
        "OPTIONS": options,
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }


def describe(db):
    """A safe one-line description (no password) for the footer and backup page."""
    if db["ENGINE"].endswith("sqlite3"):
        return "Local file (db.sqlite3)"
    return f"Postgres at {db['HOST']} ({db['NAME']})"
