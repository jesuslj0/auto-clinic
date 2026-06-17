ANTES DE ESCRIBIR CÓDIGO, anclate en el estado REAL del repo. El proyecto ha 
cambiado recientemente, así que NO confíes en suposiciones sobre cómo está montado.

1. Abre y lee el estado actual de SOLO lo que esta tarea toca:
   - El modelo afectado (Patient / Appointment): campos reales, choices de status, 
     restricciones (unique_together), y si tiene métodos de negocio propios.
   - El services.py de esa app: qué funciones de creación existen ya.
   - El ViewSet/serializer correspondiente: cómo crea hoy ese recurso vía API, y 
     si esa lógica ya vive en un service o todavía está en el serializer/view.
   - Las views de plantilla existentes de esa app y alguna CreateView ya hecha que 
     puedas usar como patrón.
   - El fichero de convenciones del proyecto (CLAUDE.md o equivalente) por si las 
     reglas cambiaron.

2. Si algo de lo que asume el prompt de abajo ya no coincide con el código real 
   (nombres de vistas, campos, rutas /api/, dónde vive la lógica de 
   creación), AJÚSTATE a lo que hay en el repo y dímelo explícitamente antes de 
   continuar. No reimplementes algo que ya exista.

3. Solo entonces ejecuta la tarea siguiente:

Trabajas en el repo clinic-app (Django 5.x + DRF). Implementa el alta manual de 
citas desde el panel web. PatientCreateView ya está implementado y puedes apoyarte 
en él.

Contexto y reglas que NO se rompen:
- Modelo Appointment: clinic (FK), patient (FK), service (FK), scheduled_at, end_at, 
  status (choices: pending, confirmed, cancelled, completed, no_show), flags 
  reminder_sent / confirmation_sent.
- La lógica de negocio va SIEMPRE en services.py. La view solo orquesta.
- El status se asigna/cambia SOLO a través de método del modelo o del service, 
  NUNCA con .update() ni asignación directa en la view.
- Todo se almacena en UTC. La hora se introduce en el timezone de la clínica 
  (clinic.timezone) y se convierte a UTC en la capa de servicio/form antes de guardar.
- El endpoint available-slots aún NO existe, así que NO valides disponibilidad contra 
  él. El staff elige fecha/hora manualmente y la cita entra como 'pending'.

Tareas:
1. En appointments/services.py reutiliza la función de servicio que ya usa el 
   AppointmentViewSet/serializer al crear una cita por POST /api/appointments/. Si esa 
   lógica no está aún en un service, extráela a create_appointment(...) y haz que API 
   y form la compartan. La cita debe nacer en status 'pending' por esa vía. NO crees 
   la cita en la view.
2. En appointments/forms.py crea AppointmentForm SIN el campo clinic (se inyecta desde 
   request.user.clinic). El campo service debe filtrarse a los servicios de esa clínica. 
   El campo patient permite seleccionar un paciente existente de la clínica; añade un 
   enlace visible a PatientCreateView para dar de alta uno nuevo si no existe (que al 
   volver deje el form de cita recuperable o prerrellenado por query string).
3. La view AppointmentCreateView (GET/POST) debe aceptar valores iniciales por query 
   string DESDE EL PRINCIPIO: scheduled_at (o date + time), patient, service. Esto es 
   obligatorio: en el siguiente paso el calendario enlazará a esta misma URL con la 
   fecha/hora prerrellenadas, y no quiero reabrir la view entonces.
4. Calcula end_at a partir de service.duration_minutes si no se especifica.
5. URL en appointments/urls.py como /appointments/crear/, acceso autenticado 
   (staff o admin). En éxito redirige al calendario o al detalle de la cita.
6. Plantilla coherente con el resto del panel.
7. Tests imprescindibles: que el POST válido crea la cita vía service y NO con 
   .objects.create() en la view; que el status resultante es 'pending'; que scheduled_at 
   se guarda en UTC partiendo de la hora local de la clínica; que service y patient 
   ajenos a la clínica del usuario son rechazados; que los valores iniciales por query 
   string prerrellenan el formulario.
