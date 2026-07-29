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

# Los adjuntos clínicos van a memoria: ni red hacia R2 ni ficheros sueltos en el
# repo. Se conserva el backend como una entrada aparte de `default`, que es lo
# que se está probando: que el adjunto NO usa el media público. El test que
# comprueba la firma de la URL sí monta el S3Storage real (sin salir a la red:
# boto3 firma en local).
STORAGES = {
    **STORAGES,
    'clinical_media': {
        'BACKEND': 'django.core.files.storage.InMemoryStorage',
        'OPTIONS': {'base_url': '/test-clinical-media/'},
    },
}

# Enable pagination so API tests can rely on response.data["results"]
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 100,
}
