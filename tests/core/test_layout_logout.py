"""Cerrar sesión: se confirma antes, y sigue saliendo por POST.

Como el resto de la maquetación, lo que se puede defender aquí es el **contrato**,
no el aspecto. Y hay uno que importa más que el modal: cerrar sesión cambia estado
del servidor, así que tiene que seguir siendo un formulario POST con su token. Si
alguien "simplifica" el botón a un enlace para que abra antes el diálogo, el test
lo canta — un logout por GET lo dispara cualquier cosa que precargue enlaces.

Lo demás es el reparto: dos botones (escritorio y móvil), un solo formulario, y un
diálogo que se anuncia como tal.
"""
import re

import pytest
from django.urls import reverse


@pytest.fixture
def panel(client, admin_user):
    """El HTML del panel con sesión iniciada (cualquier página sirve: es base.html)."""
    client.force_login(admin_user)
    return client.get(reverse('patients:list')).content.decode()


@pytest.mark.django_db
class TestElLogoutSigueSiendoUnPost:
    def test_hay_un_formulario_post_al_logout(self, panel):
        formulario = re.search(r'<form id="logout-form".*?</form>', panel, re.S)

        assert formulario is not None
        assert 'method="post"' in formulario.group(0)
        assert f'action="{reverse("core:logout")}"' in formulario.group(0)

    def test_y_viaja_firmado(self, panel):
        """Sin el token, el POST se queda en un 403 y la sesión no se cierra."""
        formulario = re.search(r'<form id="logout-form".*?</form>', panel, re.S)

        assert 'csrfmiddlewaretoken' in formulario.group(0)

    def test_no_hay_ningun_otro_camino_al_logout(self, panel):
        """Un solo formulario para los dos paneles: el POST se escribe una vez."""
        assert panel.count(reverse('core:logout')) == 1


@pytest.mark.django_db
class TestLaConfirmacion:
    def test_los_dos_paneles_abren_el_modal_en_vez_de_salir(self, panel):
        """El de escritorio y el del móvil; el móvil además se cierra antes."""
        assert panel.count('logoutOpen = true') == 2
        assert 'sidebarOpen = false; logoutOpen = true' in panel

    def test_el_modal_se_anuncia_como_dialogo_y_dice_su_nombre(self, panel):
        modal = re.search(r'<div\s+x-cloak\s+x-show="logoutOpen".*?</div>\s*</div>', panel, re.S)

        assert modal is not None
        assert 'role="dialog"' in modal.group(0)
        assert 'aria-modal="true"' in modal.group(0)
        assert 'aria-labelledby="logout-modal-title"' in modal.group(0)
        assert 'id="logout-modal-title"' in modal.group(0)

    def test_se_puede_salir_del_modal_sin_cerrar_sesion(self, panel):
        """Con Escape, con el fondo y con el botón: un diálogo sin salida es una trampa."""
        assert 'sidebarOpen = false; logoutOpen = false' in panel  # Escape, en el <body>
        assert panel.count('logoutOpen = false') >= 3              # + fondo + «Volver»


@pytest.mark.django_db
def test_sin_sesion_no_hay_ni_formulario_ni_modal(client):
    """No hay sesión que cerrar, y el formulario llevaría a un logout de nadie."""
    html = client.get(reverse('core:login')).content.decode()

    assert 'id="logout-form"' not in html
    assert 'logoutOpen = true' not in html
