from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parents[2]

SECRET_KEY = config('SECRET_KEY', default='dummy-key-for-collectstatic')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='127.0.0.1,localhost', cast=Csv())
CSRF_TRUSTED_ORIGINS = config(
    'CSRF_TRUSTED_ORIGINS',
    default='http://127.0.0.1:8000,http://localhost:8000',
    cast=Csv(),
)

INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework.authtoken',
    'django_filters',
    'corsheaders',
    'drf_spectacular',
    'channels',
    'core',
    'patients',
    'services',
    'appointments',
    'booking',
    'notifications',
    'billing',
    'knowledge',
    'agent',
    'audit',
    'clinical',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # Después de AuthenticationMiddleware: necesita `request.user`.
    'audit.middleware.AuditContextMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    }
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('POSTGRES_DB', default='clinic'),
        'USER': config('POSTGRES_USER', default='clinic'),
        'PASSWORD': config('POSTGRES_PASSWORD', default='clinic'),
        'HOST': config('POSTGRES_HOST', default='localhost'),
        'PORT': config('POSTGRES_PORT', default='5432'),
    }
}

AUTH_USER_MODEL = 'core.User'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

LANGUAGE_CODE = 'es'
TIME_ZONE = 'Europe/Madrid'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# --- Almacenamiento ---------------------------------------------------------
#
# `default` es el media público de siempre (logos de clínica y poco más).
# `clinical_media` es OTRO backend, deliberadamente separado: guarda fotos de
# lesiones, que son datos de salud. Nunca debe servirse desde MEDIA_URL ni desde
# ninguna ruta pública — se accede solo con una URL firmada de vida corta que
# genera `clinical/attachments.py` tras comprobar permisos.
#
# El bucket es de Cloudflare R2 (S3-compatible) y es PRIVADO: `default_acl=None`
# (jamás `public-read`) y `querystring_auth=True`, de modo que sin firma no hay
# lectura posible ni aunque alguien acierte la clave del objeto.
R2_ACCESS_KEY_ID = config('R2_ACCESS_KEY_ID', default='')
R2_SECRET_ACCESS_KEY = config('R2_SECRET_ACCESS_KEY', default='')
R2_BUCKET_NAME = config('R2_BUCKET_NAME', default='')
R2_ENDPOINT_URL = config('R2_ENDPOINT_URL', default='')

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
    'clinical_media': {
        'BACKEND': 'storages.backends.s3.S3Storage',
        'OPTIONS': {
            'access_key': R2_ACCESS_KEY_ID,
            'secret_key': R2_SECRET_ACCESS_KEY,
            'bucket_name': R2_BUCKET_NAME,
            'endpoint_url': R2_ENDPOINT_URL,
            # R2 no tiene regiones al estilo de S3; «auto» es lo que espera.
            'region_name': 'auto',
            'signature_version': 's3v4',
            'addressing_style': 'virtual',
            # Privado: sin ACL pública y con firma obligatoria en cada lectura.
            'default_acl': None,
            'querystring_auth': True,
            'querystring_expire': config(
                'CLINICAL_MEDIA_URL_EXPIRE', default=600, cast=int
            ),
            # Una clave nunca se pisa: cada adjunto tiene la suya (un UUID) y
            # sobrescribir una existente sería perder documentación clínica.
            'file_overwrite': False,
        },
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'core.authentication.AgentClinicKeyAuthentication',
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 100,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

# CORS — add your n8n instance URL here
CORS_ALLOWED_ORIGINS = config(
    'CORS_ALLOWED_ORIGINS',
    default='http://localhost:5678',
    cast=Csv(),
)
CORS_ALLOW_METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']
CORS_ALLOW_HEADERS = ['authorization', 'content-type']

# drf-spectacular
SPECTACULAR_SETTINGS = {
    'TITLE': 'Auto Clinic API',
    'DESCRIPTION': 'REST API for Auto Clinic — compatible with n8n workflows.',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

REDIS_URL = config('REDIS_URL', default='redis://localhost:6379/0')
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {'hosts': [REDIS_URL]},
    }
}

CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='redis://localhost:6379/1')
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default='redis://localhost:6379/2')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    # Libera los huecos de las citas que el staff no validó dentro del plazo.
    # Es lo que impide que una reserva del agente que nadie mira bloquee el hueco
    # para siempre. Lo ejecuta el servicio `celery-beat` del docker-compose.
    'expire-appointment-holds': {
        'task': 'appointments.tasks.expire_appointment_holds',
        'schedule': 600.0,
    },
    # Los recordatorios al paciente (24h/3h) los manda n8n por WhatsApp, no Django:
    # consume `GET /api/appointments/pending-reminders/`. Las tareas
    # `notifications.tasks.dispatch_*_reminders` (que enviaban un email paralelo)
    # se dejan fuera del schedule a propósito para no duplicar el aviso. El código
    # sigue en `notifications/tasks.py` por si algún día se quiere el email.
}

AGENT_MASTER_API_KEY = config('AGENT_MASTER_API_KEY', default='')

# URL del webhook de n8n donde Meta entrega los mensajes entrantes de WhatsApp.
# Es única y global: n8n resuelve la clínica por el phone_number_id del mensaje.
WHATSAPP_WEBHOOK_URL = config(
    'WHATSAPP_WEBHOOK_URL',
    default='https://n8n.alt4ir.online/webhook/whatsapp-inbound',
)

# Webhook de n8n para probar el agente desde el panel. Recibe {clinic_id, message}
# y responde de forma síncrona con el texto del agente. Separado del flujo real.
WHATSAPP_TEST_WEBHOOK_URL = config(
    'WHATSAPP_TEST_WEBHOOK_URL',
    default='https://n8n.alt4ir.online/webhook/whatsapp-test',
)

# Qué hacer si no se puede escribir un registro de auditoría (app `audit`).
#   fail_closed → se aborta la operación auditada. Es el valor por defecto:
#     son datos de salud, y un cambio que después no se puede justificar es peor
#     que una operación que falla ahora y se ve.
#   fail_open   → la operación se completa y el fallo se anota en el logger
#     `audit` a nivel CRITICAL. Válvula de escape para una incidencia.
AUDIT_FAILURE_POLICY = config('AUDIT_FAILURE_POLICY', default='fail_closed')

# Plazo de conservación de la historia clínica, en AÑOS, contado desde el alta
# del episodio. Valor por defecto conservador (por encima del mínimo legal de 5
# años de la Ley 41/2002 art. 17); el aplicable depende de la normativa
# autonómica, pendiente de confirmar. Es solo andamiaje de cálculo: no hay purga
# automática. Ver `clinical/conf.py` y el README de la app `clinical`.
CLINICAL_RETENTION_YEARS = config('CLINICAL_RETENTION_YEARS', default=15, cast=int)

# Tamaño máximo de una foto clínica, en BYTES. El segundo límite se aplica a lo
# que llega de fuera de la consulta (el paciente por WhatsApp o por la web): esa
# vía es la que no controlamos, así que va más apretada. Ver `clinical/files.py`.
CLINICAL_ATTACHMENT_MAX_BYTES = config(
    'CLINICAL_ATTACHMENT_MAX_BYTES', default=10 * 1024 * 1024, cast=int
)
CLINICAL_ATTACHMENT_MAX_BYTES_EXTERNAL = config(
    'CLINICAL_ATTACHMENT_MAX_BYTES_EXTERNAL', default=5 * 1024 * 1024, cast=int
)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': 'INFO'},
}
