"""Construye el ZIP de una exportación.

Recibe un `Scope` (un paciente, o la clínica) y un fichero binario donde escribir;
no sabe nada de Celery, de vistas ni de notificaciones. Eso es lo que permite
llamarlo igual desde la tarea y desde un comando de gestión.

Pensado para no comerse la memoria de una máquina compartida: las filas se leen
con `.iterator()`, los adjuntos se copian en bloques de 64 KB y todo se escribe
directo al ZIP en disco. Solo el manifest y los textos pequeños viven en memoria.
"""
from __future__ import annotations

import hashlib
import json
import logging
import posixpath
import re
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time
from datetime import timezone as dt_timezone
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO, Callable

from django.utils import timezone

from exports.readme import render_readme
from exports.schema import (
    CLINIC,
    EXCLUDED_MODELS,
    SCHEMA_VERSION,
    Entity,
    Scope,
    entities_for,
)

logger = logging.getLogger(__name__)

CHUNK_BYTES = 64 * 1024
CHUNK_ROWS = 500

VERIFY_SCRIPT = Path(__file__).parent / 'assets' / 'verificar_firmas.py'


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f'{type(value).__name__} no se puede serializar')


def dump_line(row: dict) -> bytes:
    """Una fila como línea de JSON Lines, UTF-8 y sin escapar los acentos."""
    text = json.dumps(row, ensure_ascii=False, separators=(',', ':'), default=_json_default)
    return text.encode('utf-8') + b'\n'


class _Tee:
    """Escribe en el miembro del ZIP y a la vez cuenta bytes y calcula el SHA-256."""

    def __init__(self, raw):
        self.raw = raw
        self.sha = hashlib.sha256()
        self.bytes = 0

    def write(self, data: bytes):
        self.raw.write(data)
        self.sha.update(data)
        self.bytes += len(data)

    @property
    def hexdigest(self) -> str:
        return self.sha.hexdigest()


class ZipSink:
    def __init__(self, fileobj: BinaryIO, now: datetime):
        self.zf = zipfile.ZipFile(fileobj, 'w', zipfile.ZIP_DEFLATED, allowZip64=True)
        stamp = now.astimezone(dt_timezone.utc)
        self._date_time = (stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute, stamp.second)

    @contextmanager
    def member(self, path: str, *, compress: bool = True):
        info = zipfile.ZipInfo(path, date_time=self._date_time)
        info.compress_type = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        info.external_attr = 0o644 << 16
        with self.zf.open(info, 'w', force_zip64=True) as raw:
            yield _Tee(raw)

    def write_bytes(self, path: str, data: bytes) -> dict:
        with self.member(path) as out:
            out.write(data)
        return {'path': path, 'bytes': out.bytes, 'sha256': out.hexdigest}

    def close(self):
        self.zf.close()


@dataclass
class BuildResult:
    counts: dict[str, int] = field(default_factory=dict)
    files: dict[str, int] = field(default_factory=lambda: {
        'total': 0, 'ok': 0, 'missing': 0, 'unreadable': 0, 'checksum_mismatch': 0,
    })

    @property
    def total_rows(self) -> int:
        return sum(self.counts.values())

    @property
    def has_warnings(self) -> bool:
        return any(self.files[key] for key in ('missing', 'unreadable', 'checksum_mismatch'))


_UNSAFE = re.compile(r'[^A-Za-z0-9._-]')


def _safe_segment(value, *, fallback='x') -> str:
    """Un trozo de ruta del ZIP sin separadores ni `..`.

    El identificador de la clínica es texto libre; sin esto, un `clinic_id` con `/` o
    `..` pondría el adjunto fuera de su carpeta al descomprimir.
    """
    cleaned = _UNSAFE.sub('_', str(value)).strip('.')
    return cleaned or fallback


def _copy_attachment(sink: ZipSink, entity: Entity, row: dict, listing, result: BuildResult) -> dict:
    """Copia el fichero de una fila al ZIP y devuelve lo que se añade a su fila."""
    spec = entity.file
    key = row.get(spec.field)
    if not key:
        return {'zip_path': None, 'file_status': None}

    row_id = row[entity.pk_field]
    extension = posixpath.splitext(key)[1].lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,6}', extension):
        extension = ''
    path = f'adjuntos/{spec.folder}/{_safe_segment(row_id)}{extension}'
    storage = entity.model._meta.get_field(spec.field).storage
    record = {'entity': entity.name, 'row_id': str(row_id), 'path': path, 'key': key}
    result.files['total'] += 1

    try:
        source = storage.open(key, 'rb')
    except FileNotFoundError:
        status = 'missing'
    except Exception:
        # No es lo mismo un objeto que no está que un bucket que no contesta: el
        # primero es un dato perdido, el segundo se arregla reintentando.
        logger.exception('No se pudo abrir %s de %s #%s', spec.field, entity.name, row_id)
        status = 'unreadable'
    else:
        with source:
            with sink.member(path, compress=False) as out:
                for chunk in iter(lambda: source.read(CHUNK_BYTES), b''):
                    out.write(chunk)
        actual = f'sha256:{out.hexdigest}'
        record.update(bytes=out.bytes, sha256=actual)
        stored = row.get(spec.checksum_field) if spec.checksum_field else None
        status = 'ok'
        if stored:
            record['stored_checksum'] = stored
            if stored != actual:
                status = 'checksum_mismatch'

    record['status'] = status
    result.files[status] += 1
    listing.write(dump_line(record))
    return {'zip_path': path if status in ('ok', 'checksum_mismatch') else None, 'file_status': status}


