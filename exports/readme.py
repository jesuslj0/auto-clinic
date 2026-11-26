"""Texto del `LEEME.txt` que viaja dentro de cada ZIP.

Es la documentación de la copia para quien la reciba (el propio paciente, otro
centro, otro programa): qué hay, cómo está organizado, qué se dejó fuera y cómo
comprobar que no se ha alterado. El aviso final sobre datos de salud es un
borrador para revisar con el abogado de la clínica.
"""
from __future__ import annotations

from exports.schema import CLINIC, EXCLUDED_MODELS, SCHEMA_VERSION, Entity, Scope

#: Qué es cada fichero `datos/<entidad>.jsonl`.
DESCRIPTIONS = {
    'patients': 'Ficha del paciente (datos de identificación y contacto, y de facturación).',
    'guardians': 'Contactos responsables.',
    'patient_guardians': 'Vínculo entre pacientes y contactos responsables, con la relación.',
    'professionals': 'Profesionales que firman o atienden.',
    'medical_histories': 'Historia clínica (una por paciente) con su número.',
    'episodes': 'Episodios asistenciales.',
    'visits': 'Visitas de cada episodio.',
    'clinical_notes': 'Notas clínicas. Las firmadas llevan su sello (`content_hash`).',
    'addenda': 'Adendas a notas firmadas.',
    'questionnaire_responses': 'Anamnesis: copia literal de cada pregunta y respuesta (`snapshot`).',
    'clinical_alerts': 'Alertas clínicas, también las desactivadas.',
    'lesions': 'Lesiones del mapa del pie: zona clínica y coordenadas del dibujo por separado.',
    'lesion_observations': 'Mediciones de cada lesión en cada visita.',
    'lesion_attachments': 'Fotos de las lesiones (los ficheros están en `adjuntos/`).',
    'performed_procedures': 'Procedimientos realizados, con nombre y precio congelados.',
    'signed_consents': 'Consentimientos firmados: texto literal firmado y firma (en `adjuntos/`).',
    'patient_form_links': 'Enlaces enviados al paciente para rellenar su ficha (solo metadatos).',
    'appointments': 'Citas.',
    'appointment_status_history': 'Cambios de estado de las citas.',
    'invoices': 'Facturas, con las líneas congeladas al emitirlas.',
    'payments': 'Cobros.',
    'conversations': 'Hilos de WhatsApp.',
    'chat_messages': 'Mensajes de WhatsApp.',
    'chat_attachments': 'Fotos y notas de voz de WhatsApp (los ficheros están en `adjuntos/`).',
    'clinic': 'Datos de la clínica (sin credenciales).',
    'users': 'Usuarios de la clínica (sin contraseñas).',
    'services': 'Catálogo de servicios.',
    'service_categories': 'Categorías de servicios.',
    'professional_schedules': 'Horarios de los profesionales.',
    'professional_time_off': 'Ausencias de los profesionales.',
    'professional_services': 'Qué servicios ofrece cada profesional.',
    'questionnaire_templates': 'Plantillas de anamnesis.',
    'template_versions': 'Versiones de las plantillas de anamnesis.',
    'questions': 'Preguntas de cada versión de anamnesis.',
    'consent_templates': 'Plantillas de consentimiento.',
    'consent_versions': 'Versiones (texto) de los consentimientos.',
    'history_sequences': 'Contadores de numeración de historias clínicas.',
    'invoice_sequences': 'Contadores de numeración de facturas.',
    'receipt_sequences': 'Contadores de numeración de recibos.',
    'knowledge_base': 'Base de conocimiento de la clínica.',
    'agent_profile': 'Personalidad del agente de WhatsApp.',
    'whatsapp_templates': 'Plantillas de WhatsApp.',
    'change_log': 'Registro de auditoría de cambios.',
    'access_log': 'Registro de auditoría de accesos.',
}

