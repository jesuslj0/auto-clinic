from decimal import Decimal
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Prefetch, Q, Sum, When
from django.shortcuts import redirect
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from rest_framework import viewsets
from django.urls import reverse, reverse_lazy

from appointments.models import Appointment
from audit.mixins import AccessLogMixin, AuditedViewSetMixin
from clinical.forms import AnamnesisForm
from clinical.models import (
    ClinicalAlert,
    Episode,
    Lesion,
    MedicalHistory,
    QuestionnaireResponse,
    QuestionnaireTemplate,
    PerformedProcedure,
    SignedConsent,
)
from core.authentication import ClinicAgent
from core.mixins import BulkCreateMixin, BulkUpdateMixin, ExportMixin
from core.permissions import IsAgentClinicKey, IsStaffOrAdmin
from patients.filters import PatientFilter
from patients.models import Patient
from patients.serializers import PatientSerializer
from patients.forms import PatientForm
from patients.services import create_patient


class PatientViewSet(
    AuditedViewSetMixin, ExportMixin, BulkCreateMixin, BulkUpdateMixin, viewsets.ModelViewSet
):
    serializer_class = PatientSerializer
    permission_classes = [IsStaffOrAdmin | IsAgentClinicKey]
    search_fields = ["first_name", "last_name", "email", "phone"]
    filterset_class = PatientFilter
    ordering_fields = ['first_name', 'last_name', 'email', 'phone', 'created_at']
    ordering = ['last_name', 'first_name']

    def get_queryset(self):
        queryset = Patient.objects.select_related('clinic')
        user = self.request.user
        if isinstance(user, ClinicAgent):
            return queryset.filter(clinic=user.clinic)
        if not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)


class PatientListView(AccessLogMixin, LoginRequiredMixin, ListView):
    model = Patient
    template_name = 'patients/list.html'
    context_object_name = 'patients'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'patients'
        return context

    def get_queryset(self):
        query = self.request.GET.get('q', '').strip()
        user = self.request.user
        queryset = Patient.objects.annotate(appointment_count=Count('appointments')).prefetch_related('appointments')
        if user.clinic_id:
            queryset = queryset.filter(clinic=user.clinic)
        if query:
            queryset = queryset.filter(
                Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(email__icontains=query)
                | Q(phone__icontains=query)
            )
        return queryset


# ---------------------------------------------------------------------------
# Ficha del paciente (pestañas)
# ---------------------------------------------------------------------------

#: Pestañas de la ficha, en orden de presentación. Vive aquí y no repartido por
#: las plantillas para que añadir una sección en fases posteriores sea una línea
#: (más su ruta y su parcial).
PATIENT_TABS = [
    {'key': 'general', 'label': 'Datos generales', 'url_name': 'patients:detail'},
    {'key': 'anamnesis', 'label': 'Anamnesis', 'url_name': 'patients:tab-anamnesis'},
    {'key': 'alerts', 'label': 'Alertas', 'url_name': 'patients:tab-alerts'},
    {'key': 'lesions', 'label': 'Lesiones', 'url_name': 'patients:tab-lesions'},
    {'key': 'consents', 'label': 'Consentimientos', 'url_name': 'patients:tab-consents'},
    {'key': 'procedures', 'label': 'Procedimientos', 'url_name': 'patients:tab-procedures'},
]

#: Orden de presentación de las alertas: primero lo que puede contraindicar un
#: tratamiento. El campo es texto, así que ordenar por él daría un orden
#: alfabético sin sentido clínico (crítica, informativa, advertencia).
_SEVERITY_RANK = Case(
    When(severity=ClinicalAlert.Severity.CRITICAL, then=0),
    When(severity=ClinicalAlert.Severity.WARNING, then=1),
    default=2,
    output_field=IntegerField(),
)


