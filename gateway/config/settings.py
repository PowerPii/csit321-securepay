"""
Django settings for the gateway.

Configuration comes from environment variables, and secrets from files
mounted by Docker Compose under /run/secrets (see deploy/secrets/).
Nothing secret is committed to the repository.
"""
import base64
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
# Prototype demonstration (M4, 19 Dec 2026): show only the core payment story. When on, the
# dashboard keeps each role's core pages only (DEMO_HIDDEN in frontend/dashboard/src/auth.tsx),
# checkout hides the "check status later" link, and
# customer accounts answer 404. Nothing is removed: turn it off and everything is back
# (deploy/RUNBOOK.md, "Demo core-only mode"). Off by default.
DEMO_CORE_ONLY = env("DEMO_CORE_ONLY", "0") == "1"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "apps.accounts",
    "apps.merchants",
    "apps.checkout",
    "apps.vault",
    "apps.payments",
    "apps.refunds",
    "apps.risk",
    "apps.events",
    "apps.reconciliation",
    "apps.audit",
    "apps.internal",
    "apps.guest",
    "apps.customers",
    "apps.security",
    "apps.status",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.common.middleware.RequestIdMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",  # request.user.is_verified() after the TOTP step
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
        "NAME": env("POSTGRES_DB", "gateway"),
        "USER": env("POSTGRES_USER", "gateway"),
        "PASSWORD": secret("db_password", "gateway"),
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

# The vault uses its own database role so that only the vault module can read card data.
DATABASES["vault"] = {
    **DATABASES["default"],
    # Same database as the gateway in the stack; a separate one can be named for local runs
    # where both roles would otherwise share one django_migrations table.
    "NAME": env("VAULT_DB_NAME", DATABASES["default"]["NAME"]),
    "USER": env("VAULT_DB_USER", "vault"),
    "PASSWORD": secret("vault_db_password", "vault"),
    # In the stack the vault is a schema in the gateway database (search_path of the vault
    # role). Tests give it a database of its own so its tables are created and isolated.
    "TEST": {"NAME": env("VAULT_TEST_DB_NAME", "test_gateway_vault")},
}
DATABASE_ROUTERS = ["apps.vault.routers.VaultRouter"]

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
# Dates in the staff dashboard's filters and tiles ("Blocked today") are Singapore dates.
DASHBOARD_TIME_ZONE = "Asia/Singapore"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Every service sits behind Caddy over HTTPS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
# Staff dashboard sessions end after 15 idle minutes (FR-ACC-10); every request extends them.
SESSION_COOKIE_AGE = 15 * 60
SESSION_SAVE_EVERY_REQUEST = True

# Staff two-step sign-in (FR-ACC-14). Our own lockout (STAFF_LOCKOUT_*) counts wrong
# passwords and wrong codes together, so django-otp's per-device back-off is switched off.
OTP_TOTP_ISSUER = "SecurePay"
OTP_TOTP_THROTTLE_FACTOR = 0

# Shared cache for the rate limits (DRF throttles, apps.security.limits). Gunicorn runs several
# worker processes; Django's default per-process memory cache gave each its own counters, so a
# key could make about three times its per-minute limit (security self-test, 18 Nov 2026).
# A table in the gateway database needs nothing new on the server; migration
# security 0004 creates it.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "gateway_cache",
    }
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Every error in the contract format (contracts/schemas/error.schema.json).
    "EXCEPTION_HANDLER": "apps.common.errors.exception_handler",
    # JSON only. DRF's browsable HTML pages (forms, view names, allowed methods) are for
    # local development; on the public server they only told visitors how the API is built
    # (security self-test, 18 Nov 2026).
    "DEFAULT_RENDERER_CLASSES": (
        ["rest_framework.renderers.JSONRenderer", "rest_framework.renderers.BrowsableAPIRenderer"]
        if DEBUG else ["rest_framework.renderers.JSONRenderer"]
    ),
}

REST_FRAMEWORK.update(
    {
        "DEFAULT_THROTTLE_CLASSES": ["apps.common.throttling.AnonRateThrottle"],
        "DEFAULT_THROTTLE_RATES": {
            "anon": env("THROTTLE_ANON", "60/min"),
            # Per IP: forgot-password and business registration forms (FR-ACC-07, FR-MER-01).
            "account_email": env("THROTTLE_ACCOUNT_EMAIL", "20/hour"),
            # Per IP: customer sign-in and sign-up (on top of the per-account lockout).
            "customer_auth": env("THROTTLE_CUSTOMER_AUTH", "30/min"),
        },
    }
)