PATIENT_LIMITS = """\
- Las citas sin ficha de paciente y los hilos de WhatsApp que solo se asocian por teléfono
  no se atribuyen a ningún paciente, así que no salen en esta copia.
- De los contactos responsables solo se da su nombre y la relación: el resto de sus datos
  son de un tercero.
- De los profesionales solo salen los que aparecen en la historia del paciente.
- No incluye el registro de auditoría, que contiene datos del personal.
- Las referencias a plantillas (`version_id`, `template_id`) apuntan a catálogos de la
  clínica que no se incluyen: el contenido literal que se rellenó o firmó está copiado
  en la propia fila (`snapshot`, `text_copy`).
"""

CLINIC_LIMITS = """\
- Las conversaciones de prueba del panel no se exportan.
- Las fotos de profesionales y el logotipo de la clínica se incluyen en `adjuntos/`.
- Los registros de auditoría no llevan clínica: se incluyen los de su personal y los que
  tocan a sus pacientes. Los campos clínicos aparecen enmascarados en origen (se anota
  que cambiaron, no su valor).
- No hay PDF: la copia legible es la de cada paciente, que se pide desde su ficha.
"""


def render_readme(scope: Scope, entities: list[Entity], counts: dict[str, int], generated_at: str) -> str:
    lines: list[str] = []
    add = lines.append
    add('EXPORTACIÓN DE DATOS · Auto Clinic')
    add('=' * 60)
    add('')
    add(f'Clínica:   {scope.clinic.name}')
    if scope.patient is not None:
        add(f'Paciente:  {scope.patient.first_name} {scope.patient.last_name}')
    add(f'Ámbito:    {"clínica entera" if scope.kind == CLINIC else "ficha de un paciente"}')
    add(f'Generada:  {generated_at}')
    add(f'Versión del esquema: {SCHEMA_VERSION}')
    add('')
    add('AVISO')
    add('-----')
    add('Este paquete contiene datos de salud, una categoría especial de datos personales')
    add('(RGPD, art. 9). Guárdelo en un lugar seguro, no lo reenvíe por canales no cifrados')
    add('y elimínelo cuando ya no lo necesite.')
    add('')
    add('CONTENIDO')
    add('---------')
    add('manifest.json            Índice: ficheros, filas y huellas (SHA-256).')
    add('manifest_ficheros.jsonl  Un registro por adjunto, con su huella y su estado.')
    add('verificar_firmas.py      Comprueba el paquete sin necesidad de la aplicación.')
    if scope.kind != CLINIC:
        add('paciente.pdf             Copia legible de la ficha completa.')
    add('datos/                   Los datos, un fichero por entidad, en JSON Lines')
    add('                         (una fila = un objeto JSON por línea, UTF-8).')
    add('adjuntos/                Fotos, firmas y audios, en carpetas por tipo.')
    add('')
    add('Las relaciones entre ficheros van por identificador (`patient_id`, `visit_id`…).')
    add('Fechas en ISO 8601; importes como texto, para no perder decimales. Una fila con')
    add('`deleted_at` informado fue dada de baja lógicamente: se incluye porque forma parte')
    add('del registro. Un adjunto indica su ruta en el paquete en `zip_path`.')
    add('')
    add('ENTIDADES')
    add('---------')
    for entity in entities:
        description = DESCRIPTIONS.get(entity.name, '')
        add(f'datos/{entity.name}.jsonl  ({counts.get(entity.name, 0)} filas)')
        if description:
            add(f'    {description}')
    add('')
    add('CÓMO COMPROBAR QUE NO SE HA ALTERADO')
    add('-------------------------------------')
    add('Descomprima el ZIP y, desde su carpeta, ejecute:  python verificar_firmas.py')
    add('Necesita solo Python 3. Comprueba las huellas de todos los ficheros y que el sello')
    add('de cada nota clínica firmada sigue cuadrando con su contenido.')
    add('')
    add('QUÉ NO SE INCLUYE')
    add('-----------------')
    add('Credenciales y secretos (contraseñas, claves de API, tokens, secretos de')
    add('verificación en dos pasos) y estado técnico interno:')
    for model, reason in EXCLUDED_MODELS.items():
        add(f'  - {model}: {reason}')
    add('')
    add('LÍMITES CONOCIDOS')
    add('-----------------')
    add((CLINIC_LIMITS if scope.kind == CLINIC else PATIENT_LIMITS).rstrip())
    add('')
    return '\n'.join(lines) + '\n'
