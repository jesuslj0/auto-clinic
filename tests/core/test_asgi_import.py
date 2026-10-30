"""`config.asgi` debe poder importarse SIN que Django esté ya inicializado.

`daphne config.asgi:application` importa el módulo en frío: `config/asgi.py` trae los
routings (y los consumidores) antes de llamar a `get_asgi_application()`, que es lo
que carga las apps. Cualquier import de modelos a nivel de módulo en esa cadena
revienta con `AppRegistryNotReady`.

pytest-django inicializa Django antes de importar nada, así que este fallo no se ve
en el resto de la suite (pasó a producción una vez): por eso se prueba en un proceso
aparte.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_asgi_module_imports_in_a_cold_process():
    env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings.test'}
    result = subprocess.run(
        [sys.executable, '-c', 'import config.asgi; print("ok")'],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert 'ok' in result.stdout
