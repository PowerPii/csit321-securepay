"""
Django settings for the processing simulator.

Configuration comes from environment variables, and secrets from files
mounted by Docker Compose under /run/secrets (see deploy/secrets/).
Nothing secret is committed to the repository.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def secret(name, default=None):
    """Read a Compose secret file, falling back to an env var, then a dev default."""
    path = Path(env("SECRETS_DIR", "/run/secrets")) / name
    if path.exists():
        return path.read_text().strip()
    return env(name.upper(), default)


DEBUG = env("DJANGO_DEBUG", "0") == "1"
SECRET_KEY = secret("django_secret_key", "dev-only-insecure-key" if DEBUG else None)
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS", "localhost").split(",")
# The Django admin has no two-step sign-in, so it is routed only in development unless
# DJANGO_ADMIN=1 says otherwise. Off on the public server (Fifa, 14 Nov 2026; deploy/RUNBOOK.md).
ADMIN_ENABLED = env("DJANGO_ADMIN", "1" if DEBUG else "0") == "1"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.operations",
    "apps.ledger",
    "apps.faults",
    "apps.network",
    "apps.issuing",
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

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
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

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "simulator"),
        "USER": env("POSTGRES_USER", "simulator"),
        "PASSWORD": secret("db_password", "simulator"),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "ATOMIC_REQUESTS": False,
        # Keep each worker's connection open between requests instead of opening (and
        # authenticating) a new one per request; checked before reuse (Codex release review,
        # checkout profile, 18 Nov 2026).
        "CONN_MAX_AGE": int(env("DB_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": True,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Every service sits behind Caddy over HTTPS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}

# Gateway <-> simulator link (contracts/SIMULATOR-CONTRACT.md section 2, proposal S4).
# The gateway sends `Authorization: Bearer simk_...`; we keep only its SHA-256 digest in memory.
SIMULATOR_API_KEY = secret(
    "simulator_api_key", "simk_" + "devonly" * 5 + "devon" if DEBUG else None
)
# Signs every 200 and 409 reply (`Simulator-Signature`), so the gateway can keep it as evidence.
SIMULATOR_SIGNING_SECRET = secret(
    "simulator_signing_secret", "simsig_dev-only-signing-secret" if DEBUG else None
)
# How long the lost-response, lost-request and slow test cards hold the connection
# (SIMULATOR-CONTRACT.md section 5). Always longer than the gateway's 10 s reply timeout.
SIM_HOLD_SECONDS = float(env("SIM_HOLD_SECONDS", "15"))

# Test controls (SIMULATOR-CONTRACT.md section 8): /sim/faults exists only when this is 1.
# On in local and CI stacks, off in a demo deployment unless the demo needs it. Its own key,
# held by the test runner only (never the gateway).
SIM_TEST_CONTROLS = env("SIM_TEST_CONTROLS", "0") == "1"
SIMULATOR_TEST_CONTROL_KEY = secret(
    "simulator_test_control_key", "simc_" + "devonly" * 5 + "devon" if DEBUG else None
)
# Where `notify` fault rules send operation.recorded notifications (section 9), and the CA
# that signs the gateway's certificate (Caddy's local root in Compose).
SIM_GATEWAY_NOTIFY_URL = env(
    "SIM_GATEWAY_NOTIFY_URL", "https://gateway.localhost/internal/simulator/notifications"
)
SIM_GATEWAY_CA_BUNDLE = env("SIM_GATEWAY_CA_BUNDLE", "")

# v2 network split (contracts/NETWORK-CONTRACT.md). One codebase, three roles: the acquirer
# (the /sim API), the switch and the issuer. "all" runs every node in this process (local
# runs and tests). In Compose each role is its own container; the URLs say where the next
# node listens (empty = call it in-process).
SIM_ROLE = env("SIM_ROLE", "all")
if SIM_ROLE not in {"all", "acquirer", "switch", "issuer"}:
    raise ValueError("SIM_ROLE must be all, acquirer, switch or issuer")
SIM_SWITCH_URL = env("SIM_SWITCH_URL", "")
SIM_ISSUER_URL = env("SIM_ISSUER_URL", "")
# Per hop. Well under the gateway's 2 s connect / 10 s reply timeouts.
SIM_NETWORK_TIMEOUT = float(env("SIM_NETWORK_TIMEOUT", "3"))
