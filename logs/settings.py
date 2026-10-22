#!/usr/bin/env python3

# File: logs/settings.py
# Author: Hadi Cahyadi <cumulus13@gmail.com>
# Date: 2026-10-22
# Description: logging settings for django
# License: MIT

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'colored_verbose': {
            '()': 'colorlog.ColoredFormatter',
            'format': "%(log_color)s%(levelname)-8s%(red)s%(module)-30s%(reset)s %(blue)s%(message)s"
        },
        'verbose': {
            'format': '%(asctime)s [PID:%(process)d] [TID:%(thread)d] [%(filename)s:%(lineno)d] %(levelname)s %(message)s',
        },
    },
    'handlers': {
        'file': {
            'level': os.getenv("HANDLER_FILE_LOG_LEVEL", 'ERROR'),
            'class': 'logging.FileHandler',
            'filename': LOG_FILE,
        },
        'console': {
            'level': os.getenv("HANDLER_CONSOLE_LOG_LEVEL", 'INFO'),
            'class':'rich.logging.RichHandler',
        },
        'colored_console': {
            'level': os.getenv("HANDLER_COLORED_CONSOLE_LOG_LEVEL", 'INFO'),
            'class': 'logging.StreamHandler',
            'formatter': 'colored_verbose'
        },
        'syslog': {
            #'class': 'logging.handlers.SysLogHandler',
            'class': 'logs.handler.SysLogTextHandler',
            'address': (env("SYSLOG_PROXY_HOST", '222.222.222.7'), env("SYSLOG_PROXY_PORT", 514)),
            'formatter': env('DJANGO_SYSLOG_PROXY_LOG_FORMAT', 'verbose'),
            "env": env('DJANGO_SYSLOG_PROXY_LOG_ENV', "production"),
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': os.getenv("LOGGER_DJANGO_LOG_LEVEL", 'INFO'),
            'propagate': True,
            },
        'gunicorn.error': {
            'handlers': ['console', 'syslog'],
            'level': os.getenv("LOGGER_GUNICORN_ERROR_LOG_LEVEL", 'ERROR'),
            'propagate': True,
        },
        'gunicorn.access': {
            'handlers': ['console', 'syslog'],
            'level': os.getenv("LOGGER_GUNICORN_ACCESS_LOG_LEVEL", 'WARNING'),
            'propagate': False,
        },
        "root": {
            "handlers": ["console", 'syslog'], 
            "level": env("DJANGO_LOG_LEVEL", str(env("LOG_LEVEL", "INFO")).upper())
        },
        'celery': {
            'handlers': ['console', 'file', 'syslog'], 
            'level': 'WARNING', 
            'propagate': False, 
        },        
        'celery.worker': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'celery.task': {
            'handlers': ['console'],
            'level': 'WARNING', 
            'propagate': False,
        },
        'celery.beat': {
            'handlers': ['console'],
            'level': 'WARNING', 
            'propagate': False,
        }
    }
}