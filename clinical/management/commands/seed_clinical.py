"""Datos de ejemplo de la capa clínica, para desarrollo.

Todo lo que siembra es **podología**: la anamnesis, los motivos de consulta, las
notas SOAP, las lesiones del mapa del pie y el consentimiento de cirugía ungueal
cuentan la historia de dos pacientes de una clínica podológica, no de dos
especialidades mezcladas.

Rellena la historia de los pacientes que ya existan en la base de datos:

- episodio cerrado con nota firmada y adenda, y episodio abierto con borrador;
- un cuestionario de anamnesis en dos versiones con sus respuestas;
- lesiones sobre el mapa del pie, con su serie de observaciones y sus fotos;
- procedimientos realizados, con el precio del catálogo congelado;
- consentimientos informados en dos versiones, y firmados por el paciente.

**Solo desarrollo.** Se niega a ejecutarse si `DEBUG` es `False`. El motivo no es
la prudencia genérica: una nota firmada NO se puede borrar —ni por ORM, ni por
SQL, lo impide el trigger de la migración 0002—, así que un sembrado en la base
equivocada se queda ahí para siempre.

Es repetible, y lo es **por pieza**: a un paciente que ya tenga episodios no se
le duplica la historia, pero sí se le completa lo que le falte (lesiones,
procedimientos, consentimiento firmado). Así una base sembrada antes de que
existieran estos modelos se pone al día sin borrar nada. Con `--force` siembra
la historia igualmente (y entonces sí duplica episodios).

Las fotos de lesión y la firma del consentimiento van al **bucket privado**
(`clinical_media`), igual que en producción: no hay ruta especial de siembra. Si
no hay almacén configurado, esas dos piezas se omiten con un aviso y el resto se
siembra igual; `--skip-files` hace lo mismo a propósito.

Una base sembrada cuando el cuestionario era «Anamnesis dental» conserva esa
plantilla con sus versiones y sus respuestas: no se toca ni se borra, porque una
versión publicada es inmutable y una respuesta congelada prueba lo que se
contestó. Simplemente convive con «Anamnesis podológica», que es la que el
comando siembra y rellena a partir de ahora.
"""
import io
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from appointments.models import Appointment
from audit.context import ORIGIN_COMMAND, audit_context
from clinical.models import (
    Addendum,
    ClinicalAlert,
    ClinicalNote,
    ConsentTemplate,
    Episode,
    Lesion,
    LesionAttachment,
    LesionObservation,
    MedicalHistory,
    PerformedProcedure,
    Question,
    QuestionnaireResponse,
    QuestionnaireTemplate,
    SignedConsent,
    Visit,
)
from services.models import Service

TEMPLATE_NAME = 'Anamnesis podológica'

# Cuestionario v1. `(código, texto, tipo, obligatoria, opciones)`, en orden.
#
# El `código` es lo que reconoce el motor de alertas (`clinical/rules.py`): las
# preguntas con código levantan alerta si se contestan que sí; las de código
# `None` son solo informativas. Por eso las cuatro condiciones que mandan en
# podología —diabetes, arteriopatía, neuropatía y riesgo de sangrado— van como
# preguntas propias en vez de dentro del cajón de sastre de las crónicas: son
# justo las que deciden si se puede desbridar, cortar o infiltrar.
#
# **Los textos se pueden cambiar; los códigos no.** Reescribir un enunciado es
# libre (el snapshot guarda el literal que se mostró); tocar o quitar un `code`
# rompe la derivación de alertas.
V1_QUESTIONS = [
    ('has_diabetes', '¿Le han diagnosticado diabetes?',
     Question.AnswerType.BOOLEAN, True, []),
    (None, 'Si es diabético, ¿cómo la tiene controlada? '
           '(última hemoglobina glicosilada o cifras habituales)',
     Question.AnswerType.TEXT, False, []),
    ('has_peripheral_vascular_disease',
     '¿Tiene problemas de circulación en las piernas (dolor al caminar que cede '
     'al pararse, pies fríos o amoratados)?',
     Question.AnswerType.BOOLEAN, True, []),
    ('has_neuropathy',
     '¿Nota hormigueo, quemazón o falta de sensibilidad en los pies?',
     Question.AnswerType.BOOLEAN, True, []),
    ('takes_anticoagulants',
     '¿Toma anticoagulantes (Sintrom, acenocumarol, rivaroxabán…)?',
     Question.AnswerType.BOOLEAN, True, []),
    ('takes_antiplatelets',
     '¿Toma antiagregantes (aspirina, clopidogrel)?',
     Question.AnswerType.BOOLEAN, True, []),
    (None, '¿Padece alguna otra enfermedad crónica (hipertensión, cardiopatía, '
           'artritis reumatoide)?',
     Question.AnswerType.BOOLEAN, True, []),
    (None, '¿Qué medicación toma actualmente?',
     Question.AnswerType.TEXT, False, []),
    ('allergy_local_anesthetics',
     '¿Es alérgico a los anestésicos locales (mepivacaína, lidocaína)?',
     Question.AnswerType.BOOLEAN, True, []),
    (None, '¿Es alérgico a algún otro medicamento o antiséptico '
           '(yodo, clorhexidina)? ¿A cuáles?',
     Question.AnswerType.TEXT, False, []),
    (None, '¿Ha tenido antes úlceras o heridas en los pies que tardaran en curar?',
     Question.AnswerType.BOOLEAN, True, []),
    (None, '¿Le han operado alguna vez de los pies? ¿De qué?',
     Question.AnswerType.TEXT, False, []),
    (None, '¿Qué calzado usa habitualmente?',
     Question.AnswerType.SINGLE_CHOICE, True,
     ['Deportivo', 'De vestir, estrecho', 'Tacón alto',
      'Sandalia o calzado abierto', 'Calzado de seguridad']),
    (None, '¿Cuánto tiempo pasa de pie o caminando al día?',
     Question.AnswerType.SINGLE_CHOICE, False,
     ['Menos de 2 horas', 'Entre 2 y 6 horas', 'Más de 6 horas']),
    (None, '¿Quién le corta habitualmente las uñas?',
     Question.AnswerType.SINGLE_CHOICE, False,
     ['Yo mismo', 'Un familiar', 'En la consulta del podólogo']),
]

# Lo que añade la v2. Es lo que hace visible el versionado: las respuestas de la
# v1 siguen teniendo quince preguntas, no dieciocho.
V2_EXTRA_QUESTIONS = [
    ('allergy_latex', '¿Es alérgico al látex?',
     Question.AnswerType.BOOLEAN, False, []),
    # De elección y no sí/no: la regla del tabaco solo se dispara con «Sí,
    # actualmente», y haberlo dejado no es lo mismo que no haber fumado nunca.
    ('smokes', '¿Fuma?',
     Question.AnswerType.SINGLE_CHOICE, False,
     ['No, nunca', 'Lo dejé', 'Sí, actualmente']),
    (None, '¿Practica algún deporte? ¿Cuál y con qué frecuencia?',
     Question.AnswerType.TEXT, False, []),
]