class PatientTabView(AccessLogMixin, LoginRequiredMixin, DetailView):
    """Base de las seis pestañas de la ficha del paciente.

    Cada pestaña es una URL real y una vista propia, no un parámetro: pegar la
    dirección en el navegador o recargar con F5 llega exactamente al mismo sitio
    que hacer clic. HTMX solo es una mejora encima; si no hay JavaScript, los
    enlaces navegan y todo sigue funcionando.

    La única diferencia entre una petición normal y una de HTMX es la plantilla:
    la primera devuelve la página completa y la segunda solo el fragmento
    intercambiable (barra de pestañas + panel).

    Cada subclase registra su propio `AccessLog` — lo hace `AccessLogMixin`, que
    va delante en las bases. Es obligatorio: esta ficha expone datos clínicos y
    las lecturas no emiten señales. Ninguna de estas rutas es alcanzable con el
    `Api-Key` del agente: son vistas de sesión y la capa clínica no tiene API.

    **Sobre `access_action`:** ahora que cada pestaña enseña datos clínicos
    propios (anamnesis, procedimientos, consentimientos) se ha valorado afinar la
    acción por pestaña, y se ha decidido no hacerlo. `AccessLog.Action` es un
    juego cerrado de valores del modelo de auditoría, y ampliarlo para etiquetar
    pestañas del panel sería meter vocabulario de interfaz en el registro legal.
    No hace falta: el registro ya guarda `path`, así que «qué pestaña se
    consultó» se responde igual, y el sujeto del acceso —el paciente— es el
    mismo en todas. La acción que sí se afina es la de la firma de un
    consentimiento, que no se pinta aquí: se sirve por `clinical:consent-
    signature` y queda como `download_attachment`, que es lo que es.
    """

    model = Patient
    pk_url_kwarg = 'id'
    context_object_name = 'patient'
    template_name = 'patients/detail.html'
    #: Fragmento que se devuelve a HTMX (barra + panel).
    partial_template_name = 'patients/_tab_region.html'
    #: Contenido del panel de esta pestaña.
    panel_template = None
    #: Clave de `PATIENT_TABS` que queda marcada como activa.
    tab = None

    def get_queryset(self):
        # Mismo aislamiento multi-tenant que el resto de vistas del panel: un
        # paciente de otra clínica es un 404, no un 403 (y sin AccessLog: no se
        # ha llegado a leer nada).
        user = self.request.user
        queryset = Patient.objects.select_related('clinic')
        if not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)

    def is_htmx_swap(self):
        """¿Hay que devolver solo el fragmento?

        `HX-History-Restore-Request` se excluye a propósito: cuando el usuario
        vuelve atrás y htmx no tiene la página en su caché, la vuelve a pedir
        con `HX-Request` para restaurar el `body` entero. Devolverle el
        fragmento dejaría la página reducida al panel.
        """
        headers = self.request.headers
        return bool(headers.get('HX-Request')) and not headers.get('HX-History-Restore-Request')

    def get_template_names(self):
        if self.is_htmx_swap():
            return [self.partial_template_name]
        return [self.template_name]

    def get_active_alerts(self):
        """Alertas vigentes del paciente, ordenadas por gravedad.

        Alimenta a la vez la banda superior (visible en todas las pestañas) y la
        pestaña de alertas. Las desactivadas y las borradas lógicamente quedan
        fuera: el manager ya excluye las segundas.
        """
        return list(
            ClinicalAlert.objects.for_patient(self.object)
            .active()
            .annotate(_severity_rank=_SEVERITY_RANK)
            .order_by('_severity_rank', '-created_at', '-id')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'patients'
        context['patient_tabs'] = PATIENT_TABS
        context['active_tab'] = self.tab
        context['panel_template'] = self.panel_template
        alerts = self.get_active_alerts()
        context['active_alerts'] = alerts
        context['has_critical_alerts'] = any(
            alert.severity == ClinicalAlert.Severity.CRITICAL for alert in alerts
        )
        return context


class PatientDetailView(PatientTabView):
    """Pestaña «Datos generales»: la URL histórica de la ficha (`patients:detail`)."""

    tab = 'general'
    panel_template = 'patients/tabs/_general.html'

    def get_queryset(self):
        appointment_queryset = (
            Appointment.objects
            .select_related('service', 'professional__user')
            .order_by('-scheduled_at')
        )
        return super().get_queryset().prefetch_related(
            Prefetch('appointments', queryset=appointment_queryset)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['appointments'] = self.object.appointments.all()
        return context


class PatientAnamnesisTabView(PatientTabView):
    """Pestaña «Anamnesis»: los cuestionarios que contestó el paciente.

    Lo que se pinta es el `snapshot` de cada respuesta —la copia literal de las
    preguntas tal y como se le mostraron ese día—, nunca las `Question` vivas por
    FK. Por eso el queryset no las precarga: no hacen falta para leer la
    respuesta, y traerlas invitaría a usarlas. La FK a `TemplateVersion` sí, pero
    solo para decir de qué documento salió (plantilla y número de versión).

    Solo lectura: una respuesta es inmutable en cuanto existe.
    """

    tab = 'anamnesis'
    panel_template = 'patients/tabs/_anamnesis.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['responses'] = list(
            QuestionnaireResponse.objects
            .filter(patient=self.object)
            .select_related('version', 'version__template', 'created_by__user')
            # El modelo ya ordena por `-filled_at`; el desempate por id evita que
            # dos respuestas de la misma fecha bailen entre recargas.
            .order_by('-filled_at', '-id')
        )
        return context


class PatientAlertsTabView(PatientTabView):
    tab = 'alerts'
    panel_template = 'patients/tabs/_alertas.html'


class PatientLesionsTabView(PatientTabView):
    """Pestaña «Lesiones»: el mapa del pie, en solo lectura.

    **Todas las lesiones de una vez, y el filtrado en el cliente.** La pestaña es
    del PACIENTE, no de un episodio, así que se traen las lesiones de toda su
    historia (`episode__history__patient`) y se pintan las ocho combinaciones de
    vista y pie en el mismo marcado; Alpine solo enseña u oculta la que toca. Con
    el volumen real —unas pocas lesiones por pie— es una consulta contra ocho, y
    cambiar de vista es instantáneo y sin viaje al servidor.

    Por eso NO se usa `Lesion.objects.for_view()`: ese helper filtra por episodio,
    pie y vista, que es la consulta de un mapa de un episodio concreto. Aquí la
    unidad es el paciente y el filtro por vista ocurre después, al pintar. El
    helper sigue siendo el camino correcto cuando el mapa se acote a un episodio.

    Lo que va al contexto son los datos ya resueltos —incluidos los recuentos por
    combinación—, porque contarlos en Alpine sería meter lógica de datos en la
    interfaz. Alpine solo guarda dos cadenas: qué vista y qué pie se están
    mirando.

    Las coordenadas se pasan crudas: `x`/`y` son fracciones (0–1) del SVG y la
    multiplicación por el `viewBox` la hace la plantilla, que es la única que
    sabe cuánto mide el dibujo. La zona anatómica se lee aparte
    (`get_anatomical_zone_display`): es el dato clínico, y no se deriva de las
    coordenadas ni al revés.
    """

    tab = 'lesions'
    panel_template = 'patients/tabs/_lesiones.html'

    def get_lesions(self):
        """Lesiones del paciente, de todos sus episodios. Las borradas, fuera.

        Sin `select_related`: de la lesión solo se pintan campos propios y sus
        `get_*_display`, así que traer el episodio solo serviría para invitar a
        usarlo.
        """
        return list(
            Lesion.objects
            .filter(episode__history__patient=self.object)
            .order_by('-detected_at', '-id')
        )

    def build_scenes(self, lesions):
        """Las ocho combinaciones de vista y pie, con sus recuentos.

        Se generan siempre las ocho, tengan lesiones o no: la plantilla necesita
        poder decir «en esta vista no hay ninguna» sin preguntárselo a nadie.
        """
        counts = {}
        for lesion in lesions:
            entry = counts.setdefault(
                (lesion.view, lesion.laterality),
                {'total': 0, 'active': 0, 'resolved': 0},
            )
            entry['total'] += 1
            if lesion.status == Lesion.Status.ACTIVE:
                entry['active'] += 1
            else:
                entry['resolved'] += 1

        scenes = []
        for view, view_label in Lesion.View.choices:
            for laterality, laterality_label in Lesion.Laterality.choices:
                entry = counts.get(
                    (view, laterality), {'total': 0, 'active': 0, 'resolved': 0}
                )
                scenes.append({
                    'view': view,
                    'view_label': view_label,
                    'laterality': laterality,
                    'laterality_label': laterality_label,
                    **entry,
                })
        return scenes

    def default_scene(self, scenes):
        """Por dónde se abre el mapa: por donde haya algo que ver.

        Abrir siempre en «dorsal, pie izquierdo» dejaría el mapa vacío en la
        mayoría de fichas y obligaría a buscar a mano. Se elige la combinación
        con más lesiones y, en empate, la primera en el orden de los choices
        (`max` devuelve el primer máximo), para que la ficha no baile entre
        recargas. Sin lesiones, la primera combinación.
        """
        return max(scenes, key=lambda scene: scene['total'])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lesions = self.get_lesions()
        scenes = self.build_scenes(lesions)
        opening = self.default_scene(scenes)
        context.update({
            'lesions': lesions,
            'lesion_views': Lesion.View.choices,
            'lesion_lateralities': Lesion.Laterality.choices,
            'lesion_scenes': scenes,
            'default_view': opening['view'],
            'default_laterality': opening['laterality'],
        })
        return context


class PatientConsentsTabView(PatientTabView):
    """Pestaña «Consentimientos»: qué firmó el paciente, y la prueba de ello.

    Las dos pruebas son el texto literal firmado (`text_copy`, que puede no
    parecerse al de la versión vigente) y la imagen de la firma. La firma **no**
    se pinta aquí: vive en el bucket privado y solo se sirve por
    `clinical:consent-signature`, que comprueba el permiso, firma la URL y deja
    su propio `AccessLog` de descarga. La plantilla enlaza a esa vista y nada
    más; nunca a `signature_image.url`.

    `version__template` se precarga porque de la versión se enseñan el nombre de
    la plantilla y el número, no su texto actual.
    """

    tab = 'consents'
    panel_template = 'patients/tabs/_consentimientos.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['consents'] = list(
            SignedConsent.objects
            .filter(patient=self.object)
            .select_related('version', 'version__template')
            .order_by('-signed_at', '-id')
        )
        return context


