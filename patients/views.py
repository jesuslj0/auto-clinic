from decimal import Decimal
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Prefetch, Q, Sum, When
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import CreateView, DetailView, ListView, UpdateView
from rest_framework import viewsets
from django.urls import reverse, reverse_lazy

from appointments.models import Appointment
from audit.mixins import AccessLogMixin, AuditedViewSetMixin
from clinical.forms import (
    AnamnesisForm,
    LesionForm,
    LesionObservationForm,
    LesionResolveForm,
)
from clinical.models import (
    ClinicalAlert,
    Episode,
    Lesion,
    LesionAttachment,
    LesionObservation,
    MedicalHistory,
    QuestionnaireResponse,
    QuestionnaireTemplate,
    PerformedProcedure,
    SignedConsent,
    Visit,
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


class PatientScopedMixin:
    """El paciente de la URL, siempre acotado a la clínica del usuario.

    Es el aislamiento multi-tenant de todo el panel: un paciente de otra clínica
    es un **404, no un 403** —y sin `AccessLog`, porque no se ha llegado a leer
    nada—. Vive en un mixin y no copiado en cada vista porque es la comprobación
    que no puede quedarse fuera de ninguna: basta olvidarla una vez.
    """

    def get_queryset(self):
        user = self.request.user
        queryset = Patient.objects.select_related('clinic')
        if not user.clinic_id:
            # Superusuario o usuario sin clínica del panel administrativo.
            return queryset
        return queryset.filter(clinic=user.clinic)


class PatientTabView(PatientScopedMixin, AccessLogMixin, LoginRequiredMixin, DetailView):
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
    """Pestaña «Lesiones»: el mapa del pie.

    Pinta el mapa y su resumen, y es también la base de las dos vistas que lo
    modifican (`PatientLesionMapView` y `PatientLesionCreateView`): las tres
    devuelven la misma región (`patients/_lesion_region.html`) con el mismo
    contexto, así que el mapa no puede acabar diciendo una cosa y sus recuentos
    otra.

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
    interfaz. Alpine solo guarda tres cosas, y ninguna es un dato: qué vista y qué
    pie se están mirando, y si el modo de marcado está activo.

    Las coordenadas se pasan crudas: `x`/`y` son fracciones (0–1) del SVG y la
    multiplicación por el `viewBox` la hace la plantilla, que es la única que
    sabe cuánto mide el dibujo. La zona anatómica se lee aparte
    (`get_anatomical_zone_display`): es el dato clínico, y no se deriva de las
    coordenadas ni al revés.
    """

    tab = 'lesions'
    panel_template = 'patients/tabs/_lesiones.html'
    #: Nombre del parámetro con el que llega la vista marcada en el mapa.
    view_param = 'vista'
    #: Nombre del parámetro con el que llega el pie marcado en el mapa.
    laterality_param = 'pie'
    #: Dónde se sitúa una lesión de la que no se ha marcado punto: en el centro
    #: de la vista. `x`/`y` no admiten nulo (solo sirven para dibujar), así que
    #: «sin marcar» necesita un valor, y el centro es el único neutro.
    default_point = 0.5

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

    # -- punto marcado en el mapa -------------------------------------------

    def read_fraction(self, raw):
        """Una coordenada que llega del cliente: fracción 0–1, o `None`.

        Se usa solo para **prerrellenar** el formulario, así que un valor
        ausente, ilegible o fuera de rango no es un error: se cae al centro de la
        vista. Lo que sí se valida de verdad es el POST, en `LesionForm`.
        """
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if not (0.0 <= value <= 1.0):
            return None
        # Cuatro decimales sobre el `viewBox` es una décima de unidad: más
        # precisión que la que tiene el dedo de nadie, y números legibles.
        return round(value, 4)

    def build_point(self, form):
        """Dónde se pinta el marcador provisional mientras el alta está abierta.

        Sale del propio formulario (`form['x'].value()`), así que vale igual para
        el alta recién abierta y para el formulario que vuelve con errores: en
        los dos casos el punto que se ve es el que se va a guardar.
        """
        views = dict(Lesion.View.choices)
        lateralities = dict(Lesion.Laterality.choices)

        x = self.read_fraction(form['x'].value())
        y = self.read_fraction(form['y'].value())
        if x is None or y is None:
            x = y = self.default_point
        view = form['view'].value()
        laterality = form['laterality'].value()
        return {
            'x': x,
            'y': y,
            # Sin clic, el punto es el centro exacto de la vista, y eso es justo
            # lo que dice el panel. Que alguien acierte el centro al milímetro y
            # lea «sin marcar» es un caso imposible de distinguir y sin
            # consecuencias: el texto seguiría siendo verdad.
            'marked': not (x == self.default_point and y == self.default_point),
            'view': view,
            'laterality': laterality,
            'view_label': views.get(view, ''),
            'laterality_label': lateralities.get(laterality, ''),
        }

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
            'lesion_create_url': reverse(
                'patients:lesion-create', kwargs={'id': self.object.pk}
            ),
            'lesion_map_url': reverse(
                'patients:lesion-map', kwargs={'id': self.object.pk}
            ),
            'view_param': self.view_param,
            'laterality_param': self.laterality_param,
        })
        # El mapa se pinta igual con formulario y sin él; la plantilla decide.
        context.setdefault('lesion_form', None)
        context.setdefault('lesion_point', None)
        context.setdefault('lesion_created', None)
        # …y lo mismo con el detalle de una lesión: sin lesión abierta, al lado
        # del mapa va el resumen de la vista. Declarados aquí, y no solo en la
        # vista de detalle, para que la plantilla tenga siempre las mismas
        # variables mire quien la mire.
        context.setdefault('selected_lesion', None)
        context.setdefault('lesion_observations', None)
        context.setdefault('observation_form', None)
        context.setdefault('observation_created', None)
        context.setdefault('resolve_form', None)
        context.setdefault('lesion_resolved', None)
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
# Altas clínicas desde la ficha (anamnesis, lesiones)
# ---------------------------------------------------------------------------


