"""La copia legible: un PDF que sale con lo mismo que los datos y no toca la red."""
import re

import pytest

from exports.schema import Scope
from tests.exports.helpers import build

weasyprint = pytest.importorskip(
    'weasyprint', reason='WeasyPrint (y pango) no está instalado en este entorno'
)

pytestmark = pytest.mark.django_db


def html_text(scope):
    """El texto de la ficha, antes de convertirla a PDF (así no hace falta un lector de PDF)."""
    from html import unescape

    from exports.pdf import render_patient_html

    html = render_patient_html(scope)
    html = re.sub(r'<style.*?</style>', '', html, flags=re.S)
    return unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html)))


@pytest.fixture
def scope(file_a, clinic_a, patient_a):
    return Scope(clinic=clinic_a, patient=patient_a)


def test_it_is_a_real_pdf(scope):
    from exports.pdf import render_patient_pdf

    data = render_patient_pdf(scope)
    assert data.startswith(b'%PDF') and data.rstrip().endswith(b'%%EOF')
    assert len(data) > 5_000


def test_it_carries_the_patients_file(scope):
    text = html_text(scope)
    for expected in (
        'John Doe', 'HC-', 'Subjetivo ALFA', 'Valoración ALFA', 'Adenda ALFA', 'Alerta ALFA',
        '¿Pregunta ALFA?', 'Respuesta ALFA', 'Texto consentimiento ALFA', 'sello íntegro',
        'Factura F-', '50.00', 'TutorALFA',
    ):
        assert expected in text, expected


def test_soft_deleted_rows_are_marked_not_hidden(scope):
    text = html_text(scope)
    assert 'Borrada ALFA' in text and 'baja lógica' in text


def test_nothing_of_another_clinic_gets_in(file_a, file_b, scope):
    text = html_text(scope)
    assert 'BETA' not in text and 'Clinic Beta' not in text


def test_what_is_not_in_the_patients_copy_stays_out(scope, file_a):
    text = html_text(scope)
    assert file_a.guardian.phone not in text and 'tutor-ALFA@example.com' not in text


def test_a_seal_that_does_not_match_is_flagged(scope, monkeypatch):
    """Si el sello no cuadra, la copia legible lo dice en vez de callarlo.

    No se puede manipular de verdad una nota firmada (lo impide el trigger de
    Postgres), así que se simula el resultado de la comprobación.
    """
    from exports import pdf as module

    monkeypatch.setattr(module, 'verify_note_hash', lambda note: False)

    assert 'el sello no coincide' in html_text(scope)


def test_a_false_answer_is_an_answer_not_a_blank():
    from exports.pdf import _answer_text

    assert _answer_text(False) == 'No' and _answer_text(True) == 'Sí' and _answer_text(0) == '0'
    assert _answer_text(None) is None and _answer_text('') is None and _answer_text([]) is None
    assert _answer_text(['a', 'b']) == 'a, b'


def test_dates_are_shown_in_the_clinics_timezone(file_a, clinic_a, patient_a):
    from datetime import datetime, timezone as tz

    from clinical.models import Episode

    Episode.all_objects.filter(pk=file_a.episode.pk).update(
        opened_at=datetime(2026, 7, 1, 22, 30, tzinfo=tz.utc)
    )
    clinic_a.timezone = 'Europe/Madrid'
    text = html_text(Scope(clinic=clinic_a, patient=patient_a))
    # 22:30 UTC en julio son las 00:30 del día 2 en Madrid.
    assert 'Episodio · 02/07/2026' in text


def test_rendering_never_reaches_the_network(scope, monkeypatch):
    """Una URL externa en la plantilla hace fallar el render, no sale a buscarla."""
    from django.template.loader import render_to_string

    from exports import pdf as module

    original = render_to_string

    def with_remote_image(*args, **kwargs):
        html = original(*args, **kwargs)
        return html.replace('</body>', '<img src="https://example.com/track.png"></body>')

    monkeypatch.setattr(module, 'render_to_string', with_remote_image)

    with pytest.raises(RuntimeError, match='recurso externo'):
        module.render_patient_pdf(scope)


def test_the_zip_of_a_patient_includes_the_pdf_and_lists_it(file_a, clinic_a, patient_a):
    from exports.pdf import render_patient_pdf

    archive, _ = build(Scope(clinic=clinic_a, patient=patient_a), pdf_renderer=render_patient_pdf)

    assert archive.read('paciente.pdf').startswith(b'%PDF')
    import json
    manifest = json.loads(archive.read('manifest.json'))
    assert manifest['otros']['paciente.pdf']['path'] == 'paciente.pdf'


def test_the_clinic_zip_has_no_pdf(file_a, clinic_a):
    from exports.pdf import render_patient_pdf

    archive, _ = build(Scope(clinic=clinic_a), pdf_renderer=render_patient_pdf)
    assert 'paciente.pdf' not in archive.namelist()


def test_an_external_reference_fails_the_export_instead_of_leaving_it_running(
    file_a, clinic_a, admin_user, patient_a, monkeypatch,
):
    from django.template.loader import render_to_string

    from exports import pdf as module
    from exports.models import DataExport
    from exports.tasks import build_export

    original = render_to_string
    monkeypatch.setattr(
        module, 'render_to_string',
        lambda *a, **k: original(*a, **k).replace('</body>', '<img src="https://example.com/x.png"></body>'),
    )
    export = DataExport.objects.create(
        clinic=clinic_a, requested_by=admin_user, scope=DataExport.Scope.PATIENT, patient=patient_a,
    )

    build_export.delay(str(export.pk))

    export.refresh_from_db()
    assert export.status == DataExport.Status.FAILED


def test_a_procedures_zone_and_side_are_shown_in_spanish_not_as_codes(file_a, clinic_a, patient_a):
    from clinical.models import Lesion, PerformedProcedure

    PerformedProcedure.objects.create(
        visit=file_a.visit, service=file_a.procedure.service,
        affected_zone=Lesion.AnatomicalZone.HEEL, laterality=Lesion.Laterality.RIGHT,
    )

    text = html_text(Scope(clinic=clinic_a, patient=patient_a))

    assert f'{Lesion.AnatomicalZone.HEEL.label} · {Lesion.Laterality.RIGHT.label}' in text
    # los códigos internos no salen en la copia legible
    assert 'heel' not in text and 'right' not in text