# Business rules confirmed in docs/architecture/ARCHITECTURE-RECOMMENDATION.md section 11
# and the requirements document (Chapter 4). Change them here, not in code.
GATEWAY_RULES = {
    "CURRENCY": "SGD",
    "MIN_AMOUNT_CENTS": 50,  # S$0.50
    # S$10,000: the hard ceiling. An administrator can set a lower gateway-wide limit, and a
    # lower one per merchant, in the dashboard (FR-ADM-07).
    "MAX_AMOUNT_CENTS": 1_000_000,
    "IDEMPOTENCY_TTL_HOURS": 24,
    # A key left "in progress" by a crashed request is taken over after this long.
    "IDEMPOTENCY_STALE_SECONDS": 60,
    # Defaults; an administrator can change both in the dashboard (FR-ADM-12, UC-43).
    "API_RATE_LIMIT_PER_MINUTE": 100,  # per API key (NFR-AVA-02, contract section 2)
    "IP_RATE_LIMIT_PER_MINUTE": 300,  # per client IP address on the merchant API
    # UC-43: this many rejected requests from one key or IP within the window -> security alert.
    "RATE_LIMIT_ALERT_VIOLATIONS": 20,
    "RATE_LIMIT_ALERT_WINDOW_MINUTES": 10,
    "AUTH_EXPIRY_DAYS": 7,  # uncaptured authorisations are cancelled by the worker
    "VERIFICATION_CODE_TTL_MINUTES": 10,
    "VERIFICATION_MAX_ATTEMPTS": 3,  # wrong codes, then Blocked (FR-CHK-18, confirmed 5 Nov 2026)
    # A new code replaces the old one (FR-CHK-06). Not in the URS; confirmed by Fifa 13 Nov 2026.
    "VERIFICATION_RESEND_COOLDOWN_SECONDS": 30,
    "VERIFICATION_MAX_CODES": 5,  # codes sent per attempt, including the first
    "CHECKOUT_SESSION_TTL_MINUTES": 60,
    # Guest status check (contracts/API-CONTRACT.md section 8; values confirmed 13 Nov 2026)
    "GUEST_STATUS_CODE_TTL_MINUTES": 10,
    "GUEST_STATUS_MAX_ATTEMPTS": 3,  # wrong codes before the check is locked
    "GUEST_STATUS_LOCK_MINUTES": 15,
    "GUEST_STATUS_SESSION_MINUTES": 15,  # how long an unlocked payment stays viewable
    "GUEST_STATUS_CODE_REQUESTS_PER_PAIR": 3,  # per reference + email, per 15 minutes
    "GUEST_STATUS_CODE_REQUESTS_PER_IP": 10,  # per IP address, per 15 minutes
    "PASSWORD_RESET_LINK_TTL_MINUTES": 30,  # single use (contract section 9, confirmed 13 Nov 2026)
    # Reset links asked for per account per 15 minutes; more requests get the same reply but
    # send nothing, so a mailbox cannot be flooded.
    "PASSWORD_RESET_REQUESTS_PER_ACCOUNT": 3,
    # Merchant self-registration (FR-MER-01): the email verification link works once, for
    # this long. An unverified registration older than this no longer holds its UEN or email.
    "MERCHANT_VERIFICATION_LINK_TTL_HOURS": 24,
    "SCREENING_TARGET_P95_MS": 200,  # NFR-PER-02, checked by `manage.py benchmark_screening`
    # FR-FRD-04: no decision within 500 ms (or an error) -> Blocked, reason screening_unavailable
    "SCREENING_TIMEOUT_MS": 500,
    # Staff sign-in (FR-ACC-15): 5 failed sign-ins (wrong password or wrong TOTP code) lock the
    # account for 15 minutes and email the account holder.
    # Customer accounts (FR-ACC-01 to FR-ACC-04; wireframes C10 to C12). Sign-in locks for 15
    # minutes after 5 wrong passwords (FR-ACC-03). A signed-in session ends after 15 minutes
    # without a request (wireframe C10 note; FR-ACC-10 idle limit) and after 12 hours anyway.
    "CUSTOMER_LOCKOUT_FAILURES": 5,
    "CUSTOMER_LOCKOUT_MINUTES": 15,
    "CUSTOMER_IDLE_MINUTES": 15,
    "CUSTOMER_SESSION_MAX_HOURS": 12,
    # FR-ACC-10: a signed-in page (dashboard or customer account) warns after this many idle
    # minutes and signs out at the idle limit (15 for both).
    "IDLE_WARNING_MINUTES": 13,
    # FR-ACC-12: the privacy notice a customer agreed to. Raise it when the notice changes.
    "PRIVACY_NOTICE_VERSION": "2026-11-16",
    # Security alerts (FR-ADM-08, UC-39): repeats of the same event from the same source within
    # this many minutes are grouped into one open alert with a count, and emailed once.
    "SECURITY_ALERT_GROUP_MINUTES": 60,
    # FR-MER-09: this many attempts on other merchants' records by one API key or operator
    # within the window raise a security alert. Every attempt is in the audit log anyway.
    "CROSS_MERCHANT_ALERT_ATTEMPTS": 3,
    "CROSS_MERCHANT_ALERT_WINDOW_MINUTES": 10,
    # NFR-ENC-03: data keys are rotated at least every 90 days; the key report and the
    # self-check flag an active key from KEY_WARNING_DAYS and fail it at KEY_MAX_AGE_DAYS.
    "KEY_MAX_AGE_DAYS": 90,
    "KEY_WARNING_DAYS": 80,
    # FR-ADM-09, NFR-INT-02: how often the worker checks payment hashes and the audit chain,
    # and how old an audit entry must be before it is chained (lets slow commits land first).
    "INTEGRITY_CHECK_HOURS": 6,
    "AUDIT_SEAL_GRACE_SECONDS": 120,
    # FR-ADM-15: card data self-checks, daily.
    "SELF_CHECK_HOURS": 24,
    # NFR-PRI-01: personal data older than this is deleted or anonymised by the daily run
    # (12 months is a project assumption, URS Section 4.7.1).
    "RETENTION_DAYS": 365,
    "RETENTION_RUN_HOURS": 24,
    "STAFF_LOCKOUT_FAILURES": 5,
    "STAFF_LOCKOUT_MINUTES": 15,
    # After a correct password, the TOTP code must follow within this time.
    "STAFF_TOTP_STEP_MINUTES": 5,
    # One-time backup codes for staff two-step verification, made at authenticator setup.
    "STAFF_BACKUP_CODES": 10,
    "WEBHOOK_SIGNATURE_TOLERANCE_SECONDS": 300,
    # Webhook delivery (contract section 7, confirmed 13 Nov 2026): retry for 72 h from the first
    # failure with these delays (+/-10% jitter), then a final attempt at 72 h; then undelivered.
    "WEBHOOK_TIMEOUT_SECONDS": 10,
    "WEBHOOK_RETRY_WINDOW_HOURS": 72,
    "WEBHOOK_RETRY_DELAYS_SECONDS": (
        60, 300, 900, 1800, 3600, 7200, 14400, 28800, 43200, 43200, 43200, 43200,
    ),
    # Webhook log "Resend" (18 Nov 2026): how many times one event may be put back in the queue
    # by hand. Each resend starts a fresh retry schedule, so this caps the traffic one event
    # can cause.
    "WEBHOOK_MAX_MANUAL_RESENDS": 5,
    "RECONCILIATION_MAX_CHECKS": 6,
    # Status checks on an unresolved operation, after the timeout (+/-10% jitter); after the
    # last one it is escalated (SIMULATOR-CONTRACT.md S2, confirmed by Fifa 13 Nov 2026).
    "RECONCILIATION_DELAYS_SECONDS": (30, 60, 120, 300, 600, 1200),
    # Restart sweep (FR-TXN-15): an operation still "pending" this long after it was committed
    # was never answered (the process died mid-call), so it is marked unresolved and checked.
    # Longer than any call can take (2 s connect + 10 s reply).
    "RECONCILIATION_PENDING_STALE_SECONDS": 60,
    # Simulator call timeouts (SIMULATOR-CONTRACT.md S1, confirmed by Fifa 13 Nov 2026):
    # no reply in time -> the payment is Unresolved and reconciliation asks about the same ID.
    "SIMULATOR_CONNECT_TIMEOUT_SECONDS": 2,
    "SIMULATOR_TIMEOUT_SECONDS": 10,
    "SIMULATOR_SIGNATURE_TOLERANCE_SECONDS": 300,
}

