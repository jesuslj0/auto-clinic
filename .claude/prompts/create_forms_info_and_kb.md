Eres el desarrollador principal de clinic-app (Django 5.x + DRF + Django Templates).
Debes implementar dos secciones nuevas en el panel web (dashboard): gestión de
información de la clínica y gestión de la Knowledge Base. Ambas son vistas Django
Templates, no API. La API REST ya existe y no se toca.

---

## CONTEXTO DEL PROYECTO

Los modelos y endpoints relevantes ya existen:
- Modelo `Clinic` con campos: name, phone, address, timezone, is_active,
  uses_external_software, external_api_url, n8n_webhook_url
- Modelo `ClinicKnowledgeBase` con campos: clinic (FK), kb_type, title,
  content, active — accesible via `GET/POST/PATCH /api/knowledge/entries/`
- Los kb_type válidos son: services, pricing, schedule, faq, location,
  policies, team
- El usuario autenticado pertenece a una clínica (request.user.clinic)
- Las vistas del dashboard heredan de base.html con {% block %} y siguen
  el patrón del resto del proyecto (ver AppointmentCalendarView,
  PatientListView como referencia de estilo)
- Permisos: `IsClinicAdminOrReadOnly` para clínica, `IsClinicAdminOrReadOnly`
  para knowledge base

---

## VISTAS A CREAR

### SECCIÓN 1: Información de la clínica

#### Vista 1: `ClinicInfoView` — solo lectura
- URL: `/clinic/info/`
- Muestra todos los campos del modelo Clinic en formato de ficha
- Incluye un botón "Editar información" que lleva a la vista de edición
- Si `uses_external_software` es True, muestra también `external_api_url`
- Muestra `n8n_webhook_url` solo si existe

#### Vista 2: `ClinicEditView` — formulario de edición
- URL: `/clinic/edit/`
- Formulario con todos los campos editables de Clinic:
  name, phone, address, timezone, uses_external_software,
  external_api_url, n8n_webhook_url
- El campo `timezone` es un select con las zonas horarias más comunes
  de España y Europa (no hace falta listar todas las del mundo):
  Europe/Madrid, Europe/London, Europe/Paris, Europe/Berlin,
  Europe/Rome, Europe/Lisbon, Atlantic/Canary
- El campo `external_api_url` solo se muestra si `uses_external_software`
  está marcado (usa un pequeño script JS para mostrarlo/ocultarlo,
  sin librerías externas)
- Al guardar exitosamente redirige a `/clinic/info/` con mensaje de éxito
- Usa Django ModelForm, la validación va en el form, no en la view

### SECCIÓN 2: Knowledge Base

#### Vista 3: `KnowledgeBaseListView` — listado
- URL: `/knowledge/`
- Lista todas las entradas de ClinicKnowledgeBase de la clínica del
  usuario autenticado
- Agrupa las entradas por kb_type (una sección por categoría)
- Cada entrada muestra: título, primeros 100 caracteres del contenido,
  badge de activo/inactivo, botones de editar y eliminar
- Botón "Nueva entrada" en la cabecera
- Los kb_type se muestran con etiqueta legible en español:
  services → Servicios, pricing → Tarifas, schedule → Horarios,
  faq → Preguntas frecuentes, location → Ubicación,
  policies → Políticas, team → Equipo

#### Vista 4: `KnowledgeBaseCreateView` — crear entrada
- URL: `/knowledge/crear/`
- Formulario con campos: kb_type (select con todas las categorías),
  title, content (textarea grande), active (checkbox, por defecto True)
- El campo `clinic` se asigna automáticamente desde request.user.clinic,
  nunca lo elige el usuario
- Al guardar redirige a `/knowledge/` con mensaje de éxito

#### Vista 5: `KnowledgeBaseEditView` — editar entrada
- URL: `/knowledge/<int:pk>/editar/`
- Mismo formulario que crear pero pre-rellenado
- Solo permite editar entradas de la clínica del usuario (verificar en
  el queryset del get_object, devolver 404 si no pertenece)
- Al guardar redirige a `/knowledge/` con mensaje de éxito

