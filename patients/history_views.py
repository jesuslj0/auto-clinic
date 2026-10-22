"""Curso clínico y firma de consentimientos, desde la ficha del paciente.

Es la mitad de escritura de la capa clínica en el panel: abrir y cerrar episodios,
registrar visitas, escribir la nota SOAP, firmarla, añadirle adendas y recoger la
firma de un consentimiento. Todo son vistas de sesión con CSRF. **Esta capa no
tiene API REST a propósito**, para que el `Api-Key` del agente de n8n no llegue
hasta aquí; ninguna de estas rutas la abre.

Tres reglas que atraviesan el fichero:

- **Aislamiento.** El paciente se resuelve con `PatientScopedMixin` (clínica del
  usuario, 404 si es ajeno) y cada episodio, visita o nota se busca *dentro de ese
  paciente*: el id de una nota de otra persona no abre nada aunque se teclee a mano.
- **Lecturas, siempre con `AccessLog`.** Toda vista que responde a un GET lleva
  `AccessLogMixin`. Las que solo reciben POST no leen nada. La excepción es el POST
  que *re-pinta* una nota con errores (la adenda): enseña texto clínico, así que
  registra su lectura a mano.
- **Una nota firmada no se toca.** Ninguna vista de edición, borrado o firma acepta
  una nota firmada: se comprueba aquí para dar un mensaje, y el modelo y el trigger
  de PostgreSQL la rechazarían de todos modos (`clinical/migrations/0002`).

La autoría de un borrador no se guarda en la nota (solo `signed_by`): queda en el
`ChangeLog`, que registra quién creó y quién modificó cada versión sin guardar el
texto.
"""
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Prefetch
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView

from audit.mixins import AccessLogMixin
from clinical.exceptions import ConsentVersionNotPublished, EpisodeClosed, NoteAlreadySigned
from clinical.forms import (
    AddendumForm,
    ClinicalNoteForm,
    ConsentSignForm,
    EpisodeForm,
    VisitForm,
)
from clinical.hashing import verify_note_hash
from clinical.models import (
    Addendum,
    ClinicalNote,
    ConsentTemplate,
    Episode,
    Lesion,
    PerformedProcedure,
    QuestionnaireResponse,
    SignedConsent,
    Visit,
)
from clinical.procedures import history_for
from clinical.soap import is_blank, preview_for, sections_for
from patients.models import Patient
from patients.views import (
    EpisodeAuthoringMixin,
    PatientScopedMixin,
    PatientTabView,
    ProfessionalAuthorMixin,
)


def history_url(patient_id, anchor=''):
    """La pestaña «Historia», opcionalmente anclada a un episodio o una visita."""
    url = reverse('patients:tab-history', kwargs={'id': patient_id})
    return f'{url}#{anchor}' if anchor else url


def visit_landing_url(visit, patient_id):
    """Dónde se escribe la nota de una visita: la que ya hay, o una nueva.

    Un borrador pendiente se retoma antes que abrir otro; si solo hay notas
    firmadas, se enseña la última; sin notas, se abre una nueva. Lo usa también la
    cita (`appointments:visit-open`), por eso vive aquí y no en cada vista: la
    regla de «a qué pantalla se llega desde una visita» tiene que ser una.
    """
    notes = list(ClinicalNote.objects.filter(visit=visit).order_by('-created_at', '-id'))
    draft = next((note for note in notes if note.status == ClinicalNote.Status.DRAFT), None)
    if draft is not None:
        return reverse('patients:note-edit', kwargs={'id': patient_id, 'note_id': draft.pk})
    if notes:
        return reverse('patients:note-detail', kwargs={'id': patient_id, 'note_id': notes[0].pk})
    return reverse('patients:note-create', kwargs={'id': patient_id, 'visit_id': visit.pk})


def has_signed_notes(visit):
    """¿La visita tiene alguna nota firmada? Entonces es parte de un documento inmutable."""
    return ClinicalNote.objects.filter(visit=visit, status=ClinicalNote.Status.SIGNED).exists()