def _write_entity(sink: ZipSink, entity: Entity, scope: Scope, listing, result: BuildResult) -> dict:
    fields = entity.fields_for(scope)
    rows = (
        entity.queryset(scope).order_by(entity.pk_field).values(*fields).iterator(chunk_size=CHUNK_ROWS)
    )
    has_files = entity.file is not None and entity.file.field in fields
    path = f'datos/{entity.name}.jsonl'
    count = 0

    if not has_files:
        with sink.member(path) as out:
            for row in rows:
                out.write(dump_line(row))
                count += 1
    else:
        # Un ZIP no admite dos miembros abiertos a la vez: mientras se copian los
        # adjuntos de cada fila, sus líneas esperan en un temporal.
        with tempfile.TemporaryFile() as pending:
            for row in rows:
                row.update(_copy_attachment(sink, entity, row, listing, result))
                pending.write(dump_line(row))
                count += 1
            pending.seek(0)
            with sink.member(path) as out:
                for chunk in iter(lambda: pending.read(CHUNK_BYTES), b''):
                    out.write(chunk)

    result.counts[entity.name] = count
    return {'path': path, 'rows': count, 'bytes': out.bytes, 'sha256': out.hexdigest}


def _foot_map_info() -> dict:
    from patients import foot_map

    return {
        'width': foot_map.WIDTH,
        'height': foot_map.HEIGHT,
        'views': list(foot_map.VIEWS),
        'lateralities': list(foot_map.LATERALITIES),
        'mirrored': sorted([view, side] for view, side in foot_map.MIRRORED),
        'note': 'x e y son fracciones (0-1) del dibujo canónico de cada vista; el otro pie es su imagen especular.',
    }


def build_export_zip(
    scope: Scope,
    dest: BinaryIO,
    *,
    now: datetime | None = None,
    pdf_renderer: Callable[[Scope], bytes] | None = None,
    extra_entities: list | None = None,
) -> BuildResult:
    """Escribe en `dest` el ZIP del ámbito pedido y devuelve lo que contiene.

    `pdf_renderer`, si se pasa, produce `paciente.pdf`. Si falla, la excepción
    sube: un ZIP de paciente sin su copia legible no se entrega.
    `extra_entities` son entidades añadidas a las del esquema (el registro de
    auditoría, que se calcula aparte).
    """
    now = now or timezone.now()
    entities = entities_for(scope) + list(extra_entities or [])
    result = BuildResult()
    sink = ZipSink(dest, now)
    datos: dict[str, dict] = {}
    otros: dict[str, dict] = {}

    try:
        with tempfile.TemporaryFile() as listing:
            for entity in entities:
                datos[entity.name] = _write_entity(sink, entity, scope, listing, result)

            listing.seek(0)
            with sink.member('manifest_ficheros.jsonl') as out:
                for chunk in iter(lambda: listing.read(CHUNK_BYTES), b''):
                    out.write(chunk)
            ficheros = {'path': 'manifest_ficheros.jsonl', 'bytes': out.bytes, 'sha256': out.hexdigest,
                        **result.files}

        generated_at = now.astimezone(dt_timezone.utc).isoformat()
        readme = render_readme(scope, entities, result.counts, generated_at)
        info = sink.write_bytes('LEEME.txt', readme.encode('utf-8'))
        otros['LEEME.txt'] = info
        info = sink.write_bytes('verificar_firmas.py', VERIFY_SCRIPT.read_bytes())
        otros['verificar_firmas.py'] = info
        if scope.kind != CLINIC and pdf_renderer is not None:
            info = sink.write_bytes('paciente.pdf', pdf_renderer(scope))
            otros['paciente.pdf'] = info

        manifest = {
            'schema_version': SCHEMA_VERSION,
            'generator': 'auto-clinic/exports',
            'generated_at': generated_at,
            'scope': scope.kind,
            'clinic': {'id': scope.clinic.pk, 'name': scope.clinic.name},
            'patient': (
                {'id': scope.patient.pk, 'name': f'{scope.patient.first_name} {scope.patient.last_name}'}
                if scope.patient is not None else None
            ),
            'note_seal': {
                'algorithm': 'sha256',
                'description': 'content_hash = sha256 del JSON canónico de la nota (ver verificar_firmas.py)',
            },
            'datos': datos,
            'ficheros': ficheros,
            'otros': otros,
            'excluded_models': EXCLUDED_MODELS,
            'excluded_fields': {
                entity.model.__name__: entity.excluded for entity in entities if entity.excluded
            },
        }
        if 'lesions' in result.counts:
            manifest['foot_map'] = _foot_map_info()
        sink.write_bytes('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
    finally:
        sink.close()

    return result