#### Vista 6: `KnowledgeBaseDeleteView` — eliminar entrada
- URL: `/knowledge/<int:pk>/eliminar/`
- Solo POST (no GET, sin página de confirmación separada)
- Implementar como un formulario con un botón en la lista que hace POST
  directamente usando {% csrf_token %}
- Verifica que la entrada pertenece a la clínica del usuario
- Redirige a `/knowledge/` tras eliminar

---

## ARCHIVOS A CREAR O MODIFICAR

### `apps/clinics/forms.py`
- `ClinicForm(ModelForm)`: campos name, phone, address, timezone,
  uses_external_software, external_api_url, n8n_webhook_url
- Validación: si uses_external_software es True y external_api_url
  está vacío, error de validación

### `apps/clinics/views.py` (añadir, no reemplazar)
- `ClinicInfoView(LoginRequiredMixin, TemplateView)`
- `ClinicEditView(LoginRequiredMixin, UpdateView)`
  - El object es siempre `request.user.clinic`
  - No necesita pk en la URL

### `apps/clinics/urls.py` (añadir)
```python
path('info/', ClinicInfoView.as_view(), name='clinic-info'),
path('edit/', ClinicEditView.as_view(), name='clinic-edit'),
```

### `apps/knowledge/forms.py` (crear si no existe)
- `KnowledgeBaseForm(ModelForm)`: campos kb_type, title, content, active
- El campo kb_type es un Select con las 7 categorías como choices
- El campo content usa un widget Textarea con attrs={'rows': 8}

### `apps/knowledge/views.py` (añadir)
- `KnowledgeBaseListView(LoginRequiredMixin, ListView)`
  - queryset filtrado por `clinic=request.user.clinic`
  - en get_context_data agrupa entradas por kb_type con un dict
- `KnowledgeBaseCreateView(LoginRequiredMixin, CreateView)`
  - en form_valid asigna `form.instance.clinic = request.user.clinic`
- `KnowledgeBaseEditView(LoginRequiredMixin, UpdateView)`
  - get_queryset filtra por clinic del usuario
- `KnowledgeBaseDeleteView(LoginRequiredMixin, View)`
  - solo método POST, verifica pertenencia, llama a .delete()

### `apps/knowledge/urls.py` (añadir)
```python
path('', KnowledgeBaseListView.as_view(), name='knowledge-list'),
path('crear/', KnowledgeBaseCreateView.as_view(), name='knowledge-create'),
path('<int:pk>/editar/', KnowledgeBaseEditView.as_view(), name='knowledge-edit'),
path('<int:pk>/eliminar/', KnowledgeBaseDeleteView.as_view(), name='knowledge-delete'),
```

### Templates a crear

`templates/clinics/clinic_info.html`
- Extiende base.html
- Ficha con los datos de la clínica en formato legible
- Botón editar

`templates/clinics/clinic_edit.html`
- Extiende base.html
- Formulario con los campos
- Script JS inline para mostrar/ocultar external_api_url según checkbox
- Botones guardar y cancelar

`templates/knowledge/knowledge_list.html`
- Extiende base.html
- Secciones agrupadas por categoría con su etiqueta en español
- Cada entrada con sus botones
- Formularios de eliminación inline con {% csrf_token %}

`templates/knowledge/knowledge_form.html`
- Extiende base.html, sirve para crear y editar
- El título cambia según si es crear o editar
  (`{% if form.instance.pk %}Editar{% else %}Nueva{% endif %}`)

---

## RESTRICCIONES

- No toques ningún ViewSet DRF existente ni ninguna URL bajo `/api/`
- No uses JavaScript frameworks ni librerías externas adicionales
- Los mensajes de éxito usan el sistema de mensajes de Django
  (`messages.success(request, '...')`) y se muestran en el template
  base (si ya existe el bloque de mensajes en base.html, úsalo;
  si no, añádelo en los templates nuevos)
- Sigue el mismo estilo visual que el resto del dashboard (mismas
  clases CSS, mismos componentes)
- No añadas lógica de negocio en las views; si hay algo más complejo
  que un simple queryset, crea un método en services.py
- Ejecuta `ruff check . && ruff format .` al terminar y corrige errores