def offers_background(patient, note=None):
    """¿Se ofrece el bloque de Antecedentes en esta nota?

    Solo en la **primera nota del paciente**: calzado, actividad física y
    patologías no cambian de una revisión a otra y no se repiten. (Si la nota ya los
    trae se siguen mostrando: lo decide el formulario.) Se cuenta por paciente y no
    por episodio, porque un problema nuevo no es un paciente nuevo.
    """
    others = ClinicalNote.objects.filter(visit__episode__history__patient=patient)
    if note is not None:
        others = others.exclude(pk=note.pk)
    return not others.exists()


# ---------------------------------------------------------------------------
# Pestaña «Historia»
# ---------------------------------------------------------------------------


class PatientHistoryTabView(PatientTabView):
    """Pestaña «Historia»: episodios, con sus visitas, notas y adendas.

    Es el índice del curso clínico y el sitio desde el que se llega a todo lo
    demás: cada episodio enseña lo que cuelga de él (visitas, consentimientos,
    lesiones, anamnesis) con enlaces a su pestaña, y cada visita, sus notas y sus
    procedimientos.

    **Del texto de la nota solo se enseña una línea** (el análisis, o el subjetivo
    si falta): la nota entera se lee en su propia pantalla, que registra su propio
    `AccessLog`. La pestaña ya está registrada por `AccessLogMixin`.

    Se arma con cuatro consultas más la de episodios, agrupando en Python: las
    visitas con sus notas, y un recuento por episodio o visita de lo demás. Un
    episodio con veinte visitas no son veinte consultas.
    """

    tab = 'history'
    panel_template = 'patients/tabs/_historia.html'

    def build_episodes(self):
        patient = self.object
        episodes = list(
            Episode.objects
            .filter(history__patient=patient)
            .select_related('responsible_professional__user')
            .order_by('-opened_at', '-id')
        )
        if not episodes:
            return []

        notes = (
            ClinicalNote.objects
            .select_related('signed_by__user')
            .annotate(addenda_count=Count('addenda'))
            .order_by('created_at', 'id')
        )
        visits = list(
            Visit.objects
            .filter(episode__in=episodes)
            .select_related('professional__user', 'appointment')
            .prefetch_related(Prefetch('notes', queryset=notes, to_attr='note_list'))
            .order_by('-occurred_at', '-id')
        )

        procedure_counts = dict(
            PerformedProcedure.objects
            .filter(visit__in=visits)
            .order_by()
            .values_list('visit')
            .annotate(total=Count('pk'))
        )
        for visit in visits:
            visit.procedure_count = procedure_counts.get(visit.pk, 0)
            for note in visit.note_list:
                note.preview = preview_for(note)
            visit.has_signed = any(
                note.status == ClinicalNote.Status.SIGNED for note in visit.note_list
            )

        def per_episode(queryset):
            return dict(queryset.order_by().values_list('episode').annotate(total=Count('pk')))

        lesion_counts = per_episode(Lesion.objects.filter(episode__in=episodes))
        anamnesis_counts = per_episode(
            QuestionnaireResponse.objects.filter(episode__in=episodes)
        )
        consents = {}
        for consent in (
            SignedConsent.objects
            .filter(patient=patient)
            .select_related('version__template')
            .order_by('-signed_at', '-id')
        ):
            consents.setdefault(consent.episode_id, []).append(consent)

        by_episode = {}
        for visit in visits:
            by_episode.setdefault(visit.episode_id, []).append(visit)
        for episode in episodes:
            episode.visit_list = by_episode.get(episode.pk, [])
            episode.consent_list = consents.get(episode.pk, [])
            episode.lesion_count = lesion_counts.get(episode.pk, 0)
            episode.anamnesis_count = anamnesis_counts.get(episode.pk, 0)
            episode.draft_count = sum(
                1
                for visit in episode.visit_list
                for note in visit.note_list
                if note.status == ClinicalNote.Status.DRAFT
            )
        return episodes

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        professional = getattr(self.request.user, 'professional_profile', None)
        context['episodes'] = self.build_episodes()
        context['can_sign'] = professional is not None
        return context


# ---------------------------------------------------------------------------
# Piezas comunes
# ---------------------------------------------------------------------------


