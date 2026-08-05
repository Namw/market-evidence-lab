"""Django settings for Market Evidence Lab."""

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


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
    "apps.collection.apps.CollectionConfig",
    "apps.inspection.apps.InspectionConfig",
    "apps.scheduling.apps.SchedulingConfig",
    "apps.news_data.apps.NewsDataConfig",
    "apps.news_analysis.apps.NewsAnalysisConfig",
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
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

BINANCE_FUTURES_BASE_URL = os.getenv(
    "BINANCE_FUTURES_BASE_URL",
    "https://fapi.binance.com",
).rstrip("/")

DERIBIT_BASE_URL = os.getenv(
    "DERIBIT_BASE_URL",
    "https://www.deribit.com/api/v2",
).rstrip("/")

NEWS_AI_BASE_URL = os.getenv("NEWS_AI_BASE_URL", "https://api.deepseek.com").rstrip("/")
NEWS_AI_API_KEY = os.getenv("NEWS_AI_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
NEWS_AI_MODEL = os.getenv("NEWS_AI_MODEL", "deepseek-v4-flash")
NEWS_AI_TIMEOUT_SECONDS = float(os.getenv("NEWS_AI_TIMEOUT_SECONDS", "60"))
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

NEWS_COLLECTOR_USER_AGENT = os.getenv(
    "NEWS_COLLECTOR_USER_AGENT",
    "MarketEvidenceLab/1.0 jackywangcode@gmail.com",
)
NEWS_SOURCE_PROXY_URL = os.getenv(
    "NEWS_SOURCE_PROXY_URL",
    os.getenv("BLS_NEWS_PROXY_URL", ""),
).strip()
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
