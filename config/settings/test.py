from .base import *

DEBUG = True
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

ALLOWED_HOSTS = ['*', 'testserver', '127.0.0.1', 'localhost']

# Use in-memory channel layer for tests — no Redis needed
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    }
}

# Use faster password hasher for tests
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]

# Enable pagination so API tests can rely on response.data["results"]
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 100,
}