class HistoryAccessMixin(ProfessionalAuthorMixin, PatientScopedMixin):
    """El paciente de la URL y, dentro de él, el episodio, la visita o la nota.

    Cada localizador busca **dentro del paciente ya resuelto**: es la segunda mitad
    del aislamiento (la primera es la clínica del usuario) y es igual de
    imprescindible. Sin ella, el id de una nota de otra persona de la misma
    clínica abriría su contenido sin más que escribirlo en la URL.
    """

    def load_patient(self):
        self.object = get_object_or_404(self.get_queryset(), pk=self.kwargs['id'])
        return self.object

    # Los tres se piden varias veces por petición (contexto, formulario, registro
    # del acceso): se resuelven una sola vez y se recuerdan en la vista, que vive
    # lo que dura la petición.

    def get_episode(self):
        if not hasattr(self, '_episode'):
            self._episode = get_object_or_404(
                Episode.objects.select_related('responsible_professional__user'),
                pk=self.kwargs['episode_id'],
                history__patient=self.object,
            )
        return self._episode

    def get_visit(self):
        if not hasattr(self, '_visit'):
            self._visit = get_object_or_404(
                Visit.objects.select_related('episode', 'professional__user', 'appointment'),
                pk=self.kwargs['visit_id'],
                episode__history__patient=self.object,
            )
        return self._visit

    def get_note(self):
        if not hasattr(self, '_note'):
            self._note = get_object_or_404(
                ClinicalNote.objects.select_related(
                    'visit__episode', 'visit__professional__user', 'signed_by__user',
                ),
                pk=self.kwargs['note_id'],
                visit__episode__history__patient=self.object,
            )
        return self._note


class HistoryPageView(HistoryAccessMixin, AccessLogMixin, LoginRequiredMixin, DetailView):
    """Base de las pantallas de la historia que responden a un GET.

    `AccessLogMixin` va delante: registra la lectura del paciente (o del objeto que
    la subclase señale en `get_access_object`). Las subclases no sobrescriben
    `get()`, que eclipsaría el del mixin y dejaría la lectura sin registrar: todo
    el trabajo cabe en `get_context_data`.
    """

    model = Patient
    pk_url_kwarg = 'id'
    context_object_name = 'patient'

    def get_access_patient(self):
        # El resolvedor por defecto no sabe llegar de un episodio o una nota al
        # paciente (no tienen `patient`); aquí ya está resuelto y es el de la URL.
        return self.object

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['section'] = 'patients'
        return context


class HistoryActionView(HistoryAccessMixin, LoginRequiredMixin, View):
    """Base de las acciones que solo reciben POST (cerrar, firmar, borrar…).

    Sin GET no hay lectura que registrar. Un GET a estas rutas es un 405.
    """

    http_method_names = ['post', 'options']


