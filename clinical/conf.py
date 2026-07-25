"""Ajustes de la capa clínica.

Ahora mismo, solo el plazo de conservación. Es andamiaje: se usa para *calcular*
cuándo expira la conservación de un episodio, pero NO hay purga automática que
borre nada. El borrado de historia clínica, cuando llegue, será una decisión
explícita y auditada, nunca un efecto secundario de un plazo cumplido.
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
