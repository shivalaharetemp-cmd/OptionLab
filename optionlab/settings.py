"""OptionLab settings.

The Upstox analytics token is read from the environment (.env) only. It is never rendered
in a template, never sent to the browser and never written to the database or logs.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "upstox",
    "analytics",
    "dashboard",
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

ROOT_URLCONF = "optionlab.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
ASGI_APPLICATION = "optionlab.asgi.application"
WSGI_APPLICATION = "optionlab.wsgi.application"

DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": BASE_DIR / "db.sqlite3",
    "OPTIONS": {"timeout": 30},   # engine thread + web threads share the file
}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
TIME_ZONE = "Asia/Kolkata"
USE_TZ = True
STATIC_URL = "static/"

# ------------------------------------------------------------------ OptionLab
UPSTOX_TOKEN = os.environ.get("UPSTOX_ANALYTICS_TOKEN", "").strip()
OPTIONLAB_MODE = (os.environ.get("OPTIONLAB_MODE") or ("live" if UPSTOX_TOKEN else "demo")).lower()
WATCHLIST = [s.strip().upper() for s in os.environ.get("WATCHLIST", "NIFTY,BANKNIFTY").split(",") if s.strip()]
CHAIN_POLL_SECONDS = int(os.environ.get("CHAIN_POLL_SECONDS", "10"))
CANDLE_POLL_SECONDS = int(os.environ.get("CANDLE_POLL_SECONDS", "30"))
ATM_WINDOW_STRIKES = int(os.environ.get("ATM_WINDOW_STRIKES", "8"))      # CE+PE live-streamed each side of ATM
DEMO_SPEED = float(os.environ.get("DEMO_SPEED", "30"))
STATIC_IP_WHITELISTED = os.environ.get("UPSTOX_STATIC_IP_WHITELISTED", "0") == "1"
EXPLORER_ENABLED = os.environ.get("EXPLORER_ENABLED", "1" if DEBUG else "0") == "1"

# HARD SAFETY SWITCH — option BUYER only. Do not turn this into an env var on purpose.
# The engine refuses to emit anything except BUY_CE / BUY_PE while this is False (see analytics/engine.py).
ALLOW_SELLING = False

# Signal engine tunables (see README "Strategy rules")
SIGNALS = {
    "min_confidence": 65,
    "first_signal_time": "09:30",       # no entries in the opening auction noise
    "last_signal_time": "14:45",        # no fresh entries into the close
    "force_exit_time": "15:15",         # paper tracker squares off here
    "cooldown_minutes": 20,             # per underlying+strategy+direction
    "max_signals_per_day": 8,           # per underlying
    "max_open": 3,
    "daily_stop_r": -3.0,               # stop emitting after realised -3R on the day
    "delta_min": 0.38, "delta_max": 0.65, "delta_target": 0.50,
    "max_spread_pct": 3.0,
    "min_oi": 20000,
    "min_volume": 5000,
    "sl_min_pct": 15.0, "sl_max_pct": 35.0,
    "t1_r": 1.5, "t2_r": 2.5,
    "max_hold_minutes": 45,
    "theta_cost_max_frac": 0.25,        # est. theta bleed over the hold must be < 25% of risk
    "stale_tick_seconds": 20,
    "slippage_pct": 0.5,                # paper-fill slippage on entry/exit
}

LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "formatters": {"std": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "std"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