SIMULATOR_URL = env("SIMULATOR_URL", "http://localhost:8001")
# Gateway key for the simulator (`simk_...`) and the secret that signs its replies
# (SIMULATOR-CONTRACT.md section 2, proposal S4). The dev defaults match the simulator's.
SIMULATOR_API_KEY = secret("simulator_api_key", "simk_" + "devonly" * 5 + "devon" if DEBUG else None)
SIMULATOR_SIGNING_SECRET = secret(
    "simulator_signing_secret", "simsig_dev-only-signing-secret" if DEBUG else None
)
# CA bundle for the simulator's TLS certificate (Caddy's local CA in the Compose stack);
# empty means the system trust store.
SIMULATOR_CA_BUNDLE = env("SIMULATOR_CA_BUNDLE", "")
# Public status page (apps.status): the worker checks these two every five minutes through
# their public names. Empty = that part is not checked and shows as "not monitored".
STATUS_GATEWAY_URL = env("STATUS_GATEWAY_URL", "").rstrip("/")
STATUS_SHOP_URL = env("STATUS_SHOP_URL", "").rstrip("/")
# A line shown at the top of the status page, for planned work ("Upgrade on Sat 10 pm").
STATUS_NOTICE = env("STATUS_NOTICE", "")
# Webhooks go only to public addresses (API-CONTRACT.md section 7). In the local stack the
# shop is reached through Caddy on a private Docker network, so its host name is allowed
# here. Leave empty in any real deployment.
WEBHOOK_ALLOWED_PRIVATE_HOSTS = frozenset(
    h.strip().lower() for h in env("WEBHOOK_ALLOWED_PRIVATE_HOSTS", "").split(",") if h.strip()
)
# CA bundle for merchants' TLS certificates (Caddy's local CA locally); empty = system store.
WEBHOOK_CA_BUNDLE = env("WEBHOOK_CA_BUNDLE", "")
# Worker loop: events claimed per pass, and how long a claimed event is held before another
# worker may take it (covers a worker that dies mid-send).
WORKER_BATCH_SIZE = int(env("WORKER_BATCH_SIZE", "50"))
WEBHOOK_LEASE_SECONDS = 60
# Public address of the hosted checkout; the payment's checkout_url is this + "/cs_...".
CHECKOUT_BASE_URL = env("CHECKOUT_BASE_URL", "https://localhost:8443/pay")
# Public address of the guest status page (screen C9). Receipts link to it with ?ref=SP-...
# (API-CONTRACT.md section 8: the link only fills in the reference; it is not a credential).
GUEST_STATUS_URL = env("GUEST_STATUS_URL", "https://localhost:8443/guest/status")
# Public address of the staff dashboard (frontend/dashboard). Password reset, email
# verification and administrator notices link to pages under it.
DASHBOARD_URL = env("DASHBOARD_URL", "https://localhost:8443/dashboard").rstrip("/")
# Customer account pages (C10 to C12) from the checkout build; reset links point here.
# Defaults to /account next to the guest status page, so no new setting is needed.
ACCOUNT_URL = env("ACCOUNT_URL", GUEST_STATUS_URL.rsplit("/guest/status", 1)[0] + "/account"
                  ).rstrip("/")