class ProfessionalAuthorMixin:
    """Quién está registrando lo que se registra.

    Vive suelto —y no dentro de `EpisodeAuthoringMixin`— porque el seguimiento de
    una lesión necesita el autor pero no abre episodios: la lesión ya trae el
    suyo. Dos definiciones de «quién firma esto» acabarían divergiendo.
    """

    def get_professional(self):
        """El `Professional` del usuario, o `None` si no tiene perfil.

        Un administrativo puede registrar lo que ha dictado el profesional; los
        modelos admiten `created_by` vacío precisamente porque no todo canal de
        entrada tiene profesional detrás.
        """
        return getattr(self.request.user, 'professional_profile', None)


class EpisodeAuthoringMixin(ProfessionalAuthorMixin):
    """Quién registra y de qué episodio cuelga lo registrado.

    Lo comparten el alta de anamnesis y el alta de lesión, que resuelven el
    episodio igual: el que se ha elegido en el formulario (siempre uno abierto
    del paciente, validado por `EpisodeSelectionMixin`) o uno nuevo con el motivo
    de consulta indicado. `self.object` es el paciente.
    """

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

    def resolve_episode(self, form, professional):
        """El episodio elegido en el formulario o, si no hay, uno recién abierto."""
        return form.cleaned_data.get('episode') or self.create_episode(
            form.cleaned_data['episode_reason'], professional
        )


class PatientAnamnesisCreateView(
    EpisodeAuthoringMixin, PatientScopedMixin, AccessLogMixin, LoginRequiredMixin, DetailView
):
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
            episode = self.resolve_episode(form, professional)
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


