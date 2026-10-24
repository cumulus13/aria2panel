#!/usr/bin/env python3

# File: aria2panel/settings.py
# Author: Hadi Cahyadi <cumulus13@gmail.com>
# Date: 2026-10-19
# Description: 
# License: MIT

"""Settings. Only bootstrap configuration lives in the environment (DB url, secret
key, hosts); everything else is stored in the database, see core/conf.py."""
import os
from pathlib import Path
from envdot import load_env

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

#from .env import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_env(str(BASE_DIR / '.env'))
# `python manage.py ...` / gunicorn outside Docker: read BASE_DIR/.env (docker compose passes it itself)
#load_dotenv(BASE_DIR / ".env")


def env(name, default=None, cast_type=None):
    return os.getenv(name, default, cast_type=cast_type)


def env_bool(name, default=False):
    return os.getenv(name, default, cast_type=bool)

def env_list(name, default=[]):
    return os.getenv(name, default, cast_type=list)


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if DEBUG or env_bool("DJANGO_ALLOW_INSECURE_KEY"):
        SECRET_KEY = "insecure-development-key-do-not-use-in-production"
    else:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required (see .env.example)")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env("DJANGO_CSRF_TRUSTED_ORIGINS", [], cast_type=list) or []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "logs"
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "aria2panel.urls"
WSGI_APPLICATION = "aria2panel.wsgi.application"

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
                "core.context.panel",
            ],
        },
    },
]

_db = dj_database_url.config(env="DATABASE_URL", conn_max_age=0, conn_health_checks=True) # 0 for pgboucer and 60 for postgresql
if not _db:
    raise ImproperlyConfigured(
        "DATABASE_URL is required, e.g. postgres://user:password@host:5432/dbname "
        "(percent-encode special characters in the password)"
    )
DATABASES = {"default": _db}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("DJANGO_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    # shared between processes/containers (login throttling), table made by `manage.py createcachetable`
    "shared": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "core_cache"},
}

# Hardening. The CSRF token is delivered through a <meta> tag, so the cookie can be HttpOnly.
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", False)
SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = int(env("DJANGO_HSTS_SECONDS", "0"))
if env_bool("DJANGO_BEHIND_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

from logs.settings import LOGGING as logging

LOG_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, env('DJANGO_LOG_FILE_PATH', 'debug.log'))

LOGGING = logging(BASE_DIR, LOG_FILE)

# LOGGING = {
#     "version": 1,
#     "disable_existing_loggers": False,
#     "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
#     "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
#     "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
#     "loggers": {"django.server": {"level": "WARNING"}},
# }

# LOGGING = {
#     'version': 1,
#     'disable_existing_loggers': False,
#     'formatters': {
#         'colored_verbose': {
#             '()': 'colorlog.ColoredFormatter',
#             'format': "%(log_color)s%(levelname)-8s%(red)s%(module)-30s%(reset)s %(blue)s%(message)s"
#         },
#         'verbose': {
#             'format': '%(asctime)s [PID:%(process)d] [TID:%(thread)d] [%(filename)s:%(lineno)d] %(levelname)s %(message)s',
#         },
#     },
#     'handlers': {
#         'file': {
#             'level': 'DEBUG',
#             'class': 'logging.FileHandler',
#             'filename': LOG_FILE,
#         },
#         'console': {
#             'level':'INFO',
#             'class':'rich.logging.RichHandler',
#         },
#         'colored_console': {
#             'level': 'DEBUG',
#             'class': 'logging.StreamHandler',
#             'formatter': 'colored_verbose'
#         },
#         'syslog': {
#             #'class': 'logging.handlers.SysLogHandler',
#             'class': 'logs.handler.SysLogTextHandler',
#             'address': (env("SYSLOG_PROXY_HOST", '222.222.222.7'), env("SYSLOG_PROXY_PORT", 514)),
#             'formatter': env('DJANGO_SYSLOG_PROXY_LOG_FORMAT', 'verbose'),
#             "env": env('DJANGO_SYSLOG_PROXY_LOG_ENV', "production"),
#         },
#     },
#     'loggers': {
#         'django': {
#             'handlers': ['console', 'file'],
#             'level': 'WARNING',
#             'propagate': True,
#             },
#         'gunicorn.error': {
#             'handlers': ['console', 'syslog'],
#             'level': 'ERROR',
#             'propagate': True,
#         },
#         'gunicorn.access': {
#             'handlers': ['console', 'syslog'],
#             'level': 'WARNING',
#             'propagate': False,
#         },
#         "root": {
#             "handlers": ["console", 'syslog'], 
#             "level": env("DJANGO_LOG_LEVEL", str(env("LOG_LEVEL", "INFO")).upper())
#         },
#     }
# }