# Casos clínicos de ejemplo. Se reparten en rueda entre los pacientes, para que
# no salgan todas las historias con el mismo texto.
#
# El orden importa y no es decorativo: el caso 0 va con `LESION_CASES[0]` y el 1
# con `LESION_CASES[1]`, así que el episodio abierto de cada uno describe
# exactamente la lesión que se le siembra en el mapa (la úlcera plantar del
# diabético, la onicocriptosis del corredor). Cambiar uno sin el otro deja un
# paciente que dice una cosa en la nota y otra en el mapa.
#
# El caso 0 se registra sobre la v1 y el 1 sobre la v2 (`_record_anamnesis`
# alterna por índice), y por eso las condiciones están repartidas: entre los dos
# se ejercitan todas las reglas de `clinical/rules.py`, incluidas las que solo
# existen en la v2 (látex y tabaco).
CASES = [
    {
        'closed': {
            'reason': 'Revisión de pie diabético',
            'subjective': 'Diabético tipo 2 de doce años de evolución. Refiere que no nota bien '
                          'los pies y que se ha quemado con el agua caliente sin darse cuenta. '
                          'No inspecciona los pies a diario.',
            'objective': 'Piel seca y descamativa en ambos pies. Hiperqueratosis en la primera '
                         'cabeza metatarsal izquierda. Monofilamento de 10 g: ausencia de '
                         'sensibilidad en 3 de 10 puntos. Pulso pedio disminuido en el pie '
                         'derecho; índice tobillo-brazo de 0,82.',
            'assessment': 'Pie de riesgo alto: neuropatía sensitiva establecida y arteriopatía '
                          'periférica leve, con hiperqueratosis de riesgo en zona de descarga.',
            'plan': 'Deslaminado de la hiperqueratosis y pauta de hidratación con urea al 20 %. '
                    'Educación en autocuidado: inspección diaria, no cortar durezas, no usar '
                    'agua caliente. Revisión cada tres meses y derivación a angiología si '
                    'empeora la clínica vascular.',
            'addendum': 'Se corrige el lado del índice tobillo-brazo: el 0,82 corresponde al pie '
                        'derecho, no al izquierdo.',
        },
        'open': {
            'reason': 'Úlcera plantar en la primera cabeza metatarsal izquierda',
            'subjective': 'Acude por una herida en la planta del pie izquierdo que descubrió al '
                          'quitarse el calcetín. No refiere dolor.',
            'objective': 'Úlcera de 14 × 9,5 mm en la primera cabeza metatarsal izquierda, con '
                         'bordes hiperqueratósicos y lecho granulomatoso. Exudado seroso '
                         'moderado. Sin celulitis ni exposición ósea; pulso pedio presente.',
            'assessment': 'Úlcera neuropática plantar de grado 1, sin signos de infección.',
            'plan': 'Desbridamiento cortante de los bordes, cura en ambiente húmedo y descarga '
                    'con fieltro adhesivo. Control semanal con medición y fotografía. Cultivo y '
                    'derivación urgente si aparecen signos de infección.',
        },
        # Respuestas indexadas por posición de la pregunta en la versión. El
        # cuadro clásico de riesgo podológico: diabetes, arteriopatía,
        # neuropatía y anticoagulación. Las cuatro tienen código, así que el
        # motor levanta cuatro alertas críticas derivadas.
        'answers': {
            0: True,                        # diabetes
            1: 'HbA1c del 7,8 % en la última analítica',
            2: True,                        # enfermedad vascular periférica
            3: True,                        # neuropatía
            4: True,                        # anticoagulantes
            5: False,                       # antiagregantes
            6: True,                        # otra crónica
            7: 'Metformina 850 mg, Sintrom y enalapril',
            8: False,                       # alergia a anestésicos locales
            9: 'Penicilina',                # otras alergias
            10: True,                       # úlceras previas
            11: 'No',                       # cirugías previas
            12: 'Deportivo',                # calzado
            13: 'Menos de 2 horas',         # tiempo de pie
            14: 'Un familiar',              # quién corta las uñas
        },
    },
    {
        'closed': {
            'reason': 'Quiropodia y dolor en el antepié',
            'subjective': 'Refiere dolor punzante bajo el antepié derecho al caminar y al correr, '
                          'de dos meses de evolución, que cede con el reposo. Trabaja de pie.',
            'objective': 'Hiperqueratosis plantar difusa bajo la segunda y tercera cabezas '
                         'metatarsales derechas, con heloma central bajo la tercera. Talones '
                         'con queratosis seca y fisuras incipientes. Pulsos conservados y '
                         'sensibilidad normal.',
            'assessment': 'Metatarsalgia mecánica por sobrecarga del antepié, con hiperqueratosis '
                          'reactiva. Xerosis plantar.',
            'plan': 'Deslaminado y enucleación del heloma. Hidratación con urea al 10 % a diario '
                    'y recomendación de calzado más ancho para el trabajo. Se propone estudio '
                    'biomecánico para valorar soportes plantares. Revisión en ocho semanas.',
            'addendum': 'Se entrega presupuesto de soportes plantares personalizados; queda '
                        'pendiente de confirmación.',
        },
        'open': {
            'reason': 'Uña encarnada en el primer dedo del pie izquierdo',
            'subjective': 'Dolor en el borde interno de la uña del primer dedo izquierdo desde '
                          'hace una semana, que aumenta con la presión del calzado deportivo.',
            'objective': 'Onicocriptosis en el canal medial del hallux izquierdo, con eritema '
                         'perilesional y exudado escaso. Sin fiebre ni linfangitis.',
            'assessment': 'Onicocriptosis de grado II en el canal medial del hallux izquierdo.',
            'plan': 'Espiculectomía y cura con antiséptico, con recomendación de calzado ancho '
                    'mientras cicatriza. Si recidiva, cirugía ungueal con matricectomía química: '
                    'pendiente de valorar la anestesia por la alergia declarada a los '
                    'anestésicos locales.',
        },
        # El otro perfil: sin comorbilidad vascular ni metabólica, pero con dos
        # alergias, antiagregación y tabaquismo activo. Cubre las reglas que la
        # v1 no lleva (látex y tabaco) y la de los antiagregantes, que comparte
        # tipo de alerta con los anticoagulantes.
        'answers': {
            0: False,                       # diabetes
            2: False,                       # enfermedad vascular periférica
            3: False,                       # neuropatía
            4: False,                       # anticoagulantes
            5: True,                        # antiagregantes
            6: False,                       # otra crónica
            7: 'Aspirina 100 mg',
            8: True,                        # alergia a anestésicos locales
            9: 'Ibuprofeno',                # otras alergias
            10: False,                      # úlceras previas
            11: 'No',                       # cirugías previas
            12: 'De vestir, estrecho',      # calzado
            13: 'Más de 6 horas',           # tiempo de pie
            14: 'Yo mismo',                 # quién corta las uñas
            15: True,                       # alergia al látex (solo en la v2)
            16: 'Sí, actualmente',          # tabaquismo (solo en la v2)
            17: 'Correr, tres veces por semana',
        },
    },
]