class HistoryFormView(HistoryPageView):
    """Página con un formulario genérico (`patients/history_form.html`).

    Las subclases dicen cómo construir el formulario y qué hacer cuando es válido;
    el flujo —resolver el paciente, validar, volver a pintar con errores— está una
    sola vez.
    """

    template_name = 'patients/history_form.html'
    page_title = ''
    submit_label = 'Guardar'
    #: Texto del botón de volver (arriba a la derecha).
    back_label = 'Volver a la historia'

    def get_form(self, data=None):
        raise NotImplementedError

    def form_valid(self, form):
        raise NotImplementedError

    def get_cancel_url(self):
        return history_url(self.object.pk)

    def get_page_subtitle(self):
        return str(self.object)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if context.get('form') is None:
            context['form'] = self.get_form()
        context.update({
            'page_title': self.page_title,
            'page_subtitle': self.get_page_subtitle(),
            'submit_label': self.submit_label,
            'cancel_url': self.get_cancel_url(),
            'back_label': self.back_label,
        })
        return context

    def post(self, request, *args, **kwargs):
        self.load_patient()
        form = self.get_form(data=request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        return self.form_valid(form)


# ---------------------------------------------------------------------------
# Episodios
# ---------------------------------------------------------------------------


class PatientEpisodeCreateView(HistoryFormView):
    """Abrir un episodio: el proceso asistencial del que colgará todo lo demás."""

    page_title = 'Nuevo episodio'
    submit_label = 'Abrir episodio'

    def get_form(self, data=None):
        initial = {}
        professional = self.get_professional()
        if professional is not None:
            initial['responsible_professional'] = professional.pk
        return EpisodeForm(data, clinic_id=self.object.clinic_id, initial=initial)

    def form_valid(self, form):
        episode = form.save(commit=False)
        episode.history = history_for(self.object)
        episode.save()
        messages.success(self.request, 'Episodio abierto.')
        return redirect(history_url(self.object.pk, f'episodio-{episode.pk}'))


class PatientEpisodeEditView(HistoryFormView):
    """Corregir el motivo o el responsable de un episodio.

    Los cambios quedan en el `ChangeLog` (el motivo, enmascarado: se registra que
    cambió, no su valor).
    """

    page_title = 'Editar episodio'

    def get_access_object(self):
        return self.get_episode()

    def get_form(self, data=None):
        return EpisodeForm(
            data, instance=self.get_episode(), clinic_id=self.object.clinic_id,
        )

    def form_valid(self, form):
        episode = form.save()
        messages.success(self.request, 'Episodio actualizado.')
        return redirect(history_url(self.object.pk, f'episodio-{episode.pk}'))


class PatientEpisodeCloseView(HistoryActionView):
    """Dar de alta el episodio. Un episodio cerrado no admite visitas nuevas."""

    def post(self, request, id, episode_id):
        self.load_patient()
        episode = self.get_episode()
        if episode.status == Episode.Status.OPEN:
            episode.close()
            messages.success(request, 'Episodio cerrado.')
        return redirect(history_url(self.object.pk, f'episodio-{episode.pk}'))


class PatientEpisodeReopenView(HistoryActionView):
    """Reabrir un episodio cerrado. La reapertura queda en el `ChangeLog`."""

    def post(self, request, id, episode_id):
        self.load_patient()
        episode = self.get_episode()
        if episode.status == Episode.Status.CLOSED:
            episode.reopen()
            messages.success(request, 'Episodio reabierto.')
        return redirect(history_url(self.object.pk, f'episodio-{episode.pk}'))


# ---------------------------------------------------------------------------
# Visitas
# ---------------------------------------------------------------------------


class PatientVisitCreateView(HistoryFormView):
    """Registrar una visita en un episodio abierto.

    Dos botones: guardar la visita a secas (para colgarle procedimientos) o
    guardarla y pasar a escribir su nota, que es lo habitual.
    """

    page_title = 'Nueva visita'
    submit_label = 'Guardar visita'

    def get_page_subtitle(self):
        return f'{self.object} · {self.get_episode().reason}'

    def get_cancel_url(self):
        return history_url(self.object.pk, f'episodio-{self.kwargs["episode_id"]}')

    def get_form(self, data=None):
        return VisitForm(
            data, clinic_id=self.object.clinic_id,
            default_professional=self.get_professional(),
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['episode'] = self.get_episode()
        context['offer_note_button'] = True
        return context

    def get(self, request, *args, **kwargs):
        # `AccessLogMixin.get` registra la lectura; antes se rechaza lo que no
        # tiene sentido enseñar (un episodio cerrado no admite visitas).
        self.object = self.get_object()
        episode = self.get_episode()
        if episode.status == Episode.Status.CLOSED:
            messages.error(request, 'El episodio está cerrado: reábrelo para añadir una visita.')
            return redirect(history_url(self.object.pk, f'episodio-{episode.pk}'))
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        episode = self.get_episode()
        visit = form.save(commit=False)
        visit.episode = episode
        try:
            visit.save()
        except EpisodeClosed:
            messages.error(self.request, 'El episodio está cerrado: reábrelo para añadir una visita.')
            return redirect(history_url(self.object.pk, f'episodio-{episode.pk}'))
        messages.success(self.request, 'Visita registrada.')
        if self.request.POST.get('then') == 'note':
            return redirect(visit_landing_url(visit, self.object.pk))
        return redirect(history_url(self.object.pk, f'visita-{visit.pk}'))


class PatientVisitEditView(HistoryFormView):
    """Corregir quién atendió y cuándo. No se permite si ya hay una nota firmada.

    La fecha y el profesional de una visita con nota firmada forman parte de lo que
    se firmó: cambiarlos reescribiría el contexto de un documento inmutable.
    """

    page_title = 'Editar visita'

    def get_access_object(self):
        return self.get_visit()

    def get_form(self, data=None):
        return VisitForm(
            data, instance=self.get_visit(), clinic_id=self.object.clinic_id,
        )

    def get_cancel_url(self):
        return history_url(self.object.pk, f'visita-{self.kwargs["visit_id"]}')

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        visit = self.get_visit()
        if has_signed_notes(visit):
            messages.error(request, 'La visita tiene una nota firmada y ya no se puede modificar.')
            return redirect(history_url(self.object.pk, f'visita-{visit.pk}'))
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        visit = self.get_visit()
        if has_signed_notes(visit):
            messages.error(self.request, 'La visita tiene una nota firmada y ya no se puede modificar.')
            return redirect(history_url(self.object.pk, f'visita-{visit.pk}'))
        form.save()
        messages.success(self.request, 'Visita actualizada.')
        return redirect(history_url(self.object.pk, f'visita-{visit.pk}'))


# ---------------------------------------------------------------------------
# Notas clínicas
# ---------------------------------------------------------------------------


class PatientNoteCreateView(HistoryPageView):
    """Escribir la nota SOAP de una visita, en borrador."""

    template_name = 'patients/note_form.html'

    def get_form(self, data=None):
        return ClinicalNoteForm(data, show_background=offers_background(self.object))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        visit = self.get_visit()
        context.update({
            'visit': visit,
            'episode': visit.episode,
            'note': None,
            'form': kwargs.get('form') or self.get_form(),
            'cancel_url': history_url(self.object.pk, f'visita-{visit.pk}'),
        })
        return context

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        visit = self.get_visit()
        # Con un borrador pendiente se retoma ese, en vez de abrir otro paralelo.
        draft = ClinicalNote.objects.filter(
            visit=visit, status=ClinicalNote.Status.DRAFT
        ).order_by('-created_at', '-id').first()
        if draft is not None:
            return redirect('patients:note-edit', id=self.object.pk, note_id=draft.pk)
        if visit.episode.status == Episode.Status.CLOSED:
            messages.error(request, 'El episodio está cerrado: reábrelo para escribir una nota.')
            return redirect(history_url(self.object.pk, f'visita-{visit.pk}'))
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.load_patient()
        visit = self.get_visit()
        if visit.episode.status == Episode.Status.CLOSED:
            messages.error(request, 'El episodio está cerrado: reábrelo para escribir una nota.')
            return redirect(history_url(self.object.pk, f'visita-{visit.pk}'))
        form = self.get_form(data=request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        note = form.save(commit=False)
        note.visit = visit
        note.save()
        messages.success(request, 'Borrador guardado. Revísalo y fírmalo cuando esté listo.')
        return redirect('patients:note-detail', id=self.object.pk, note_id=note.pk)


class NoteScopedMixin:
    """La nota de la URL como objeto del acceso: así el `AccessLog` apunta a ella."""

    def get_access_object(self):
        return self.get_note()


class PatientNoteDetailView(NoteScopedMixin, HistoryPageView):
    """La nota entera: sus cuatro apartados, quién la firmó, sus adendas.

    Aquí se escribe la acción siguiente según el estado: un borrador se edita,
    se firma o se borra; una nota firmada solo admite adendas. **El permiso de
    firmar no se deduce de esconder el botón**: cada acción se vuelve a comprobar
    en su propia vista.
    """

    template_name = 'patients/note_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        note = self.get_note()
        professional = self.get_professional()
        is_draft = note.status == ClinicalNote.Status.DRAFT
        signed = note.status == ClinicalNote.Status.SIGNED
        can_addend = signed and professional is not None
        context.update({
            'note': note,
            'visit': note.visit,
            'episode': note.visit.episode,
            'sections': sections_for(note),
            'addenda': list(Addendum.objects.filter(note=note).select_related('author__user')),
            'is_draft': is_draft,
            'can_edit': is_draft,
            'can_sign': is_draft and professional is not None,
            'cannot_sign_reason': (
                '' if professional is not None
                else 'Solo un profesional con perfil en la clínica puede firmar una nota.'
            ),
            'can_addend': can_addend,
            'addendum_form': (
                (kwargs.get('addendum_form') or AddendumForm()) if can_addend else None
            ),
            # Cuadra el hash guardado con el contenido? Una nota firmada cuyo hash
            # no cuadra se manipuló por debajo del ORM y del trigger.
            'integrity_ok': verify_note_hash(note) if signed else None,
            'history_url': history_url(self.object.pk, f'visita-{note.visit_id}'),
        })
        return context


class PatientNoteEditView(NoteScopedMixin, HistoryPageView):
    """Editar un borrador. Una nota firmada no llega a ver el formulario."""

    template_name = 'patients/note_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        note = self.get_note()
        context.update({
            'note': note,
            'visit': note.visit,
            'episode': note.visit.episode,
            'form': kwargs.get('form') or self.get_note_form(note),
            'cancel_url': reverse(
                'patients:note-detail', kwargs={'id': self.object.pk, 'note_id': note.pk},
            ),
        })
        return context

    def get_note_form(self, note, data=None):
        return ClinicalNoteForm(
            data, instance=note, show_background=offers_background(self.object, note),
        )

    def refuse_signed(self, note):
        messages.error(
            self.request,
            'La nota está firmada y no se puede modificar. Añade una adenda.',
        )
        return redirect('patients:note-detail', id=self.object.pk, note_id=note.pk)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        note = self.get_note()
        if note.status == ClinicalNote.Status.SIGNED:
            return self.refuse_signed(note)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.load_patient()
        note = self.get_note()
        if note.status == ClinicalNote.Status.SIGNED:
            return self.refuse_signed(note)
        form = self.get_note_form(note, data=request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        try:
            form.save()
        except NoteAlreadySigned:
            # Se firmó en otra pestaña mientras se editaba.
            return self.refuse_signed(note)
        messages.success(request, 'Borrador guardado.')
        return redirect('patients:note-detail', id=self.object.pk, note_id=note.pk)


class PatientNoteSignView(HistoryActionView):
    """Firmar la nota: la vuelve inmutable para siempre.

    Solo firma un usuario con perfil de profesional, y lo hace **a su nombre**: el
    firmante nunca viene del formulario. `ClinicalNote.sign()` fija firmante, fecha
    y hash.
    """

    def post(self, request, id, note_id):
        self.load_patient()
        note = self.get_note()
        back = redirect('patients:note-detail', id=self.object.pk, note_id=note.pk)

        professional = self.get_professional()
        if professional is None:
            messages.error(request, 'Solo un profesional con perfil en la clínica puede firmar una nota.')
            return back
        if is_blank(note):
            messages.error(request, 'La nota está vacía: escríbela antes de firmarla.')
            return back
        try:
            note.sign(professional)
        except NoteAlreadySigned:
            messages.info(request, 'La nota ya estaba firmada.')
            return back
        messages.success(request, 'Nota firmada. Ya no se puede modificar; solo admite adendas.')
        return back


class PatientNoteDeleteView(HistoryActionView):
    """Descartar un borrador (borrado lógico). Una nota firmada no se borra jamás."""

    def post(self, request, id, note_id):
        self.load_patient()
        note = self.get_note()
        if not note.can_be_deleted():
            messages.error(request, 'Una nota firmada no se puede borrar.')
            return redirect('patients:note-detail', id=self.object.pk, note_id=note.pk)
        visit_id = note.visit_id
        note.delete()
        messages.success(request, 'Borrador descartado.')
        return redirect(history_url(self.object.pk, f'visita-{visit_id}'))


class PatientAddendumCreateView(PatientNoteDetailView):
    """Añadir una adenda a una nota firmada. Solo inserción.

    Hereda el detalle: si la adenda trae errores se vuelve a pintar la nota entera,
    y eso es una lectura de texto clínico que ningún GET ha registrado — de ahí el
    `log_access_event` explícito. Un GET a esta ruta devuelve a la nota.
    """

    def get(self, request, *args, **kwargs):
        return redirect('patients:note-detail', id=kwargs['id'], note_id=kwargs['note_id'])

    def post(self, request, *args, **kwargs):
        self.load_patient()
        note = self.get_note()
        professional = self.get_professional()

        if note.status != ClinicalNote.Status.SIGNED:
            messages.error(request, 'Solo se pueden añadir adendas a una nota firmada.')
            return redirect('patients:note-detail', id=self.object.pk, note_id=note.pk)
        if professional is None:
            messages.error(request, 'Solo un profesional con perfil en la clínica puede añadir una adenda.')
            return redirect('patients:note-detail', id=self.object.pk, note_id=note.pk)

        form = AddendumForm(request.POST)
        if not form.is_valid():
            response = self.render_to_response(self.get_context_data(addendum_form=form))
            response.render()
            self.log_access_event(request, response)
            return response

        # `objects.create` y no `bulk_create`: la adenda está en la auditoría.
        Addendum.objects.create(note=note, author=professional, text=form.cleaned_data['text'])
        messages.success(request, 'Adenda añadida.')
        return redirect('patients:note-detail', id=self.object.pk, note_id=note.pk)


# ---------------------------------------------------------------------------
# Firma de consentimientos
# ---------------------------------------------------------------------------


class PatientConsentSignView(EpisodeAuthoringMixin, HistoryPageView):
    """Firmar un consentimiento informado en el panel.

    **Siempre la versión vigente y publicada.** Se enseña su texto literal y es ese
    mismo texto el que `SignedConsent.sign()` copia. Entre abrir la pantalla y firmar
    puede publicarse otra versión: el formulario lleva la versión que se enseñó y,
    si ya no es la vigente, no firma (`ConsentSignForm.clean_version`).

    El alta va **siempre** por `SignedConsent.sign()`, que copia el texto y valida la
    imagen por contenido antes de subirla al bucket privado. La firma nunca se
    devuelve como URL: se sirve por `clinical:consent-signature`.

    Cuatro estados, ninguno una pantalla vacía: sin plantillas, elegir plantilla,
    formulario, y —dentro del formulario— el aviso de texto cambiado.
    """

    template_name = 'patients/consent_sign.html'
    template_param = 'plantilla'

    # -- plantillas y versión vigente ---------------------------------------

    def get_signable(self):
        """Plantillas de la clínica del paciente con versión vigente publicada."""
        signable = []
        for template in ConsentTemplate.objects.filter(
            clinic_id=self.object.clinic_id, is_active=True
        ).order_by('name'):
            version = template.current_version
            if version is not None and version.is_published:
                signable.append((template, version))
        return signable

    def resolve(self, signable):
        """Cuál se firma: la pedida, la única que hay, o ninguna (hay que elegir)."""
        requested = self.request.POST.get(self.template_param) or self.request.GET.get(
            self.template_param
        )
        if requested:
            for template, version in signable:
                if str(template.pk) == str(requested):
                    return template, version
            return None, None
        if len(signable) == 1:
            return signable[0]
        return None, None

    def get_form(self, version, data=None):
        return ConsentSignForm(data, patient=self.object, consent_version=version)

    # -- render -------------------------------------------------------------

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        signable = self.get_signable()
        template, version = self.resolve(signable)

        if not signable:
            state = 'sin_plantillas'
        elif template is None:
            state = 'elegir'
        else:
            state = 'formulario'

        context.update({
            'state': state,
            'signable': [{'template': t, 'version': v} for t, v in signable],
            'consent_template': template,
            'version': version,
            'template_param': self.template_param,
            'cancel_url': reverse('patients:tab-consents', kwargs={'id': self.object.pk}),
        })
        if context.get('form') is None:
            context['form'] = self.get_form(version) if state == 'formulario' else None
        return context

    # -- alta ---------------------------------------------------------------

    def post(self, request, *args, **kwargs):
        self.load_patient()
        template, version = self.resolve(self.get_signable())
        if version is None:
            messages.error(request, 'No hay ningún consentimiento vigente que firmar.')
            return redirect(request.path)

        form = self.get_form(version, data=request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        professional = self.get_professional()
        try:
            with transaction.atomic():
                episode = self.resolve_episode(form, professional)
                SignedConsent.sign(
                    version=version,
                    patient=self.object,
                    episode=episode,
                    signature_image=form.signature_file(),
                )
        except ConsentVersionNotPublished:
            messages.error(request, 'Ese consentimiento ya no está vigente. Léelo de nuevo.')
            return redirect(f'{request.path}?{urlencode({self.template_param: template.pk})}')
        except ValidationError as exc:
            # El modelo valida otra vez (clínica, episodio, imagen): si algo se le
            # escapó al formulario, se enseña como error y no como un 500.
            form.add_error(None, exc)
            return self.render_to_response(self.get_context_data(form=form))

        messages.success(request, f'«{template.name}» firmado y archivado en la ficha.')
        return redirect('patients:tab-consents', id=self.object.pk)