# Fernet key for secrets the gateway must read back (merchant signing secrets).
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
FIELD_ENCRYPTION_KEY = secret(
    "field_encryption_key",
    base64.urlsafe_b64encode(b"dev-only-field-encryption-key!!!").decode() if DEBUG else None,
)
# AES-256-GCM key for card data in the vault (32 bytes, URL-safe base64; a Fernet key has the
# same format, so existing secrets still work). Only apps.vault reads it.
VAULT_ENCRYPTION_KEY = secret(
    "vault_encryption_key",
    base64.urlsafe_b64encode(b"dev-only-vault-encryption-key!!!"[:32]).decode() if DEBUG else None,
)
CARD_FINGERPRINT_KEY = secret("card_fingerprint_key", "dev-only-fingerprint-key" if DEBUG else None)

# Fraud screening (apps/risk). Empty RISK_MODEL_VERSION = the stub model (score 0, rules
# decide) until the fraud experiment hands over an artefact; see apps/risk/scoring.py.
RISK_MODEL_DIR = env("RISK_MODEL_DIR", str(BASE_DIR / "apps" / "risk" / "model_artifacts"))
RISK_MODEL_VERSION = env("RISK_MODEL_VERSION", "")
RISK_MODEL_SHA256 = env("RISK_MODEL_SHA256", "")  # optional pin, checked as well as the manifest
# The model scores one row per payment. OpenMP threads per call only add start-up cost and,
# with several payments at once, oversubscribe the CPUs: in `manage.py loadtest_screening` at
# 20 req/s the p95 went from 23 ms to 66 ms. Set here, before scikit-learn's OpenMP runtime
# loads (it reads the variable once, and per-thread limits do not reach request threads).
os.environ.setdefault("OMP_NUM_THREADS", "1")
# DEBUG only: "error" or "slow" makes every screening unavailable (outage test, NFR-AVA-04).
RISK_SIMULATE_OUTAGE = env("RISK_SIMULATE_OUTAGE", "")
# How many reverse proxies (Caddy) sit in front of the gateway; the client IP used for
# screening is that many entries from the right of X-Forwarded-For. 0 = REMOTE_ADDR.
TRUSTED_PROXY_HOPS = int(env("TRUSTED_PROXY_HOPS", "0"))
# DRF's per-IP throttles count the same address. Without NUM_PROXIES, DRF keys them on the
# whole X-Forwarded-For header, which a client writes itself; that is only safe while Caddy
# replaces the header. With it, they read the same entry as screening (or REMOTE_ADDR at 0).
REST_FRAMEWORK["NUM_PROXIES"] = TRUSTED_PROXY_HOPS