# ---------------------------------------------------------------------------
# Lesiones
# ---------------------------------------------------------------------------
#
# Una lesión es una serie, no un dato: lo que se siembra no es «hay una úlcera»
# sino cómo iba midiendo esa úlcera visita a visita. Por eso cada caso trae sus
# observaciones con medidas que van cambiando — es lo único que hace visible para
# qué sirve el modelo.
#
# Cada bloque va con el caso clínico de su misma posición en `CASES`: la úlcera
# neuropática es la del paciente diabético y la herida del hallux es la
# onicocriptosis del otro. Si se toca uno hay que tocar el otro.
#
# `x`/`y` son fracciones del SVG (0–1), NUNCA píxeles, y no se derivan de la zona
# anatómica ni al revés: son dos datos distintos que envejecen distinto.
#
# Cada paciente lleva lesiones repartidas por VARIAS VISTAS y por LOS DOS PIES: es
# lo que hace comprobable el filtrado del mapa (con todo en `plantar` no se puede
# distinguir un mapa que filtra de uno que pinta siempre lo mismo).
#
# `observations` puede ir vacío: una lesión sin seguimiento es perfectamente
# válida —el mapa solo necesita su posición y su estado— y ahorra una visita de
# siembra por observación. En ese caso la fecha de detección sale de
# `detected_days_ago`, y la de alta de `resolved_days_ago` si la lesión se
# resuelve. Todas las fechas cuentan días hacia atrás desde hoy.
LESION_CASES = [
    [
        {
            'laterality': Lesion.Laterality.LEFT,
            'view': Lesion.View.PLANTAR,
            'anatomical_zone': Lesion.AnatomicalZone.FIRST_METATARSAL,
            'x': 0.38, 'y': 0.62,
            'lesion_type': Lesion.LesionType.ULCER,
            'resolve': False,
            # `days_ago` cuenta hacia atrás desde hoy: la serie va de más
            # antigua a más reciente y la úlcera va cerrando.
            'observations': [
                {'days_ago': 42, 'length_mm': '14.0', 'width_mm': '9.5', 'depth_mm': '3.0',
                 'description': 'Úlcera neuropática con bordes hiperqueratósicos y lecho '
                                'granulomatoso. Exudado seroso moderado. Sin signos de infección.',
                 'photo': True},
                {'days_ago': 28, 'length_mm': '11.5', 'width_mm': '7.0', 'depth_mm': '2.0',
                 'description': 'Reducción del diámetro tras desbridamiento y descarga. '
                                'Exudado escaso. Tejido de granulación en progresión.'},
                {'days_ago': 14, 'length_mm': '8.0', 'width_mm': '5.5', 'depth_mm': '1.5',
                 'description': 'Continúa la epitelización desde los bordes. Se mantiene '
                                'la descarga con fieltro adhesivo.'},
                {'days_ago': 3, 'length_mm': '5.0', 'width_mm': '3.5', 'depth_mm': '1.0',
                 'description': 'Lecho limpio y epitelizado en dos tercios. Se prevé cierre '
                                'completo en las próximas dos semanas.',
                 'photo': True},
            ],
        },
        {
            'laterality': Lesion.Laterality.RIGHT,
            'view': Lesion.View.DORSAL,
            'anatomical_zone': Lesion.AnatomicalZone.FIFTH_TOE,
            'x': 0.71, 'y': 0.24,
            'lesion_type': Lesion.LesionType.HYPERKERATOSIS,
            'resolve': True,
            'observations': [
                {'days_ago': 42, 'length_mm': '6.0', 'width_mm': '6.0',
                 'description': 'Heloma duro dorsal por conflicto con el calzado. '
                                'Doloroso a la presión.'},
                {'days_ago': 14, 'description': 'Deslaminado completo. Piel íntegra sin '
                                                'recidiva. Se recomienda calzado más ancho.'},
            ],
        },
        {
            # Sin seguimiento: se marcó en el mapa y ahí sigue.
            'laterality': Lesion.Laterality.LEFT,
            'view': Lesion.View.MEDIAL,
            'anatomical_zone': Lesion.AnatomicalZone.MEDIAL_ARCH,
            'x': 0.47, 'y': 0.76,
            'lesion_type': Lesion.LesionType.HYPERKERATOSIS,
            'resolve': False,
            'detected_days_ago': 21,
            'observations': [],
        },
        {
            'laterality': Lesion.Laterality.RIGHT,
            'view': Lesion.View.LATERAL,
            'anatomical_zone': Lesion.AnatomicalZone.FIFTH_METATARSAL,
            'x': 0.32, 'y': 0.73,
            'lesion_type': Lesion.LesionType.BLISTER,
            'resolve': True,
            'detected_days_ago': 25,
            'resolved_days_ago': 9,
            'observations': [],
        },
    ],
    [
        {
            'laterality': Lesion.Laterality.RIGHT,
            'view': Lesion.View.PLANTAR,
            'anatomical_zone': Lesion.AnatomicalZone.HEEL,
            'x': 0.55, 'y': 0.86,
            'lesion_type': Lesion.LesionType.WOUND,
            'resolve': False,
            'observations': [
                {'days_ago': 30, 'length_mm': '22.0', 'width_mm': '4.0', 'depth_mm': '2.5',
                 'description': 'Fisura profunda en talón sobre base de queratosis seca. '
                                'Sangrado al apoyo.',
                 'photo': True},
                {'days_ago': 10, 'length_mm': '12.0', 'width_mm': '2.0', 'depth_mm': '1.0',
                 'description': 'Fisura cerrada en su mitad posterior tras pauta de urea al 30 %. '
                                'Sin dolor al apoyo.'},
            ],
        },
        {
            'laterality': Lesion.Laterality.LEFT,
            'view': Lesion.View.DORSAL,
            'anatomical_zone': Lesion.AnatomicalZone.SECOND_TOE,
            'x': 0.48, 'y': 0.13,
            'lesion_type': Lesion.LesionType.WOUND,
            'resolve': True,
            'detected_days_ago': 30,
            'resolved_days_ago': 6,
            'observations': [],
        },
        {
            'laterality': Lesion.Laterality.LEFT,
            'view': Lesion.View.LATERAL,
            'anatomical_zone': Lesion.AnatomicalZone.LATERAL_BORDER,
            'x': 0.53, 'y': 0.79,
            'lesion_type': Lesion.LesionType.HYPERKERATOSIS,
            'resolve': False,
            'detected_days_ago': 12,
            'observations': [],
        },
        {
            # La onicocriptosis del episodio abierto, marcada en el mapa: canal
            # medial del hallux izquierdo, sin medidas todavía.
            'laterality': Lesion.Laterality.LEFT,
            'view': Lesion.View.MEDIAL,
            'anatomical_zone': Lesion.AnatomicalZone.HALLUX,
            'x': 0.16, 'y': 0.75,
            'lesion_type': Lesion.LesionType.WOUND,
            'resolve': False,
            'detected_days_ago': 7,
            'observations': [],
        },
    ],
]