class PatientProceduresTabView(PatientTabView):
    """Pestaña «Procedimientos»: qué se le hizo al paciente y por cuánto.

    Se llega por la cadena clínica (`visit__episode__history__patient`) y no por
    la propiedad `patient` del modelo, que resolvería la cadena una vez por fila.

    **El catálogo no se toca.** Ni aquí ni en la plantilla se lee `service.name`
    ni `service.price`: lo que se enseña —y lo que se suma— son los campos
    congelados, que es el sentido entero del modelo. Por eso `service` queda
    fuera del `select_related`: no se necesita, y traerlo solo serviría para
    caer en la tentación.

    El total se agrega en la base de datos: sumar en la plantilla obligaría a un
    filtro de acumulación y dejaría el importe a merced del orden de pintado.
    """

    tab = 'procedures'
    panel_template = 'patients/tabs/_procedimientos.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        procedures = (
            PerformedProcedure.objects
            .filter(visit__episode__history__patient=self.object)
            .select_related('visit', 'created_by__user')
            .order_by('-performed_at', '-id')
        )
        context['procedures'] = list(procedures)
        context['procedures_total'] = (
            procedures.aggregate(total=Sum('frozen_price'))['total'] or Decimal('0.00')
        )
        return context


# ---------------------------------------------------------------------------
# Alta de anamnesis
# ---------------------------------------------------------------------------