class PatientLesionMapView(PatientLesionsTabView):
    """Solo el mapa y su resumen, sin la barra de pestañas.

    Existe por una sola razón: **cerrar el alta**. Al cancelar hay que volver a
    pintar la región del mapa (`#lesiones-region`) sin el formulario, y la
    pestaña completa devolvería la barra y el panel enteros, que no es lo que
    hay bajo ese objetivo.

    Sin htmx sigue siendo una página normal (hereda `template_name`), así que el
    «Cancelar» es un enlace de verdad y no un callejón sin salida.
    """

    partial_template_name = 'patients/_lesion_region.html'


class PatientLesionCreateView(EpisodeAuthoringMixin, PatientLesionMapView):
    """Alta de una lesión sobre el mapa del pie.

    **La captura del punto es del cliente; la validación, del servidor.** Alpine
    convierte el clic a fracciones 0–1 del `viewBox` y las manda por la query
    string; aquí solo sirven para prerrellenar. Lo que decide es el POST, que
    pasa por `LesionForm` (rango 0–1 como error de campo), por `Lesion.save()` y
    por el `CheckConstraint` de la tabla.

    **El permiso es el mismo que el del resto de la ficha**: el `get_queryset()`
    heredado filtra por la clínica del usuario, así que un paciente ajeno es un
    404 también aquí, que es una URL invocable directamente y no solo el destino
    de un `hx-post`.

    **Sin API y sin operaciones masivas.** La escritura queda en el `ChangeLog`
    por las señales de auditoría (`Lesion` está registrada en
    `ClinicalConfig.ready`), lo que exige crear la lesión objeto a objeto. La
    lectura de esta pantalla la registra `AccessLogMixin`.

    Responde en dos modos:

    - con htmx, devuelve el fragmento del mapa (`_lesion_region.html`) para que
      el marcador nuevo aparezca sin recargar;
    - sin htmx, la página completa de la ficha y, si el alta cuaja, una
      redirección a la pestaña con su mensaje.
    """

    def get_form(self, data=None):
        if data is not None:
            return LesionForm(data, patient=self.object)
        return LesionForm(patient=self.object, initial=self.get_initial())

    def get_initial(self):
        """Vista, pie y punto tal y como estaba el mapa al abrir el alta.

        La vista y el pie se validan contra los `choices`: lo que llegue por la
        query string y no sea uno de ellos se ignora y manda el valor con el que
        abre el mapa.
        """
        params = self.request.GET
        views = dict(Lesion.View.choices)
        lateralities = dict(Lesion.Laterality.choices)

        scenes = self.build_scenes(self.get_lesions())
        opening = self.default_scene(scenes)

        view = params.get(self.view_param)
        laterality = params.get(self.laterality_param)
        x = self.read_fraction(params.get('x'))
        y = self.read_fraction(params.get('y'))
        if x is None or y is None:
            # Se ha abierto el formulario sin marcar (por teclado, por ejemplo).
            x = y = self.default_point

        return {
            'view': view if view in views else opening['view'],
            'laterality': laterality if laterality in lateralities else opening['laterality'],
            'x': x,
            'y': y,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Registrada la lesión, el formulario se cierra: lo que se devuelve es el
        # mapa con el marcador nuevo, no otro formulario en blanco.
        if context.get('lesion_created') is None and context.get('lesion_form') is None:
            context['lesion_form'] = self.get_form()
        if context.get('lesion_form') is not None:
            point = self.build_point(context['lesion_form'])
            context['lesion_point'] = point
            # Sin htmx se pinta la página entera, y el mapa tiene que abrirse
            # donde está el punto: si no, el marcador provisional quedaría en una
            # vista que no se está mirando.
            context['default_view'] = point['view']
            context['default_laterality'] = point['laterality']
        return context

    def post(self, request, *args, **kwargs):
        # Resolver el objeto es lo que aplica el aislamiento: paciente de otra
        # clínica, 404, y no se ha tocado nada.
        self.object = self.get_object()
        form = self.get_form(data=request.POST)

        if not form.is_valid():
            return self.render_to_response(self.get_context_data(lesion_form=form))

        professional = self.get_professional()
        # El episodio y la lesión se crean juntos o no se crea ninguno: un fallo
        # a medias dejaría un episodio abierto sin la lesión que lo motivó.
        with transaction.atomic():
            episode = self.resolve_episode(form, professional)
            lesion = form.save(commit=False)
            lesion.episode = episode
            lesion.created_by = professional
            lesion.save()

        if self.is_htmx_swap():
            # Nada de `messages` aquí: no hay recarga que los vacíe y el aviso
            # saldría luego, fuera de sitio. El fragmento trae el suyo.
            return self.render_to_response(self.get_context_data(lesion_created=lesion))

        messages.success(request, 'Lesión registrada correctamente.')
        return redirect('patients:tab-lesions', id=self.object.pk)


class LesionAccessMixin:
    """La lesión de la URL, **buscada dentro del paciente ya resuelto**.

    El paciente lo acota `PatientScopedMixin` (clínica del usuario); esta es la
    segunda mitad del aislamiento y es igual de imprescindible: sin ella, el id
    de la lesión de otra persona de la misma clínica abriría su seguimiento sin
    más que escribirlo en la URL. Todas estas rutas son invocables directamente.
    """

    #: Nombre del argumento de URL con el id de la lesión.
    lesion_url_kwarg = 'lesion_id'

    def get_lesion(self):
        """La lesión pedida, del paciente de la URL. Ajena o inexistente, 404."""
        if getattr(self, '_lesion', None) is None:
            self._lesion = get_object_or_404(
                Lesion.objects.select_related('episode', 'created_by__user'),
                pk=self.kwargs[self.lesion_url_kwarg],
                episode__history__patient=self.object,
            )
        return self._lesion

    def get_observations(self, lesion, *, chronological=False):
        """El seguimiento de la lesión, con sus fotos precargadas.

        Por defecto, lo más reciente primero: es como se lee una ficha. En orden
        **cronológico** es como se lee una evolución, y se pide explícitamente
        —igual que hace `Lesion.evolution()`— porque leer una serie al revés se
        presta a concluir «va a peor» cuando iba a mejor.

        Las fotos se precargan aquí y no se piden foto a foto en la plantilla:
        una observación con seis adjuntos serían seis consultas por fila.
        """
        queryset = (
            LesionObservation.objects
            .for_lesion(lesion)
            .select_related('visit', 'created_by__user')
            .prefetch_related('attachments')
        )
        if chronological:
            return list(queryset.chronological())
        return list(queryset.order_by('-observed_at', '-id'))


class PatientLesionDetailView(ProfessionalAuthorMixin, LesionAccessMixin, PatientLesionMapView):
    """Detalle de una lesión del mapa: sus datos y su serie de observaciones.

    **El detalle se pide, no se precarga.** El mapa pinta la posición y el estado
    de todas las lesiones del paciente —eso es lo que hace falta para dibujarlo—,
    pero las observaciones son texto clínico y medidas, y solo viajan las de la
    lesión que se abre. Meter el seguimiento de todas en la página sería mandar
    al navegador un historial entero para enseñar uno.

    Se sirve en la MISMA región intercambiable que el mapa (`#lesiones-region`),
    por lo mismo que el alta: al cerrar una lesión cambian a la vez el panel y el
    color de su marcador, y separarlos en dos objetivos de htmx abriría la puerta
    a que el mapa dijera una cosa y el panel otra.

    **El permiso se comprueba aquí, no en quien enlaza.** El paciente se resuelve
    con el `get_queryset()` heredado (clínica del usuario → 404 si es ajeno) y la
    lesión se busca *dentro de ese paciente*: el id de la lesión de otra persona
    no abre nada aunque se escriba a mano en la URL, que es lo que hay que
    defender en un parcial invocable directamente.
    """

    def get_observation_form(self, data=None, files=None):
        return LesionObservationForm(
            data, files, lesion=self.get_lesion(), professional=self.get_professional(),
        )

    def get_resolve_form(self, data=None):
        return LesionResolveForm(data, lesion=self.get_lesion())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lesion = self.get_lesion()
        patient_id = self.object.pk

        context.update({
            'selected_lesion': lesion,
            'lesion_observations': self.get_observations(lesion),
            # Una observación necesita una visita del mismo episodio: o hay
            # alguna, o el episodio sigue abierto y se puede registrar.
            'can_add_observation': (
                lesion.episode.status == Episode.Status.OPEN
                or Visit.objects.filter(episode=lesion.episode).exists()
            ),
            'lesion_detail_url': reverse(
                'patients:lesion-detail',
                kwargs={'id': patient_id, 'lesion_id': lesion.pk},
            ),
            'observation_create_url': reverse(
                'patients:observation-create',
                kwargs={'id': patient_id, 'lesion_id': lesion.pk},
            ),
            'lesion_resolve_url': reverse(
                'patients:lesion-resolve',
                kwargs={'id': patient_id, 'lesion_id': lesion.pk},
            ),
            'lesion_evolution_url': reverse(
                'patients:lesion-evolution',
                kwargs={'id': patient_id, 'lesion_id': lesion.pk},
            ),
            # Sin htmx se pinta la página entera: el mapa tiene que abrirse por
            # donde está la lesión que se está leyendo.
            'default_view': lesion.view,
            'default_laterality': lesion.laterality,
        })
        return context


class PatientLesionObservationCreateView(PatientLesionDetailView):
    """Anotar cómo está la lesión en una visita.

    Es la pieza que convierte una lesión en una **serie**: la lesión no se mueve
    ni cambia de identidad, lo que cambia es lo que se ve de ella cada día.

    Con `GET` abre el formulario dentro del panel; con `POST` registra la
    observación y devuelve el panel con la serie ya al día. Sin htmx las dos
    cosas siguen siendo páginas normales, así que el seguimiento también se puede
    registrar sin JavaScript.

    **La visita se crea aquí si hace falta**, dentro de la misma transacción que
    la observación: observar una lesión es un encuentro clínico, y dejar la
    visita creada sin la observación que la motivó sería peor que no crear nada.

    **Las fotos entran por aquí y van al bucket privado.** Se crean una a una
    (`LesionAttachment.objects.create`) y nunca con `bulk_create`: los adjuntos
    están registrados en la auditoría, y un bulk se salta las señales. Cada uno
    pasa por la validación por contenido de su propio `save()`, así que lo que se
    guarda es lo que el fichero ES, no lo que dijera su nombre.
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Registrada la observación, el formulario se cierra: lo que se devuelve
        # es la serie con la anotación nueva, no otro formulario en blanco.
        if context.get('observation_created') is None and context.get('observation_form') is None:
            context['observation_form'] = self.get_observation_form()
        return context

    def post(self, request, *args, **kwargs):
        # Resolver paciente y lesión es lo que aplica el aislamiento: de otra
        # clínica o de otra persona, 404, y no se ha tocado nada.
        self.object = self.get_object()
        lesion = self.get_lesion()
        form = self.get_observation_form(data=request.POST, files=request.FILES)

        if not form.is_valid():
            return self.render_to_response(self.get_context_data(observation_form=form))

        professional = self.get_professional()
        with transaction.atomic():
            visit = form.cleaned_data.get('visit') or Visit.objects.create(
                episode=lesion.episode,
                professional=form.new_visit_professional(),
                occurred_at=form.visit_moment(),
            )
            observation = form.save(commit=False)
            observation.lesion = lesion
            observation.visit = visit
            observation.created_by = professional
            observation.save()

            # Dentro de la transacción: una foto que no llega al bucket no puede
            # dejar una observación diciendo que existe. (Al revés no hay vuelta
            # atrás: si la transacción cae después de subir, el objeto se queda
            # huérfano en el bucket. Es el precio de no borrar nada físicamente,
            # y es el lado seguro del fallo.)
            for photo in form.cleaned_data.get('photos') or []:
                LesionAttachment.objects.create(
                    observation=observation,
                    file=photo,
                    source=LesionAttachment.Source.PROFESSIONAL,
                )

        if self.is_htmx_swap():
            # Nada de `messages` aquí: no hay recarga que los vacíe y el aviso
            # saldría luego, en otra pantalla.
            return self.render_to_response(
                self.get_context_data(observation_created=observation)
            )

        messages.success(request, 'Observación registrada correctamente.')
        return redirect(
            'patients:lesion-detail', id=self.object.pk, lesion_id=lesion.pk
        )


class PatientLesionResolveView(PatientLesionDetailView):
    """Dar de alta una lesión, con su fecha de resolución.

    El cierre va por `Lesion.resolve(on=…)` y nunca por un `update`: es lo que
    mantiene coherentes el estado y la fecha —el `CheckConstraint` exige que una
    lesión resuelta traiga la suya— y lo que deja el cambio en el `ChangeLog`,
    que un `queryset.update()` se saltaría.

    Una lesión ya resuelta no se vuelve a cerrar: se responde con el panel tal y
    como está. Reabrirla (`Lesion.reopen()`) es otra decisión y no se toma aquí.
    """

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lesion = self.get_lesion()
        if (
            context.get('lesion_resolved') is None
            and context.get('resolve_form') is None
            and lesion.status == Lesion.Status.ACTIVE
        ):
            context['resolve_form'] = self.get_resolve_form()
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        lesion = self.get_lesion()

        if lesion.status == Lesion.Status.RESOLVED:
            return self.render_to_response(self.get_context_data())

        form = self.get_resolve_form(data=request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(resolve_form=form))

        lesion.resolve(on=form.cleaned_data['resolved_at'])

        if self.is_htmx_swap():
            return self.render_to_response(self.get_context_data(lesion_resolved=lesion))

        messages.success(request, 'Lesión marcada como resuelta.')
        return redirect(
            'patients:lesion-detail', id=self.object.pk, lesion_id=lesion.pk
        )


#: Las tres medidas de una observación, en el orden en que se leen. Vive aquí y
#: no en la plantilla para que el resumen de la evolución y el detalle no puedan
#: acabar contando dimensiones distintas.
LESION_MEASUREMENTS = (
    ('length_mm', 'Largo'),
    ('width_mm', 'Ancho'),
    ('depth_mm', 'Profundidad'),
)


class PatientLesionEvolutionView(
    LesionAccessMixin, PatientScopedMixin, AccessLogMixin, LoginRequiredMixin, DetailView
):
    """La evolución de una lesión: su serie completa, en orden y con sus fotos.

    Es una **página propia**, no un panel: comparar dos fotos de una úlcera
    necesita el ancho entero, y el mapa no aporta nada aquí (la lesión ya está
    elegida). Por eso tampoco es un fragmento de htmx — se llega con un enlace
    normal y se puede compartir, marcar o recargar.

    **El orden es cronológico y explícito.** El `ordering` del modelo es el
    contrario porque una ficha se lee de lo más reciente hacia atrás; una
    evolución, no. Leer una serie al revés se presta a concluir «va a peor»
    cuando iba a mejor, así que aquí se pide en el orden de la serie.

    **Ninguna URL de imagen se escribe en esta página.** Cada miniatura apunta a
    `clinical:lesion-attachment`, la vista que comprueba el permiso, firma contra
    el bucket en ese instante y redirige — ver la nota sobre caducidad en
    `templates/patients/lesion_evolution.html`.
    """

    model = Patient
    pk_url_kwarg = 'id'
    context_object_name = 'patient'
    template_name = 'patients/lesion_evolution.html'

    def measurement_trend(self, observations):
        """Primera y última medida de cada dimensión, y su variación.

        Solo entra la dimensión medida **al menos dos veces**: con un único dato
        no hay evolución que resumir, y enseñar «14 mm» sin nada con lo que
        compararlo invita a leerlo como una tendencia. La primera y la última se
        buscan entre las observaciones que TIENEN esa medida, no entre todas: no
        toda observación mide lo mismo, y saltarse ese detalle compararía una
        medida con un hueco.
        """
        trend = []
        for field, label in LESION_MEASUREMENTS:
            medidas = [
                observation for observation in observations
                if getattr(observation, field) is not None
            ]
            if len(medidas) < 2:
                continue
            first, last = medidas[0], medidas[-1]
            trend.append({
                'label': label,
                'first': getattr(first, field),
                'first_at': first.observed_at,
                'last': getattr(last, field),
                'last_at': last.observed_at,
                'delta': getattr(last, field) - getattr(first, field),
            })
        return trend

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lesion = self.get_lesion()
        observations = self.get_observations(lesion, chronological=True)

        context.update({
            'section': 'patients',
            'lesion': lesion,
            'observations': observations,
            'measurement_trend': self.measurement_trend(observations),
            'photo_count': sum(len(o.attachments.all()) for o in observations),
            'compare_url': reverse(
                'patients:lesion-compare',
                kwargs={'id': self.object.pk, 'lesion_id': lesion.pk},
            ),
            'back_url': reverse(
                'patients:lesion-detail',
                kwargs={'id': self.object.pk, 'lesion_id': lesion.pk},
            ),
        })
        return context


class PatientLesionCompareView(
    LesionAccessMixin, PatientScopedMixin, AccessLogMixin, LoginRequiredMixin, DetailView
):
    """Dos observaciones de la misma lesión, lado a lado.

    Es el fragmento que pide la evolución cuando se eligen dos puntos de la
    serie. **Los datos los sirve el servidor**, no el navegador: Alpine solo
    recuerda cuáles están seleccionadas, y las fotos y las medidas se piden aquí.
    Meterlas en un `x-data` obligaría a volcar el historial clínico entero en un
    atributo HTML para enseñar dos columnas.

    Las dos observaciones se buscan **dentro de la lesión**, que a su vez está
    dentro del paciente: el id de la observación de otra persona no compara nada.
    Si falta alguna, el fragmento lo dice en vez de reventar — es lo que se ve
    mientras se elige la segunda.
    """

    model = Patient
    pk_url_kwarg = 'id'
    context_object_name = 'patient'
    template_name = 'patients/_lesion_compare.html'
    #: Nombres de los dos parámetros con los que llegan las observaciones.
    compare_params = ('a', 'b')

    def get_selection(self, lesion):
        """Las dos observaciones pedidas, en el orden en que se pintan.

        Se resuelven en una sola consulta y se ordenan **por fecha**, no por el
        orden en que se marcaron: una comparación en la que la de la izquierda
        sea la posterior se lee al revés sin que nada lo advierta.
        """
        ids = []
        for param in self.compare_params:
            raw = self.request.GET.get(param)
            if raw and raw.isdigit():
                ids.append(int(raw))

        if len(set(ids)) < 2:
            return []
        found = {
            observation.pk: observation
            for observation in LesionObservation.objects
            .for_lesion(lesion)
            .filter(pk__in=ids)
            .select_related('visit')
            .prefetch_related('attachments')
        }
        selection = [found[pk] for pk in dict.fromkeys(ids) if pk in found]
        if len(selection) < 2:
            return []
        return sorted(selection, key=lambda observation: (observation.observed_at, observation.pk))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        lesion = self.get_lesion()
        selection = self.get_selection(lesion)

        context['lesion'] = lesion
        context['comparison'] = selection
        if len(selection) == 2:
            first, last = selection
            context['comparison_days'] = (last.observed_at - first.observed_at).days
            context['comparison_trend'] = [
                {
                    'label': label,
                    'first': getattr(first, field),
                    'last': getattr(last, field),
                    'delta': getattr(last, field) - getattr(first, field),
                }
                for field, label in LESION_MEASUREMENTS
                if getattr(first, field) is not None and getattr(last, field) is not None
            ]
        return context


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
    