# ---------------------------------------------------------------------------
# Procedimientos realizados
# ---------------------------------------------------------------------------
#
# Se cuelgan de las visitas que ya existen y toman su nombre y su precio del
# catálogo REAL de la clínica, que es lo que hace visible el congelado: si luego
# se sube el precio del servicio, estos importes no se mueven.
#
# Van por paciente, igual que las lesiones y por el mismo motivo: las zonas
# tratadas son las que el paciente tiene marcadas en el mapa (se desbrida la
# úlcera que hay, no una cualquiera). El servicio, en cambio, sale del catálogo
# de la clínica y no de aquí — esta capa registra la zona, no inventa la tarifa.
#
# `price` a `None` significa «lo que diga el catálogo hoy». Con un importe
# explícito se siembra el otro caso, el del servicio de precio variable que se
# cobra por lo que se hizo y no por el mínimo de la ficha.
PROCEDURE_CASES = [
    [
        # Pie diabético: deslaminado de la queratosis de descarga, desbridamiento
        # de la úlcera y quiropodia del heloma del quinto dedo.
        {'zone': Lesion.AnatomicalZone.FIRST_METATARSAL,
         'laterality': Lesion.Laterality.LEFT, 'price': None},
        {'zone': Lesion.AnatomicalZone.FIRST_METATARSAL,
         'laterality': Lesion.Laterality.LEFT, 'price': Decimal('45.00')},
        {'zone': Lesion.AnatomicalZone.FIFTH_TOE,
         'laterality': Lesion.Laterality.RIGHT, 'price': None},
    ],
    [
        # Quiropodia del antepié, tratamiento de las fisuras del talón y
        # espiculectomía del hallux.
        {'zone': Lesion.AnatomicalZone.THIRD_METATARSAL,
         'laterality': Lesion.Laterality.RIGHT, 'price': None},
        {'zone': Lesion.AnatomicalZone.HEEL,
         'laterality': Lesion.Laterality.RIGHT, 'price': Decimal('45.00')},
        {'zone': Lesion.AnatomicalZone.HALLUX,
         'laterality': Lesion.Laterality.LEFT, 'price': None},
    ],
]

# ---------------------------------------------------------------------------
# Consentimientos informados
# ---------------------------------------------------------------------------
#
# Dos versiones publicadas de la misma plantilla, como en la anamnesis y por el
# mismo motivo: lo que se firma es una versión concreta, y las firmas de la v1
# siguen probando el texto de la v1 aunque la v2 lo haya reescrito entero.
CONSENT_NAME = 'Consentimiento informado para cirugía ungueal'

CONSENT_V1_TEXT = """CONSENTIMIENTO INFORMADO PARA CIRUGÍA UNGUEAL

1. Naturaleza del procedimiento.
La onicocriptosis (uña encarnada) consiste en la penetración del borde de la
lámina ungueal en los tejidos blandos adyacentes. El tratamiento quirúrgico
propuesto consiste en la extirpación parcial o total de la lámina ungueal, bajo
anestesia local, y en la destrucción química de la matriz correspondiente
mediante fenol para evitar la recidiva.

2. Objetivo.
Eliminar el dolor y la inflamación producidos por la lesión y evitar que el
cuadro se repita.

3. Riesgos generales.
Todo procedimiento quirúrgico, por sencillo que sea, conlleva riesgo de
infección, sangrado, retraso en la cicatrización y reacción a la anestesia
local.

4. Riesgos específicos.
Recidiva del cuadro, cambio permanente en la forma y el ancho de la uña,
alteración de la sensibilidad de la zona y, con menor frecuencia, granuloma
piógeno o retraso de la cicatrización superior a lo previsto.

5. Alternativas.
Tratamiento conservador mediante deslaminado y espiculectomía periódica, sin
destrucción de la matriz, con mayor probabilidad de recidiva.

6. Declaración.
Declaro que se me ha explicado el procedimiento en un lenguaje comprensible, que
he podido hacer preguntas y que se han resuelto mis dudas. Conozco que puedo
revocar este consentimiento en cualquier momento y sin dar explicaciones, antes
de la realización del procedimiento.
"""

# La v2 añade el apartado de conservación de imágenes: es el cambio típico que
# obliga a publicar una versión nueva en vez de retocar la anterior.
CONSENT_V2_TEXT = CONSENT_V1_TEXT + """
7. Registro fotográfico.
Autorizo la toma de fotografías clínicas de la zona tratada con la finalidad
exclusiva de documentar la evolución en mi historia clínica. Estas imágenes se
conservan con las mismas garantías que el resto de mi documentación clínica y no
se utilizan con fines docentes, comerciales ni de difusión sin un consentimiento
adicional y específico por mi parte.
"""


def _signature_png() -> ContentFile:
    """Una firma manuscrita de mentira, en PNG.

    Se genera aquí en vez de guardar un fichero de ejemplo en el repositorio por
    dos motivos: el repositorio no es sitio para binarios de siembra, y así la
    imagen pasa por exactamente la misma validación por contenido
    (`validate_clinical_image`) que la firma de un paciente real.
    """
    from PIL import Image, ImageDraw

    image = Image.new('RGB', (640, 220), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    stroke = [
        (60, 160), (95, 105), (130, 150), (168, 80), (205, 155),
        (245, 95), (290, 145), (335, 70), (380, 150), (430, 110),
        (480, 140), (530, 100), (575, 130),
    ]
    draw.line(stroke, fill=(24, 34, 84), width=6, joint='curve')
    draw.line([(60, 185), (575, 178)], fill=(24, 34, 84), width=2)

    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    buffer.seek(0)
    return ContentFile(buffer.getvalue(), name='firma.png')


def _lesion_photo_jpeg(seed: int) -> ContentFile:
    """Una foto de lesión de mentira, en JPEG.

    No pretende parecer una lesión: es una imagen sintética con un tono y una
    mancha que cambian con `seed`, lo justo para distinguir una foto de otra en
    la ficha y para que el flujo de subida al bucket sea el de verdad.
    """
    from PIL import Image, ImageDraw

    width, height = 480, 360
    base = (208 - (seed * 9) % 40, 168 - (seed * 5) % 30, 150 - (seed * 7) % 25)
    image = Image.new('RGB', (width, height), base)
    draw = ImageDraw.Draw(image)

    center_x = width // 2 + (seed * 13) % 60 - 30
    center_y = height // 2 + (seed * 7) % 40 - 20
    radius = 70 - (seed * 11) % 35
    draw.ellipse(
        [center_x - radius, center_y - radius, center_x + radius, center_y + radius],
        fill=(150 - seed % 30, 60, 55), outline=(90, 35, 30), width=4,
    )

    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=80)
    buffer.seek(0)
    return ContentFile(buffer.getvalue(), name='lesion.jpg')


