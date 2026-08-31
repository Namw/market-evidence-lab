"""Django settings for Market Evidence Lab."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)
load_dotenv(BASE_DIR / ".env.market_pilot", override=False)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-local-development-only-change-me",
)
DEBUG = env_bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core.apps.CoreConfig",
    "apps.market_data.apps.MarketDataConfig",
    "apps.microstructure.apps.MicrostructureConfig",
    "apps.market_funds.apps.MarketFundsConfig",
    "apps.collection.apps.CollectionConfig",
    "apps.inspection.apps.InspectionConfig",
    "apps.scheduling.apps.SchedulingConfig",
    "apps.news_data.apps.NewsDataConfig",
    "apps.news_analysis.apps.NewsAnalysisConfig",
    "apps.meme_monitor.apps.MemeMonitorConfig",
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
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "market_evidence_lab"),
        "USER": os.getenv("POSTGRES_USER", "market_evidence_lab"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "market_evidence_lab_dev"),
        "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "PORT": os.getenv("POSTGRES_PORT", "55432"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

BINANCE_FUTURES_BASE_URL = os.getenv(
    "BINANCE_FUTURES_BASE_URL",
    "https://fapi.binance.com",
).rstrip("/")

MICROSTRUCTURE_SYMBOLS = [
    value.strip().upper()
    for value in os.getenv("MICROSTRUCTURE_SYMBOLS", "ETHUSDT,ZECUSDT").split(",")
    if value.strip()
]
MICROSTRUCTURE_SYMBOL = os.getenv(
    "MICROSTRUCTURE_SYMBOL", MICROSTRUCTURE_SYMBOLS[0]
).upper()
MICROSTRUCTURE_WS_BASE_URL = os.getenv(
    "MICROSTRUCTURE_WS_BASE_URL",
    "wss://fstream.binance.com/public/ws",
).rstrip("/")
MICROSTRUCTURE_WS_UPDATE_SPEED = os.getenv(
    "MICROSTRUCTURE_WS_UPDATE_SPEED",
    "500ms",
)
MICROSTRUCTURE_SAMPLE_INTERVAL_SECONDS = float(
    os.getenv("MICROSTRUCTURE_SAMPLE_INTERVAL_SECONDS", "1")
)
MICROSTRUCTURE_KLINE_POLL_SECONDS = float(
    os.getenv("MICROSTRUCTURE_KLINE_POLL_SECONDS", "5")
)
MICROSTRUCTURE_RECONNECT_INITIAL_SECONDS = float(
    os.getenv("MICROSTRUCTURE_RECONNECT_INITIAL_SECONDS", "1")
)
MICROSTRUCTURE_RECONNECT_MAX_SECONDS = float(
    os.getenv("MICROSTRUCTURE_RECONNECT_MAX_SECONDS", "30")
)
MICROSTRUCTURE_WS_OPEN_TIMEOUT_SECONDS = float(
    os.getenv("MICROSTRUCTURE_WS_OPEN_TIMEOUT_SECONDS", "10")
)

DERIBIT_BASE_URL = os.getenv(
    "DERIBIT_BASE_URL",
    "https://www.deribit.com/api/v2",
).rstrip("/")

NEWS_AI_BASE_URL = os.getenv("NEWS_AI_BASE_URL", "https://api.deepseek.com").rstrip("/")

MARKET_FUNDS_USER_AGENT = os.getenv(
    "MARKET_FUNDS_USER_AGENT",
    "MarketEvidenceLab/1.0 contact=local-research",
)
MARKET_FUNDS_CONNECT_TIMEOUT_SECONDS = float(
    os.getenv("MARKET_FUNDS_CONNECT_TIMEOUT_SECONDS", "10")
)
MARKET_FUNDS_READ_TIMEOUT_SECONDS = float(
    os.getenv("MARKET_FUNDS_READ_TIMEOUT_SECONDS", "20")
)
MARKET_FUNDS_MAX_RETRIES = int(os.getenv("MARKET_FUNDS_MAX_RETRIES", "2"))
ETHEREUM_RPC_URL = os.getenv("ETHEREUM_RPC_URL", "").strip()
NEWS_AI_API_KEY = os.getenv("NEWS_AI_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
NEWS_AI_MODEL = os.getenv("NEWS_AI_MODEL", "deepseek-v4-flash")
NEWS_AI_TIMEOUT_SECONDS = float(os.getenv("NEWS_AI_TIMEOUT_SECONDS", "60"))
MARKET_PILOT_WECHAT_WEBHOOK_URL = os.getenv(
    "MARKET_PILOT_WECHAT_WEBHOOK_URL", ""
).strip()
MARKET_PILOT_PUBLIC_BASE_URL = os.getenv(
    "MARKET_PILOT_PUBLIC_BASE_URL", ""
).strip()
MARKET_PILOT_WEBHOOK_TIMEOUT_SECONDS = float(
    os.getenv("MARKET_PILOT_WEBHOOK_TIMEOUT_SECONDS", "10")
)
NEWS_ARTICLE_TIMEOUT_SECONDS = float(os.getenv("NEWS_ARTICLE_TIMEOUT_SECONDS", "10"))
NEWS_AI_BATCH_SIZE = int(os.getenv("NEWS_AI_BATCH_SIZE", "10"))
NEWS_AI_MAX_RETRIES = int(os.getenv("NEWS_AI_MAX_RETRIES", "2"))
NEWS_AI_MAX_REQUESTS_PER_RUN = int(
    os.getenv("NEWS_AI_MAX_REQUESTS_PER_RUN", "50")
)
NEWS_AI_ANALYSIS_VERSION = os.getenv("NEWS_AI_ANALYSIS_VERSION", "news-eth-v2")
NEWS_AI_PROMPT_VERSION = os.getenv("NEWS_AI_PROMPT_VERSION", "news-eth-direction-v2")
NEWS_OBJECTIVE_FACT_PROMPT_VERSION = os.getenv(
    "NEWS_OBJECTIVE_FACT_PROMPT_VERSION", "objective-news-facts-v1.1"
)
NEWS_EVENT_MERGE_PROMPT_VERSION = os.getenv(
    "NEWS_EVENT_MERGE_PROMPT_VERSION", "same-event-v1.2"
)
NEWS_EVENT_MERGE_ALGORITHM_VERSION = os.getenv(
    "NEWS_EVENT_MERGE_ALGORITHM_VERSION", "complete-link-v1.2"
)
NEWS_EVENT_MERGE_WINDOW_DAYS = int(
    os.getenv("NEWS_EVENT_MERGE_WINDOW_DAYS", "14")
)
NEWS_EVENT_MERGE_MAX_CANDIDATES = int(
    os.getenv("NEWS_EVENT_MERGE_MAX_CANDIDATES", "5")
)
NEWS_EVENT_MERGE_MIN_RECALL_SCORE = float(
    os.getenv("NEWS_EVENT_MERGE_MIN_RECALL_SCORE", "0.20")
)
NEWS_EVENT_MERGE_AUTO_THRESHOLD = float(
    os.getenv("NEWS_EVENT_MERGE_AUTO_THRESHOLD", "0.85")
)

MEME_MONITOR_NETWORK = os.getenv("MEME_MONITOR_NETWORK", "bsc").strip()
MEME_MONITOR_CHAIN = os.getenv("MEME_MONITOR_CHAIN", "BSC").strip()
MEME_MONITOR_GECKOTERMINAL_BASE_URL = os.getenv(
    "MEME_MONITOR_GECKOTERMINAL_BASE_URL",
    "https://api.geckoterminal.com/api/v2",
).rstrip("/")
MEME_MONITOR_NEW_PAIR_MAX_AGE_HOURS = float(
    os.getenv("MEME_MONITOR_NEW_PAIR_MAX_AGE_HOURS", "24")
)
MEME_MONITOR_POLL_INTERVAL_SECONDS = float(
    os.getenv("MEME_MONITOR_POLL_INTERVAL_SECONDS", "30")
)
MEME_MONITOR_COOLDOWN_SECONDS = float(os.getenv("MEME_MONITOR_COOLDOWN_SECONDS", "600"))
MEME_MONITOR_BOOTSTRAP_DISCOVERY_PAGES = int(
    os.getenv("MEME_MONITOR_BOOTSTRAP_DISCOVERY_PAGES", "10")
)
MEME_MONITOR_MAX_TRACKED_PAIRS = int(os.getenv("MEME_MONITOR_MAX_TRACKED_PAIRS", "200"))
MEME_MONITOR_PRICE_CHANGE_5M_PCT = float(
    os.getenv("MEME_MONITOR_PRICE_CHANGE_5M_PCT", "30")
)
MEME_MONITOR_MIN_VOLUME_5M_USD = float(
    os.getenv("MEME_MONITOR_MIN_VOLUME_5M_USD", "5000")
)
MEME_MONITOR_VOLUME_SPIKE_MULTIPLIER = float(
    os.getenv("MEME_MONITOR_VOLUME_SPIKE_MULTIPLIER", "3")
)
MEME_MONITOR_VOLUME_HISTORY_SAMPLES = int(
    os.getenv("MEME_MONITOR_VOLUME_HISTORY_SAMPLES", "10")
)
MEME_MONITOR_VOLUME_HISTORY_MIN_SAMPLES = int(
    os.getenv("MEME_MONITOR_VOLUME_HISTORY_MIN_SAMPLES", "3")
)
MEME_MONITOR_MIN_TRANSACTIONS_5M = int(
    os.getenv("MEME_MONITOR_MIN_TRANSACTIONS_5M", "20")
)
MEME_MONITOR_MIN_LIQUIDITY_USD = float(
    os.getenv("MEME_MONITOR_MIN_LIQUIDITY_USD", "5000")
)
MEME_MONITOR_HTTP_TIMEOUT_SECONDS = float(
    os.getenv("MEME_MONITOR_HTTP_TIMEOUT_SECONDS", "15")
)
MEME_MONITOR_HTTP_MAX_RETRIES = int(os.getenv("MEME_MONITOR_HTTP_MAX_RETRIES", "2"))
MEME_MONITOR_MIN_REQUEST_INTERVAL_SECONDS = float(
    os.getenv("MEME_MONITOR_MIN_REQUEST_INTERVAL_SECONDS", "2.1")
)
MEME_RESEARCH_RULE_VERSION = os.getenv(
    "MEME_RESEARCH_RULE_VERSION", "launchpad_5m_v1"
).strip()
MEME_RESEARCH_ENTRY_DELAY_SECONDS = int(
    os.getenv("MEME_RESEARCH_ENTRY_DELAY_SECONDS", "30")
)
MEME_RESEARCH_HORIZON_SECONDS = int(
    os.getenv("MEME_RESEARCH_HORIZON_SECONDS", "300")
)
MEME_RESEARCH_OBSERVATION_TOLERANCE_SECONDS = int(
    os.getenv("MEME_RESEARCH_OBSERVATION_TOLERANCE_SECONDS", "180")
)
MEME_RESEARCH_NOTIONAL_USD = float(os.getenv("MEME_RESEARCH_NOTIONAL_USD", "100"))
MEME_RESEARCH_FEE_BPS_PER_SIDE = float(
    os.getenv("MEME_RESEARCH_FEE_BPS_PER_SIDE", "30")
)
MEME_RESEARCH_MAX_PRICE_IMPACT_PCT = float(
    os.getenv("MEME_RESEARCH_MAX_PRICE_IMPACT_PCT", "5")
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "meme_monitor": {"format": "[{levelname}] {message}", "style": "{"},
    },
    "handlers": {
        "meme_monitor_console": {
            "class": "logging.StreamHandler",
            "formatter": "meme_monitor",
        }
    },
    "loggers": {
        "apps.meme_monitor": {
            "handlers": ["meme_monitor_console"],
            "level": "INFO",
            "propagate": False,
        }
    },
}

NEWS_COLLECTOR_USER_AGENT = os.getenv(
    "NEWS_COLLECTOR_USER_AGENT",
    "MarketEvidenceLab/1.0 jackywangcode@gmail.com",
)
SOURCE_PROXY_URL = os.getenv(
    "SOURCE_PROXY_URL",
    os.getenv(
        "NEWS_SOURCE_PROXY_URL",
        os.getenv("BLS_NEWS_PROXY_URL", ""),
    ),
).strip()
MEME_MONITOR_PROXY_URL = os.getenv(
    "MEME_MONITOR_PROXY_URL",
    SOURCE_PROXY_URL,
).strip()
MICROSTRUCTURE_WS_PROXY_URL = os.getenv(
    "MICROSTRUCTURE_WS_PROXY_URL",
    SOURCE_PROXY_URL,
).strip()
# Backwards-compatible alias for existing deployments.
NEWS_SOURCE_PROXY_URL = SOURCE_PROXY_URL
SEC_NEWS_USER_AGENT = os.getenv(
    "SEC_NEWS_USER_AGENT",
    "MarketEvidenceLab/1.0 jackywangcode@gmail.com",
)
SEC_NEWS_MIN_REQUEST_INTERVAL_SECONDS = float(
    os.getenv("SEC_NEWS_MIN_REQUEST_INTERVAL_SECONDS", "1.0")
)
TETHER_NEWS_MIN_REQUEST_INTERVAL_SECONDS = float(
    os.getenv("TETHER_NEWS_MIN_REQUEST_INTERVAL_SECONDS", "1.0")
)
SLOWMIST_HACKED_MIN_REQUEST_INTERVAL_SECONDS = float(
    os.getenv("SLOWMIST_HACKED_MIN_REQUEST_INTERVAL_SECONDS", "1.0")
)
CIRCLE_PRESSROOM_MIN_REQUEST_INTERVAL_SECONDS = float(
    os.getenv("CIRCLE_PRESSROOM_MIN_REQUEST_INTERVAL_SECONDS", "1.0")
)
