"""Hash de firma de una nota clínica.

Al firmar, se calcula un SHA-256 sobre un JSON canónico del contenido SOAP más
los metadatos de firma. El hash ata *qué* se firmó a *quién* lo firmó y *cuándo*:
si más adelante alguien lograra alterar el contenido por debajo del ORM y del
trigger, el hash dejaría de cuadrar y la manipulación sería detectable.

El JSON es canónico (`sort_keys`, separadores fijos, `ensure_ascii=False`) para
que el mismo contenido produzca siempre el mismo digest, reproducible desde
fuera del sistema.
"""
import hashlib
import json

ALGORITHM = 'sha256'


def _canonical_payload(note) -> str:
    """JSON canónico del contenido firmado.

    Incluye los cuatro campos SOAP y los metadatos de firma. `signed_at` se
    serializa en ISO-8601; `note_id` y `signed_by_id` atan el hash a la
    identidad concreta de la nota y del firmante.
    """
    payload = {
        'subjective': note.subjective,
        'objective': note.objective,
        'assessment': note.assessment,
        'plan': note.plan,
        'note_id': note.pk,
        'signed_by_id': note.signed_by_id,
        'signed_at': note.signed_at.isoformat() if note.signed_at else None,
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def compute_note_hash(note) -> str:
    """`"sha256:<hexdigest>"` del contenido y los metadatos de firma de la nota."""
    digest = hashlib.sha256(_canonical_payload(note).encode('utf-8')).hexdigest()
    return f'{ALGORITHM}:{digest}'


def verify_note_hash(note) -> bool:
    """`True` si el `content_hash` guardado cuadra con el contenido actual.

    Una nota sin firmar (sin hash) devuelve `False`: no hay integridad que
    verificar todavía.
    """
    if not note.content_hash:
        return False
    return note.content_hash == compute_note_hash(note)
