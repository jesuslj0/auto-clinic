Eres el desarrollador principal de clinic-app (Django 5.x + DRF).
Debes implementar la normalización y validación de teléfonos en formato E.164
en todo el proyecto, sin romper ningún endpoint existente ni el contrato API
con n8n.

---

## CONTEXTO Y PRINCIPIOS

- Django es la fuente de verdad de los datos. La normalización ocurre siempre
  en Django, nunca en n8n.
- La lógica de normalización va en `services.py`, no en modelos ni serializers.
  Los serializers llaman al service para validar.
- Los modelos NO cambian su tipo de campo: siguen siendo CharField(max_length=20).
  Solo cambia lo que se almacena en ellos.
- El formato E.164 es: `+` seguido de prefijo de país y número, sin espacios ni
  guiones. Ejemplos válidos: `+34612345678`, `+12125551234`.
- La API debe ser tolerante en la entrada (acepta `34612345678`, `+34612345678`,
  `612345678` para números españoles) y estricta en el almacenamiento (siempre
  guarda `+34612345678`).
- La búsqueda por teléfono (`GET /api/v1/patients/?phone=`) debe normalizar el
  parámetro antes de buscar, para que `?phone=34612345678` y
  `?phone=+34612345678` devuelvan el mismo resultado.

---

## ARCHIVOS A CREAR O MODIFICAR

### 1. `apps/patients/services.py` — Añadir función de normalización

Crea la función `normalize_phone(phone: str) -> str` con esta lógica:
- Elimina todos los caracteres no numéricos excepto el `+` inicial.
- Si empieza por `+`, toma los dígitos que siguen como número completo.
- Si tiene 9 dígitos y empieza por 6, 7, 8 o 9: asume España, antepone `34`.
- Si ya tiene prefijo de país (10-15 dígitos sin el `+`): usa tal cual.
- Valida que el resultado tenga entre 7 y 15 dígitos tras el `+`.
- Lanza `ValueError` con mensaje descriptivo si no puede normalizar.
- Devuelve siempre el número con `+` al inicio.

También crea `normalize_phone_safe(phone: str) -> str | None` que devuelve
`None` en vez de lanzar excepción, para usar en migraciones de datos.

### 2. `apps/patients/serializers.py` — Añadir validación en serializers

En todos los serializers que contengan el campo `phone` de Patient:
- Añade el método `validate_phone(self, value)` que llama a
  `normalize_phone(value)` del service y captura el `ValueError`
  convirtiéndolo en `serializers.ValidationError`.
- El campo `phone` en el serializer de lectura (list/detail) devuelve
  el valor tal como está almacenado (ya normalizado).
- NO añadas lógica de normalización directamente en el serializer;
  solo llama al service.

Haz lo mismo para el campo `phone` del serializer de `Clinic`.

### 3. `apps/patients/views.py` — Normalizar parámetro de búsqueda

En `PatientViewSet`, si existe un filtro o queryset override para el
parámetro `phone`:
- Antes de usarlo en el queryset, llama a `normalize_phone_safe(phone)`.
- Si devuelve `None` (formato inválido), retorna queryset vacío en vez
  de lanzar error, para que el endpoint devuelva `[]` en lugar de 400.
- Si el filtro se hace con `django-filter`, crea un filtro custom
  `PhoneFilter` en `filters.py` que normalice el valor antes de filtrar.

### 4. `apps/patients/filters.py` — Filtro custom (si usa django-filter)

Crea `PhoneExactFilter(filters.CharFilter)` que en su método `filter()`
normalice el valor con `normalize_phone_safe` antes de aplicar el lookup
`exact` sobre el queryset.

Úsalo en `PatientFilter` para el campo `phone`.

### 5. Migración de datos — management command

Crea `apps/patients/management/commands/normalize_phones.py`:
- Itera todos los `Patient` y todos los `Clinic` con `iterator()`.
- Para cada registro llama a `normalize_phone_safe(phone)`.
- Si el valor normalizado es distinto al actual, actualiza con
  `Model.objects.filter(pk=obj.pk).update(phone=normalized)`.
- Si devuelve None, omite el registro silenciosamente.
- Al final imprime: cuántos actualizados y cuántos omitidos.

### 6. `apps/patients/tests/test_phone_normalization.py` — Tests

Crea tests con pytest para:

`normalize_phone`:
- `+34612345678` → `+34612345678` (ya correcto, sin cambio)
- `34612345678` → `+34612345678` (sin +)
- `612345678` → `+34612345678` (español sin prefijo)
- `6 12 34 56 78` → `+34612345678` (con espacios)
- `+1 212 555 1234` → `+12125551234` (número USA)
- `abc` → ValueError
- `` (vacío) → ValueError
- `123` → ValueError (muy corto)

`PatientSerializer.validate_phone`:
- Acepta `612345678` y normaliza a `+34612345678` en validated_data.
- Rechaza `abc` con error 400 y código de error del proyecto.

`GET /api/v1/patients/?phone=612345678`:
- Devuelve el paciente cuyo phone almacenado es `+34612345678`.

`GET /api/v1/patients/?phone=invalido`:
- Devuelve `[]` (lista vacía), no 400.

`POST /api/v1/patients/` con `phone: "612345678"`:
- Almacena `+34612345678` en base de datos.

---

## RESTRICCIONES IMPORTANTES

- NO uses `django-phonenumber-field` ni ninguna librería externa nueva.
  La lógica es suficientemente simple para implementarla en el service.
- NO cambies el tipo de campo `phone` en los modelos (sigue siendo
  `CharField(max_length=20)`).
- NO crees una migración de esquema. Solo el management command de datos.
- NO modifiques el contrato de respuesta de ningún endpoint: `phone`
  sigue siendo un string en el JSON de respuesta.
- Los errores de validación deben seguir el formato estándar del proyecto:
  `{"error": {"code": "INVALID_PHONE", "message": "...", "details": {}}}`.
- Tras implementar, ejecuta `ruff check .` y `ruff format .` y corrige
  cualquier error antes de dar la tarea por terminada.

---

## ORDEN DE EJECUCIÓN

1. `services.py` — la función de normalización.
2. `filters.py` — el filtro custom.
3. `serializers.py` — validación en serializers.
4. `views.py` — normalización en búsqueda por phone.
5. `tests/test_phone_normalization.py` — tests.
6. Management command `normalize_phones`.
7. Ejecutar tests: `pytest apps/patients/tests/test_phone_normalization.py -v`.
8. Ejecutar linter: `ruff check . && ruff format .`.