class Command(BaseCommand):
    help = (
        'Crea datos clínicos de ejemplo (episodios, visitas, notas SOAP, '
        'anamnesis, lesiones con su evolución, procedimientos y consentimientos '
        'firmados) para los pacientes ya existentes. Solo para desarrollo.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--clinic',
            help='clinic_id de una clínica concreta. Por defecto, todas.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='No escribe nada; solo informa de lo que crearía.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Siembra también los pacientes que ya tengan actividad clínica.',
        )
        parser.add_argument(
            '--refresh-anamnesis',
            action='store_true',
            help='A los pacientes ya sembrados les registra una anamnesis nueva '
                 'sobre la versión vigente, en vez de saltarlos. Es la forma de '
                 'ver el motor de alertas sobre datos que ya existen.',
        )
        parser.add_argument(
            '--skip-files',
            action='store_true',
            help='No sube nada al bucket privado: siembra las lesiones y sus '
                 'observaciones sin fotos y se salta los consentimientos '
                 'firmados (un consentimiento sin firma no existe).',
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                'seed_clinical solo se ejecuta con DEBUG=True: es un comando de '
                'desarrollo y lo que crea (notas firmadas) no se puede borrar.'
            )

        from core.models import Clinic

        clinics = Clinic.objects.all()
        if options['clinic']:
            clinics = clinics.filter(clinic_id=options['clinic'])
            if not clinics.exists():
                raise CommandError(f'No existe la clínica «{options["clinic"]}».')

        self.dry_run = options['dry_run']
        self.refresh_anamnesis = options['refresh_anamnesis']
        self.files_enabled = self._resolve_file_storage(options['skip_files'])
        if self.dry_run:
            self.stdout.write(self.style.WARNING('DRY-RUN: no se escribe nada.'))

        totals = {'historias': 0, 'episodios': 0, 'visitas': 0, 'notas': 0,
                  'adendas': 0, 'respuestas': 0, 'alertas': 0, 'lesiones': 0,
                  'observaciones': 0, 'fotos': 0, 'procedimientos': 0,
                  'consentimientos': 0}

        # Todo lo que se cree queda en el ChangeLog atribuido al comando, no a un
        # usuario fantasma.
        with audit_context(origin=ORIGIN_COMMAND, user_repr='comando seed_clinical'):
            for clinic in clinics:
                self._seed_clinic(clinic, options['force'], totals)

        self.stdout.write('')
        summary = ', '.join(f'{count} {name}' for name, count in totals.items())
        verb = 'Se crearía' if self.dry_run else 'Creado'
        self.stdout.write(self.style.SUCCESS(f'{verb}: {summary}.'))

    def _resolve_file_storage(self, skip_files) -> bool:
        """¿Se pueden sembrar fotos y firmas? Avisa de por qué no, si no.

        No hay atajo de siembra: las imágenes van al mismo bucket privado que en
        producción, con la misma validación. Si no está configurado, lo que se
        omite se dice en voz alta en vez de reventar a mitad del sembrado con una
        traza de boto3 que no explica nada.
        """
        if skip_files:
            self.stdout.write(self.style.WARNING(
                '--skip-files: sin fotos de lesión ni consentimientos firmados.'
            ))
            return False

        configured = bool(
            getattr(settings, 'R2_ACCESS_KEY_ID', '')
            and getattr(settings, 'R2_BUCKET_NAME', '')
            and getattr(settings, 'R2_ENDPOINT_URL', '')
        )
        if not configured:
            self.stdout.write(self.style.WARNING(
                'Sin almacén de ficheros clínicos configurado (R2_*): se siembran '
                'las lesiones sin fotos y se omiten los consentimientos firmados, '
                'que no existen sin firma. El resto va igual.'
            ))
            return False
        return True

    # ------------------------------------------------------------------
    # Por clínica
    # ------------------------------------------------------------------

    def _seed_clinic(self, clinic, force, totals):
        self.stdout.write(self.style.MIGRATE_HEADING(f'\n{clinic.name} ({clinic.clinic_id})'))

        professional = clinic.professionals.filter(is_active=True).first()
        if professional is None:
            self.stdout.write(self.style.WARNING(
                '  Sin profesionales activos: una visita necesita uno. Se salta.'
            ))
            return

        patients = list(clinic.patients.order_by('pk'))
        if not patients:
            self.stdout.write(self.style.WARNING('  Sin pacientes. Se salta.'))
            return

        versions = self._ensure_questionnaire(clinic)
        consent_version = self._ensure_consent(clinic)
        # El catálogo real de la clínica: un procedimiento congela lo que valía
        # un servicio de verdad, no un precio inventado.
        catalog = list(clinic.services.filter(is_active=True).order_by('pk'))
        if not catalog:
            self.stdout.write(self.style.WARNING(
                '  Sin servicios activos en el catálogo: se omiten los procedimientos.'
            ))

        for index, patient in enumerate(patients):
            self._seed_patient(patient, professional, versions, index, force, totals,
                               consent_version=consent_version, catalog=catalog)

    def _ensure_questionnaire(self, clinic):
        """Cuestionario de anamnesis con v1 y v2 publicadas. Devuelve `(v1, vigente)`.

        Si ya existe, se reutiliza tal cual: republicar versiones en cada
        ejecución llenaría el histórico de ruido. La excepción es que la versión
        vigente no tenga ninguna pregunta codificada —sembrada antes de que
        existiera `Question.code`—: entonces se publica una versión nueva con los
        códigos puestos, porque una versión publicada es inmutable y no se le
        pueden añadir. Es exactamente el flujo real de «editar» un cuestionario.
        """
        existing = QuestionnaireTemplate.objects.filter(
            clinic=clinic, name=TEMPLATE_NAME
        ).first()
        if existing is not None:
            published = list(existing.versions.filter(is_published=True).order_by('number'))
            current = existing.current_version
            if published and current is not None:
                if current.questions.exclude(code=None).exists():
                    self.stdout.write(
                        f'  Cuestionario «{TEMPLATE_NAME}» ya existe: se reutiliza.'
                    )
                    return published[0], current
                if self.dry_run:
                    self.stdout.write(
                        f'  La versión vigente de «{TEMPLATE_NAME}» no tiene códigos: '
                        f'publicaría una versión nueva con ellos.'
                    )
                    return None, None
                coded = self._publish_coded_version(existing)
                self.stdout.write(
                    f'  «{TEMPLATE_NAME}»: la vigente no tenía códigos; publicada la '
                    f'v{coded.number} con ellos (las anteriores quedan intactas).'
                )
                return published[0], coded

        if self.dry_run:
            self.stdout.write(
                f'  Crearía el cuestionario «{TEMPLATE_NAME}» con dos versiones '
                f'({len(V1_QUESTIONS)} y {len(V1_QUESTIONS) + len(V2_EXTRA_QUESTIONS)} preguntas).'
            )
            return None, None

        with transaction.atomic():
            template = existing or QuestionnaireTemplate.objects.create(
                clinic=clinic, name=TEMPLATE_NAME, specialty='podología general',
            )

            v1 = template.new_draft_version(copy_questions_from=False)
            self._add_questions(v1, V1_QUESTIONS, start_order=1)
            v1.publish()

            # La v2 clona la vigente y añade preguntas: así es como se "edita" un
            # cuestionario publicado.
            v2 = template.new_draft_version()
            self._add_questions(v2, V2_EXTRA_QUESTIONS, start_order=len(V1_QUESTIONS) + 1)
            v2.publish()

        self.stdout.write(
            f'  Cuestionario «{TEMPLATE_NAME}»: v1 ({len(V1_QUESTIONS)} preguntas) '
            f'y v2 ({len(V1_QUESTIONS) + len(V2_EXTRA_QUESTIONS)}, vigente).'
        )
        return v1, v2

    def _publish_coded_version(self, template):
        """Publica una versión nueva con el juego completo de preguntas codificadas."""
        with transaction.atomic():
            version = template.new_draft_version(copy_questions_from=False)
            self._add_questions(version, V1_QUESTIONS, start_order=1)
            self._add_questions(
                version, V2_EXTRA_QUESTIONS, start_order=len(V1_QUESTIONS) + 1
            )
            version.publish()
        return version

    def _add_questions(self, version, specs, start_order):
        for offset, (code, text, answer_type, required, options) in enumerate(specs):
            Question.objects.create(
                version=version,
                code=code,
                text=text,
                answer_type=answer_type,
                order=start_order + offset,
                is_required=required,
                options=list(options),
            )

    # ------------------------------------------------------------------
    # Por paciente
    # ------------------------------------------------------------------

    def _seed_patient(self, patient, professional, versions, index, force, totals,
                      consent_version=None, catalog=None):
        name = f'{patient.first_name} {patient.last_name}'
        history = MedicalHistory.all_objects.filter(patient=patient).first()

        if history is not None and history.episodes.exists() and not force:
            if self.refresh_anamnesis:
                self._refresh_anamnesis(patient, name, history, professional,
                                        versions, index, totals)
            else:
                self.stdout.write(f'  {name}: ya tiene historia con episodios.')
            # Aunque la historia esté sembrada, se le completa lo que le falte:
            # es lo que pone al día una base anterior a estos modelos sin tocar
            # ni un episodio de los que ya hay.
            self._complete_extras(patient, name, history, professional, index,
                                  totals, consent_version, catalog)
            return

        case = CASES[index % len(CASES)]
        # Se cuelgan las visitas de citas reales cuando las hay: es lo que hace
        # que el panel enseñe la cadena cita → visita → nota.
        appointments = list(
            patient.appointments.filter(
                status=Appointment.Status.COMPLETED
            ).order_by('scheduled_at')
        )
        now = timezone.now()
        first_at = appointments[0].scheduled_at if appointments else now - timedelta(days=90)
        second_at = appointments[1].scheduled_at if len(appointments) > 1 else now - timedelta(days=5)

        if self.dry_run:
            missing = ' (crearía su historia)' if history is None else ''
            self.stdout.write(
                f'  {name}{missing}: 2 episodios, 2 visitas, 2 notas '
                f'(1 firmada), 1 adenda y 1 respuesta de anamnesis.'
            )
            if history is None:
                totals['historias'] += 1
            totals['episodios'] += 2
            totals['visitas'] += 2
            totals['notas'] += 2
            totals['adendas'] += 1
            totals['respuestas'] += 1
            self._report_extras_dry_run(name, index, totals, consent_version, catalog,
                                        patient=patient, history=history)
            return

        with transaction.atomic():
            if history is None:
                # Los pacientes dados de alta antes de que existiese la app
                # `clinical` no pasaron por la señal que crea la historia.
                history = MedicalHistory.objects.create(patient=patient, clinic=patient.clinic)
                totals['historias'] += 1

            # --- Episodio cerrado, con nota firmada y adenda ------------------
            closed = case['closed']
            episode = Episode.objects.create(
                history=history,
                reason=closed['reason'],
                opened_at=first_at,
                responsible_professional=professional,
            )
            visit = Visit.objects.create(
                episode=episode,
                professional=professional,
                appointment=appointments[0] if appointments else None,
                occurred_at=first_at,
            )
            note = ClinicalNote.objects.create(
                visit=visit,
                subjective=closed['subjective'],
                objective=closed['objective'],
                assessment=closed['assessment'],
                plan=closed['plan'],
            )
            note.sign(professional)
            Addendum.objects.create(note=note, author=professional, text=closed['addendum'])

            # El alta se pone a mano y no con `close()`, que fecharía el episodio
            # hoy y dejaría una historia con fechas incoherentes.
            episode.status = Episode.Status.CLOSED
            episode.discharged_at = first_at + timedelta(hours=1)
            episode.save(update_fields=['status', 'discharged_at', 'updated_at'])

            # --- Episodio abierto, con nota en borrador -----------------------
            open_case = case['open']
            episode_open = Episode.objects.create(
                history=history,
                reason=open_case['reason'],
                opened_at=second_at,
                responsible_professional=professional,
            )
            visit_open = Visit.objects.create(
                episode=episode_open,
                professional=professional,
                appointment=appointments[1] if len(appointments) > 1 else None,
                occurred_at=second_at,
            )
            ClinicalNote.objects.create(
                visit=visit_open,
                subjective=open_case['subjective'],
                objective=open_case['objective'],
                assessment=open_case['assessment'],
                plan=open_case['plan'],
            )

            totals['episodios'] += 2
            totals['visitas'] += 2
            totals['notas'] += 2
            totals['adendas'] += 1

            # --- Anamnesis ----------------------------------------------------
            alerts = self._record_anamnesis(patient, episode_open, professional, versions,
                                            case, index, second_at)
            if alerts is not False:
                totals['respuestas'] += 1
                totals['alertas'] += alerts

        self.stdout.write(
            f'  {name}: historia {history.number}, episodio cerrado '
            f'«{closed["reason"]}» (nota firmada + adenda) y episodio abierto '
            f'«{open_case["reason"]}» (borrador).'
        )

        self._complete_extras(patient, name, history, professional, index,
                              totals, consent_version, catalog)

    def _refresh_anamnesis(self, patient, name, history, professional, versions, index, totals):
        """Registra una anamnesis nueva a un paciente ya sembrado.

        Es el camino para ver el motor de alertas sobre datos que ya existían:
        no se toca nada de lo anterior —las notas firmadas y las respuestas
        antiguas siguen donde estaban—, solo se añade una respuesta más sobre la
        versión vigente. Si esa anamnesis declara alguna condición crítica, salen
        sus alertas derivadas.
        """
        episode = (
            history.episodes.filter(status=Episode.Status.OPEN).order_by('-opened_at').first()
            or history.episodes.order_by('-opened_at').first()
        )
        if episode is None:
            self.stdout.write(f'  {name}: sin episodios donde colgar la anamnesis. Se salta.')
            return

        if self.dry_run:
            self.stdout.write(f'  {name}: registraría una anamnesis nueva sobre la versión vigente.')
            totals['respuestas'] += 1
            return

        case = CASES[index % len(CASES)]
        alerts = self._record_anamnesis(
            patient, episode, professional, versions, case, index,
            timezone.now(), use_current_version=True,
        )
        if alerts is False:
            return

        totals['respuestas'] += 1
        totals['alertas'] += alerts
        detail = f'{alerts} alerta(s) derivada(s)' if alerts else 'sin condiciones críticas'
        self.stdout.write(f'  {name}: anamnesis nueva sobre «{episode.reason}» → {detail}.')

    def _record_anamnesis(self, patient, episode, professional, versions, case, index,
                          filled_at, use_current_version=False):
        v1, v2 = versions
        if v1 is None:
            return False

        # Se alternan versión y canal a propósito: el panel enseña así una
        # respuesta sobre una versión antigua junto a otra sobre la vigente.
        if index % 2 == 0:
            version, source, created_by = v1, QuestionnaireResponse.Source.PROFESSIONAL, professional
        else:
            version, source, created_by = v2, QuestionnaireResponse.Source.PATIENT_WEB, None

        if use_current_version:
            # En el refresco manda la vigente: es la única que lleva los códigos
            # que el motor de alertas sabe leer.
            version = v2

        questions = list(version.questions.all())
        answers = {
            questions[position].pk: value
            for position, value in case['answers'].items()
            if position < len(questions)
        }
        # `record()` dispara el motor de alertas: si la anamnesis declara alguna
        # condición crítica, el paciente sale ya con su alerta derivada.
        response = QuestionnaireResponse.record(
            version=version,
            patient=patient,
            episode=episode,
            answers=answers,
            source=source,
            created_by=created_by,
            filled_at=filled_at,
        )
        return ClinicalAlert.objects.filter(source_response=response).count()

    # ------------------------------------------------------------------
    # Lesiones, procedimientos y consentimientos
    # ------------------------------------------------------------------

    def _report_extras_dry_run(self, name, index, totals, consent_version, catalog,
                               patient=None, history=None):
        """Informa de lo que se crearía, comprobando lo mismo que la ejecución real.

        Repetir aquí las comprobaciones de existencia es imprescindible: un seco
        que anuncie tres lesiones sobre una base que ya las tiene no sirve para
        decidir nada.
        """
        episode = None
        if history is not None:
            episode = (
                history.episodes.filter(status=Episode.Status.OPEN).order_by('-opened_at').first()
                or history.episodes.order_by('-opened_at').first()
            )

        procedures_pending = bool(catalog) and (
            episode is None
            or not PerformedProcedure.objects.filter(visit__episode=episode).exists()
        )
        consent_pending = self.files_enabled and (
            patient is None or not SignedConsent.objects.filter(patient=patient).exists()
        )

        case = self._pending_lesion_cases(episode, index)
        observations = sum(len(lesion.get('observations', [])) for lesion in case)
        photos = sum(
            1 for lesion in case for observation in lesion.get('observations', [])
            if observation.get('photo') and self.files_enabled
        )
        procedures = len(PROCEDURE_CASES[index % len(PROCEDURE_CASES)]) if procedures_pending else 0
        # `consent_version` viene a `None` en seco porque la plantilla aún no
        # existe; lo que hay que informar es lo que se crearía, no eso.
        consents = 1 if consent_pending else 0

        if not (case or procedures or consents):
            return

        totals['lesiones'] += len(case)
        totals['observaciones'] += observations
        totals['fotos'] += photos
        totals['procedimientos'] += procedures
        totals['consentimientos'] += consents
        self.stdout.write(
            f'    …y {len(case)} lesión/es con {observations} observación/es '
            f'({photos} foto/s), {procedures} procedimiento/s y {consents} '
            f'consentimiento/s firmado/s.'
        )

    def _complete_extras(self, patient, name, history, professional, index, totals,
                         consent_version, catalog):
        """Añade al paciente lo que le falte de lesiones, procedimientos y consentimientos.

        Cada pieza mira si ya existe antes de crearse, así que ejecutar el
        comando dos veces no duplica nada — y una base sembrada antes de que
        estos modelos existieran se completa sin tocar la historia previa.
        """
        if self.dry_run:
            self._report_extras_dry_run(name, index, totals, consent_version, catalog,
                                        patient=patient, history=history)
            return

        # Las lesiones y sus observaciones cuelgan del episodio ABIERTO: una
        # visita nueva no cabe en uno cerrado sin reabrirlo, y reabrir un
        # episodio para sembrar sería falsear la historia.
        episode = (
            history.episodes.filter(status=Episode.Status.OPEN).order_by('-opened_at').first()
            or history.episodes.order_by('-opened_at').first()
        )
        if episode is None:
            self.stdout.write(f'  {name}: sin episodios donde colgar el seguimiento. Se salta.')
            return

        created = []
        created += self._seed_lesions(patient, episode, professional, index, totals)
        created += self._seed_procedures(episode, professional, catalog, index, totals)
        created += self._seed_consent(patient, episode, consent_version, totals)

        if created:
            self.stdout.write(f'    {name}: ' + ', '.join(created) + '.')

    def _pending_lesion_cases(self, episode, index):
        """Qué lesiones del caso le faltan todavía a este episodio.

        La comprobación es **por pieza** (pie + vista + zona dentro del
        episodio) y no «¿tiene ya alguna lesión?»: así una base sembrada antes
        de que el caso incluyera las vistas medial y lateral se pone al día sin
        duplicar ni una de las que ya estaban. Repetir el comando no crea nada.

        Un episodio cerrado se deja en paz: no admite visitas nuevas y sin
        visita no hay observación, y reabrirlo para sembrar sería falsear la
        historia. Sin episodio (paciente que aún no tiene historia) están
        pendientes todas: el sembrado de la historia va por delante en la misma
        pasada.
        """
        cases = LESION_CASES[index % len(LESION_CASES)]
        if episode is None:
            return cases
        if episode.status == Episode.Status.CLOSED:
            return []
        existing = set(
            Lesion.objects.filter(episode=episode)
            .values_list('laterality', 'view', 'anatomical_zone')
        )
        return [
            case for case in cases
            if (case['laterality'], case['view'], case['anatomical_zone']) not in existing
        ]

    def _seed_lesions(self, patient, episode, professional, index, totals):
        """Lesiones del episodio con su serie de observaciones y sus fotos.

        Cada observación necesita una visita del MISMO episodio (lo exige el
        modelo: una evolución repartida entre procesos asistenciales distintos es
        ilegible), así que se crean visitas de seguimiento fechadas hacia atrás.

        Una lesión sin observaciones no crea ninguna visita: es la marca sobre el
        mapa y nada más, que es un caso real (se localiza hoy y se mide en la
        próxima visita).
        """
        cases = self._pending_lesion_cases(episode, index)
        if not cases:
            return []

        today = timezone.localdate()
        now = timezone.now()
        summary = []

        for case in cases:
            observations = case.get('observations', [])
            with transaction.atomic():
                # La lesión se detecta el día de su primera observación; si no
                # tiene ninguna, lo dice el caso.
                detected_days_ago = (
                    observations[0]['days_ago'] if observations
                    else case.get('detected_days_ago', 0)
                )
                first_day = today - timedelta(days=detected_days_ago)
                lesion = Lesion.objects.create(
                    episode=episode,
                    laterality=case['laterality'],
                    view=case['view'],
                    anatomical_zone=case['anatomical_zone'],
                    x=case['x'],
                    y=case['y'],
                    lesion_type=case['lesion_type'],
                    detected_at=first_day,
                    created_by=professional,
                )
                totals['lesiones'] += 1

                photos = 0
                for spec in observations:
                    day = today - timedelta(days=spec['days_ago'])
                    visit = Visit.objects.create(
                        episode=episode,
                        professional=professional,
                        occurred_at=now - timedelta(days=spec['days_ago']),
                    )
                    totals['visitas'] += 1

                    observation = LesionObservation.objects.create(
                        lesion=lesion,
                        visit=visit,
                        observed_at=day,
                        length_mm=spec.get('length_mm'),
                        width_mm=spec.get('width_mm'),
                        depth_mm=spec.get('depth_mm'),
                        description=spec.get('description', ''),
                        created_by=professional,
                    )
                    totals['observaciones'] += 1

                    if spec.get('photo') and self.files_enabled:
                        LesionAttachment.objects.create(
                            observation=observation,
                            file=_lesion_photo_jpeg(spec['days_ago']),
                            source=LesionAttachment.Source.PROFESSIONAL,
                        )
                        totals['fotos'] += 1
                        photos += 1

                if case['resolve']:
                    # Por `resolve()` y no a mano: es el camino que mantiene
                    # coherentes el estado y la fecha de alta de la lesión.
                    resolved_days_ago = (
                        observations[-1]['days_ago'] if observations
                        else case.get('resolved_days_ago', 0)
                    )
                    lesion.resolve(on=today - timedelta(days=resolved_days_ago))

            state = 'resuelta' if case['resolve'] else 'activa'
            photo_note = f', {photos} foto(s)' if photos else ''
            follow_up = (
                f'{len(observations)} observación/es{photo_note}'
                if observations else 'sin seguimiento'
            )
            summary.append(
                f'{lesion.get_lesion_type_display().lower()} en '
                f'{lesion.get_anatomical_zone_display().lower()} '
                f'({lesion.get_view_display().lower()}, '
                f'{lesion.get_laterality_display().lower()}, {state}, {follow_up})'
            )

        return summary

    def _seed_procedures(self, episode, professional, catalog, index, totals):
        """Procedimientos sobre las visitas del episodio, con el precio congelado."""
        if not catalog:
            return []
        if PerformedProcedure.objects.filter(visit__episode=episode).exists():
            return []

        visits = list(episode.visits.order_by('occurred_at'))
        if not visits:
            return []

        created = 0
        for position, case in enumerate(PROCEDURE_CASES[index % len(PROCEDURE_CASES)]):
            # El catálogo se recorre en rueda: con una sola entrada activa salen
            # igualmente los tres procedimientos, que es el caso real de repetir
            # el mismo servicio en visitas distintas.
            service = catalog[position % len(catalog)]
            visit = visits[position % len(visits)]
            # `frozen_price` se deja en `None` para que lo copie del catálogo:
            # es el camino normal, y es justo lo que hay que poder ver en la
            # ficha cuando el precio del servicio cambie después.
            procedure = PerformedProcedure(
                visit=visit,
                service=service,
                laterality=case['laterality'],
                affected_zone=case['zone'],
                performed_at=visit.occurred_at,
                created_by=professional,
            )
            if case['price'] is not None:
                procedure.frozen_price = case['price']
            procedure.save()
            created += 1
            totals['procedimientos'] += 1

        return [f'{created} procedimiento(s)'] if created else []

    def _seed_consent(self, patient, episode, version, totals):
        """Un consentimiento firmado sobre la versión vigente."""
        if version is None or not self.files_enabled:
            return []
        if SignedConsent.objects.filter(patient=patient).exists():
            return []

        # `sign()` y no un `create()`: es lo que congela el texto sin depender de
        # que quien escribe la vista se acuerde de copiarlo.
        SignedConsent.sign(
            version=version,
            patient=patient,
            episode=episode,
            signature_image=_signature_png(),
            signed_at=episode.opened_at,
        )
        totals['consentimientos'] += 1
        return [f'consentimiento «{version.template.name}» v{version.number} firmado']

    def _ensure_consent(self, clinic):
        """Plantilla de consentimiento con v1 y v2 publicadas. Devuelve la vigente.

        Se reutiliza si ya existe: republicar versiones en cada ejecución
        llenaría el histórico de ruido, y una versión publicada es inmutable.
        """
        template = ConsentTemplate.objects.filter(
            clinic=clinic, name=CONSENT_NAME
        ).first()
        if template is not None and template.current_version is not None:
            self.stdout.write(f'  Consentimiento «{CONSENT_NAME}» ya existe: se reutiliza.')
            return template.current_version

        if self.dry_run:
            self.stdout.write(
                f'  Crearía el consentimiento «{CONSENT_NAME}» con dos versiones publicadas.'
            )
            return None

        with transaction.atomic():
            template = template or ConsentTemplate.objects.create(
                clinic=clinic, name=CONSENT_NAME, specialty='cirugía ungueal',
            )
            v1 = template.new_draft_version(text=CONSENT_V1_TEXT)
            v1.publish()
            # La v2 se publica encima: las firmas de la v1 siguen probando el
            # texto de la v1, que es exactamente lo que hay que poder enseñar.
            v2 = template.new_draft_version(text=CONSENT_V2_TEXT)
            v2.publish()

        self.stdout.write(
            f'  Consentimiento «{CONSENT_NAME}»: v1 y v2 (vigente) publicadas.'
        )
        return v2
