#!/usr/bin/env python3
"""Comprueba una exportación de Auto Clinic sin necesitar la aplicación.

Solo usa la biblioteca estándar de Python 3.8 o posterior. Uso:

    python verificar_firmas.py [carpeta]

`carpeta` es donde se descomprimió el ZIP (por defecto, la carpeta de este
fichero). Comprueba tres cosas:

1. Que cada fichero de `datos/` coincide con el sha256 del `manifest.json`.
2. Que cada fichero de `adjuntos/` coincide con el sha256 de
   `manifest_ficheros.jsonl` (y con el que la aplicación guardó al subirlo).
3. Que el sello (`content_hash`) de cada nota clínica firmada sigue cuadrando
   con su contenido. Es el mismo cálculo que hace la aplicación: SHA-256 de un
   JSON canónico del contenido de la nota más quién la firmó y cuándo. Si alguien
   hubiera alterado el texto de una nota firmada, el sello dejaría de coincidir.

Termina con código 0 si todo cuadra y con 1 si algo no.
"""
import hashlib
import json
import sys
from pathlib import Path

# Campos que se añadieron a la nota DESPUÉS de las primeras firmas: entran en el
# sello solo si traen texto, para que las notas antiguas sigan verificando.
LATER_FIELDS = (
    'background',
    'inspection',
    'palpation',
    'biomechanics',
    'complementary_tests',
    'treatment_done',
    'follow_up',
)


def canonical_payload(note):
    payload = {
        'subjective': note['subjective'],
        'objective': note['objective'],
        'assessment': note['assessment'],
        'plan': note['plan'],
        'note_id': note['id'],
        'signed_by_id': note['signed_by_id'],
        'signed_at': note['signed_at'],
    }
    for name in LATER_FIELDS:
        value = note.get(name) or ''
        if value:
            payload[name] = value
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def note_hash(note):
    digest = hashlib.sha256(canonical_payload(note).encode('utf-8')).hexdigest()
    return f'sha256:{digest}'


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path):
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def check_manifest_files(root, manifest, problems):
    checked = 0
    entries = list(manifest.get('datos', {}).values())
    entries += [info for info in manifest.get('otros', {}).values()]
    ficheros = manifest.get('ficheros') or {}
    if ficheros.get('path'):
        entries.append(ficheros)
    for info in entries:
        path = root / info['path']
        if not path.exists():
            problems.append(f'Falta el fichero {info["path"]}')
            continue
        checked += 1
        if file_sha256(path) != info['sha256']:
            problems.append(f'El fichero {info["path"]} no coincide con el manifest')
    return checked


def check_attachments(root, manifest, problems):
    ficheros = manifest.get('ficheros') or {}
    listing = root / ficheros.get('path', 'manifest_ficheros.jsonl')
    checked = skipped = 0
    if not listing.exists():
        return checked, skipped
    for record in read_jsonl(listing):
        if record['status'] in ('missing', 'unreadable'):
            skipped += 1
            problems.append(
                f'Adjunto no disponible al exportar ({record["status"]}): {record["entity"]} '
                f'#{record["row_id"]}'
            )
            continue
        path = root / record['path']
        if not path.exists():
            problems.append(f'Falta el adjunto {record["path"]}')
            continue
        checked += 1
        actual = 'sha256:' + file_sha256(path)
        if actual != record['sha256']:
            problems.append(f'El adjunto {record["path"]} no coincide con el manifest')
        elif record.get('stored_checksum') and record['stored_checksum'] != actual:
            problems.append(
                f'El adjunto {record["path"]} no coincide con el checksum que guardó la aplicación'
            )
    return checked, skipped


def check_notes(root, problems):
    path = root / 'datos' / 'clinical_notes.jsonl'
    signed = ok = 0
    if not path.exists():
        return signed, ok
    for note in read_jsonl(path):
        if not note.get('content_hash'):
            continue
        signed += 1
        if note_hash(note) == note['content_hash']:
            ok += 1
        else:
            problems.append(f'El sello de la nota clínica #{note["id"]} NO coincide con su contenido')
    return signed, ok


def main(argv):
    root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent
    manifest_path = root / 'manifest.json'
    if not manifest_path.exists():
        print(f'No se encuentra manifest.json en {root}')
        return 1
    with open(manifest_path, encoding='utf-8') as handle:
        manifest = json.load(handle)

    problems = []
    files_checked = check_manifest_files(root, manifest, problems)
    attachments, unavailable = check_attachments(root, manifest, problems)
    signed, signed_ok = check_notes(root, problems)

    print(f'Ficheros de datos comprobados: {files_checked}')
    print(f'Adjuntos comprobados: {attachments}' + (f' ({unavailable} no disponibles)' if unavailable else ''))
    print(f'Notas clínicas firmadas: {signed} (sello correcto: {signed_ok})')
    if problems:
        print('\nPROBLEMAS:')
        for problem in problems:
            print(f' - {problem}')
        return 1
    print('\nTodo correcto.')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