class PatientAnamnesisCreateView(AccessLogMixin, LoginRequiredMixin, DetailView):
    """Rellenar una anamnesis para un paciente, desde el panel.

    **Siempre la versión vigente.** El formulario se construye sobre
    `template.current_version` y solo sobre ella: un borrador lo rechazaría el
    propio modelo (`TemplateVersionNotPublished`) y una versión antigua sería
    peor todavía, porque colaría sin error una anamnesis obsoleta. Si el
    cuestionario no tiene versión vigente publicada, la página lo dice; no
    revienta.

    El alta va por `QuestionnaireResponse.record()`, nunca por
    `objects.create()`: es la vía que congela el snapshot y dispara el motor de
    alertas. Y se llama SIN `derive=False`, que es justo lo que hace que
    contestar «sí» a la diabetes levante su aviso crítico en la ficha.

    Sin API REST y sin `hx-post`: es un formulario de sesión con CSRF. Esta capa
    no tiene endpoints a propósito, para que el `Api-Key` del agente de n8n no
    llegue hasta aquí. La escritura queda en el `ChangeLog` por las señales de
    auditoría (`QuestionnaireResponse` está registrado en `ClinicalConfig.ready`);
    la lectura de esta pantalla la registra `AccessLogMixin`.
    """

    model = Patient
    pk_url_kwarg = 'id'
    context_object_name = 'patient'
    template_name = 'patients/anamnesis_form.html'
    #: Nombre del parámetro con el que se elige cuestionario cuando hay varios.
    template_param = 'cuestionario'

    def get_queryset(self):
        # Mismo aislamiento multi-tenant que `PatientTabView`: un paciente de
        # otra clínica es un 404.
        user = self.request.user
        queryset = Patient.objects.select_related('clinic')
        if not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)

    # -- resolución del cuestionario y la versión ---------------------------

    def get_questionnaire_templates(self):
        """Cuestionarios activos de la clínica DEL PACIENTE, siempre filtrados."""
        return list(
            QuestionnaireTemplate.objects
            .filter(clinic_id=self.object.clinic_id, is_active=True)
            .order_by('name')
        )

    def resolve_template(self, templates):
        """Cuál se rellena: el pedido, el único que hay, o ninguno (hay que elegir)."""
        requested = self.request.POST.get(self.template_param) or self.request.GET.get(
            self.template_param
        )
        if requested:
            for template in templates:
                if str(template.pk) == str(requested):
                    return template
            return None
        if len(templates) == 1:
            return templates[0]
        return None

    def get_professional(self):
        """El `Professional` del usuario, o `None` si no tiene perfil.

        Un administrativo puede registrar la anamnesis que ha dictado el
        profesional; el modelo admite `created_by` vacío precisamente porque no
        todo canal de entrada tiene profesional detrás.
        """
        return getattr(self.request.user, 'professional_profile', None)

    def get_form(self, version, data=None):
        return AnamnesisForm(data, version=version, patient=self.object)

    # -- render -------------------------------------------------------------

    def get_context_data(self, **kwargs):
        """Estado de la pantalla + formulario.

        No se sobrescribe `get()` a propósito: el `get()` de `AccessLogMixin`
        quedaría eclipsado por el de la subclase y la lectura no se registraría.
        Todo el trabajo cabe aquí, que es donde `DetailView` lo espera.
        """
        context = super().get_context_data(**kwargs)
        templates = self.get_questionnaire_templates()
        template = self.resolve_template(templates)
        version = template.current_version if template else None

        if not templates:
            state = 'sin_cuestionarios'
        elif template is None:
            state = 'elegir_cuestionario'
        elif version is None:
            state = 'sin_version'
        else:
            state = 'formulario'

        context.update({
            'section': 'patients',
            'cancel_url': reverse('patients:tab-anamnesis', kwargs={'id': self.object.pk}),
            'questionnaire_templates': templates,
            'questionnaire_template': template,
            'version': version,
            'state': state,
            'template_param': self.template_param,
        })
        # Un formulario ya ligado (POST con errores) llega por `kwargs` y manda.
        if context.get('form') is None:
            context['form'] = self.get_form(version) if state == 'formulario' else None
        return context

    # -- alta ---------------------------------------------------------------

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        templates = self.get_questionnaire_templates()
        template = self.resolve_template(templates)
        version = template.current_version if template else None

        if version is None:
            # No hay nada que contestar: se vuelve a la página, que explicará por
            # qué (sin cuestionarios, sin versión vigente o hay que elegir uno).
            # El cuestionario pedido viaja de vuelta en la query string para no
            # perder de vista cuál era.
            messages.error(request, 'No hay ningún cuestionario vigente que rellenar.')
            requested = request.POST.get(self.template_param)
            if requested:
                return redirect(
                    f'{request.path}?{urlencode({self.template_param: requested})}'
                )
            return redirect(request.path)

        form = self.get_form(version, data=request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        professional = self.get_professional()
        # El episodio y la respuesta se crean juntos o no se crea ninguno: un
        # fallo a medias dejaría un episodio abierto sin anamnesis dentro.
        with transaction.atomic():
            episode = form.cleaned_data.get('episode') or self.create_episode(
                form.cleaned_data['episode_reason'], professional
            )
            response = QuestionnaireResponse.record(
                version=version,
                patient=self.object,
                episode=episode,
                answers=form.answers(),
                source=QuestionnaireResponse.Source.PROFESSIONAL,
                created_by=professional,
            )

        self.announce(response)
        return redirect('patients:tab-anamnesis', id=self.object.pk)

    def create_episode(self, reason, professional):
        history = MedicalHistory.objects.filter(patient=self.object).first()
        if history is None:
            # Los pacientes de alta desde que existe la capa clínica ya tienen
            # historia (la crea una señal); esto cubre a los anteriores.
            history = MedicalHistory.objects.create(
                patient=self.object, clinic=self.object.clinic
            )
        return Episode.objects.create(
            history=history,
            reason=reason.strip(),
            responsible_professional=professional,
        )

    def announce(self, response):
        """Avisa de lo que ha pasado, incluidas las alertas que se han levantado.

        Contar las alertas derivadas de ESTA respuesta es lo que hace visible que
        el motor ha funcionado: sin este número, la derivación ocurre en silencio
        y hay que irse a otra pestaña para descubrirlo.
        """
        raised = ClinicalAlert.objects.filter(source_response=response).count()
        if raised:
            messages.success(
                self.request,
                f'Anamnesis registrada. Se {"ha" if raised == 1 else "han"} levantado '
                f'{raised} alerta{"s" if raised != 1 else ""} clínica'
                f'{"s" if raised != 1 else ""} en la ficha del paciente.',
            )
        else:
            messages.success(self.request, 'Anamnesis registrada correctamente.')


class PatientCreateView(LoginRequiredMixin, CreateView):
    model = Patient
    form_class = PatientForm
    template_name = 'patients/patient_form.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.is_superuser and not request.user.clinic_id:
            messages.error(request, 'Tu usuario no tiene una clínica asignada.')
            return redirect('patients:list')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'patients'
        context['next_url'] = self.request.GET.get('next', '')
        return context

    def form_valid(self, form):
        try:
            self.object = create_patient(clinic=self.request.user.clinic, **form.cleaned_data)
        except ValueError as exc:
            form.add_error('phone', str(exc))
            return self.form_invalid(form)
        messages.success(self.request, 'Paciente creado correctamente.')
        return redirect(self.get_success_url())

    def get_success_url(self):
        # Si venimos del alta de cita, volvemos a ese formulario con el
        # paciente recién creado preseleccionado por query string.
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            parts = urlparse(next_url)
            query = dict(parse_qsl(parts.query))
            query['patient'] = self.object.pk
            return urlunparse(parts._replace(query=urlencode(query)))
        return reverse_lazy('patients:detail', kwargs={'id': self.object.pk})


class PatientEditView(LoginRequiredMixin, UpdateView):
    model = Patient
    pk_url_kwarg = 'id'
    context_object_name = 'patient'
    template_name = 'patients/edit_patient.html'
    form_class = PatientForm

    def get_queryset(self):
        user = self.request.user
        queryset = Patient.objects.select_related('clinic')
        if not user.clinic_id:
            return queryset
        return queryset.filter(clinic=user.clinic)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = self.form_class(instance=self.object)
        context['section'] = 'patients'
        return context
    
    def form_valid(self, form):
        form.save()
        return super().form_valid(form)
    
    def get_success_url(self):
        return reverse_lazy('patients:detail', kwargs={'id': self.object.pk})
    
