"""Ajustes de la capa clínica.

Dos cosas: el plazo de conservación y los límites de los adjuntos.

El plazo de conservación es andamiaje: se usa para *calcular* cuándo expira la
conservación de un episodio, pero NO hay purga automática que borre nada. El
borrado de historia clínica, cuando llegue, será una decisión explícita y
auditada, nunca un efecto secundario de un plazo cumplido.
"""
from django.conf import settings

# Plazo de conservación por defecto, en AÑOS, contado desde el alta del episodio
# (`discharged_at`).
#
# NO se fija en el código el número «correcto» de años porque no existe uno solo:
#   - La Ley 41/2002 (art. 17) obliga a conservar la historia clínica un MÍNIMO
#     de 5 años desde el alta de cada proceso asistencial.
#   - Varias comunidades autónomas amplían ese plazo (hasta 15, 20 o más años
#     según la norma y el tipo de documento). Pendiente de confirmar el aplicable
#     a cada clínica.
#   - El RGPD (art. 5.1.e) exige lo contrario para lo que ya no haga falta:
#     limitar el plazo de conservación.
#
# Por eso el valor por defecto es CONSERVADOR (por encima del mínimo legal) y se
# sobreescribe con `CLINICAL_RETENTION_YEARS` en settings una vez confirmada la
# normativa autonómica.
DEFAULT_RETENTION_YEARS = 15


def retention_years() -> int:
    """Años de conservación por episodio. Override: `CLINICAL_RETENTION_YEARS`."""
    return getattr(settings, 'CLINICAL_RETENTION_YEARS', DEFAULT_RETENTION_YEARS)


# --- Adjuntos clínicos ------------------------------------------------------
#
# Dos límites de tamaño, y no uno, porque hay dos niveles de confianza:
#
#   - Lo que sube el profesional desde la consulta (`professional`) es material
#     controlado: una foto de una cámara o de un móvil de trabajo.
#   - Lo que llega del paciente por WhatsApp o por la web es material de origen
#     ajeno, que además entra por una vía automatizada. Ahí el límite aprieta
#     más: cuanto menos superficie se acepte de fuera, mejor.
#
# El límite es solo la mitad barata del control. La otra —qué es el fichero en
# realidad, no qué dice su extensión— está en `clinical/files.py` y es igual de
# estricta para los dos orígenes.
DEFAULT_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024          # 10 MiB
DEFAULT_ATTACHMENT_MAX_BYTES_EXTERNAL = 5 * 1024 * 1024  # 5 MiB


def attachment_max_bytes(external: bool = False) -> int:
    """Tamaño máximo de un adjunto clínico, en bytes.

    `external=True` para lo que no sube un profesional desde la consulta.
    Overrides: `CLINICAL_ATTACHMENT_MAX_BYTES` y
    `CLINICAL_ATTACHMENT_MAX_BYTES_EXTERNAL`.
    """
    if external:
        return getattr(
            settings,
            'CLINICAL_ATTACHMENT_MAX_BYTES_EXTERNAL',
            DEFAULT_ATTACHMENT_MAX_BYTES_EXTERNAL,
        )
    return getattr(
        settings, 'CLINICAL_ATTACHMENT_MAX_BYTES', DEFAULT_ATTACHMENT_MAX_BYTES
    )


# --- Ficha desde el móvil del paciente (`clinical/intake.py`) ----------------
#
# Todo configurable, con valores conservadores: el enlace es una puerta pública
# a una capa que guarda datos de salud.

#: Horas de vida de un enlace de ficha. Corta a propósito: la ficha se rellena
#: antes de la visita, no semanas después. Override: `PATIENT_FORM_LINK_TTL_HOURS`.
DEFAULT_FORM_LINK_TTL_HOURS = 48

#: Por debajo de esta edad el paciente no consiente por sí mismo y rellena y
#: firma su representante (Ley 41/2002, art. 9.3.c). Override: `INTAKE_MINOR_AGE`.
DEFAULT_MINOR_AGE = 16

#: Tokens desconocidos que se toleran desde una misma IP antes de bloquearla un
#: rato. Con 256 bits de entropía adivinar uno es inviable; esto es para no
#: dejar que nadie lo intente a ciegas ni llene el log de ruido.
DEFAULT_INTAKE_THROTTLE_FAILURES = 20
DEFAULT_INTAKE_THROTTLE_WINDOW_SECONDS = 15 * 60


def form_link_ttl_hours() -> int:
    return getattr(settings, 'PATIENT_FORM_LINK_TTL_HOURS', DEFAULT_FORM_LINK_TTL_HOURS)


def minor_age() -> int:
    return getattr(settings, 'INTAKE_MINOR_AGE', DEFAULT_MINOR_AGE)


def intake_throttle() -> tuple[int, int]:
    """`(fallos permitidos, ventana en segundos)` para los tokens desconocidos."""
    return (
        getattr(settings, 'INTAKE_THROTTLE_FAILURES', DEFAULT_INTAKE_THROTTLE_FAILURES),
        getattr(
            settings, 'INTAKE_THROTTLE_WINDOW_SECONDS', DEFAULT_INTAKE_THROTTLE_WINDOW_SECONDS
        ),
    )
