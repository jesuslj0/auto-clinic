import json
import tempfile
import zipfile

from exports.builder import build_export_zip
from exports.schema import Scope


def build(scope: Scope, **kwargs):
    """Genera el ZIP del ámbito y lo devuelve abierto, junto al resultado."""
    tmp = tempfile.TemporaryFile()
    result = build_export_zip(scope, tmp, **kwargs)
    tmp.seek(0)
    return zipfile.ZipFile(tmp), result


def rows(archive: zipfile.ZipFile, name: str) -> list[dict]:
    with archive.open(f'datos/{name}.jsonl') as handle:
        return [json.loads(line) for line in handle.read().decode('utf-8').splitlines() if line]


def manifest(archive: zipfile.ZipFile) -> dict:
    return json.loads(archive.read('manifest.json'))