# Email (verification codes). Mailpit in the local stack; nothing leaves the machine.
# Every message gets a branded HTML version (apps/common/branded_email.py), then goes out
# through EMAIL_INNER_BACKEND, which the EMAIL_BACKEND variable still chooses.
EMAIL_BACKEND = "apps.common.branded_email.BrandedEmailBackend"
EMAIL_INNER_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "1025"))
EMAIL_TIMEOUT = 5
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "SecurePay <no-reply@securepay.localhost>")
# Real recipients and made-up demo ones take different routes (apps/common/mail_routing.py).
# EMAIL_HOST above is the capture inbox (Mailpit). MAIL_PROVIDER_HOST empty = no provider:
# real-looking addresses are then captured too (MAIL_CAPTURE_REAL=1, laptops and CI) or not
# sent at all (MAIL_CAPTURE_REAL=0, the servers), so the shared demo inbox never holds them.
_provider_port = int(env("MAIL_PROVIDER_PORT", "587"))
MAIL_PROVIDER = {
    "host": env("MAIL_PROVIDER_HOST", ""),
    "port": _provider_port,
    "username": env("MAIL_PROVIDER_USERNAME", ""),
    "password": secret("mail_provider_password", ""),
    # 465/2465 are implicit TLS; 587/2587/25 upgrade with STARTTLS. Never plain text.
    "use_ssl": _provider_port in (465, 2465),
    "use_tls": _provider_port not in (465, 2465),
}
MAIL_CAPTURE_REAL = env("MAIL_CAPTURE_REAL", "1") == "1"
# Signing secret of the provider's delivery reports (Resend > Webhooks, `whsec_...`;
# RUNBOOK.md 13b). Empty = POST /webhooks/resend answers 404.
RESEND_WEBHOOK_SECRET = secret("resend_webhook_secret", "")
MAIL_REAL_RECIPIENTS = env("MAIL_REAL_RECIPIENTS", "")  # optional allowlist (regex)
MAIL_SYNTHETIC_RECIPIENTS = env("MAIL_SYNTHETIC_RECIPIENTS", "") or (
    "(?i)@(example\\.(com|org|net)|demoshop\\.sg|securepay\\.sg"
    "|[a-z0-9.-]+\\.(test|invalid|localhost|example))$")

# NFR-PRI-01: every log line goes through a filter that masks email addresses and phone
# numbers (apps/common/logmask.py). Django's own loggers keep their defaults otherwise.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"mask_personal_data": {"()": "apps.common.logmask.MaskPersonalData"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "filters": ["mask_personal_data"]},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
    "loggers": {
        "django": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO"),
                   "propagate": False},
    },
}
