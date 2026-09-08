"""El sidebar del panel: fijo, colapsable y con el logo solo.

Es maquetación, así que lo que se puede defender con un test es el **contrato**
entre la plantilla y el CSS que la gobierna (`partials/_head_sidebar.html`), no su
aspecto. Y ese contrato se rompe en silencio: si alguien renombra un gancho o se
deja una etiqueta sin `sidebar-label`, nada falla — el menú simplemente se ve mal
al colapsarlo.

Cuatro cosas:

1. El panel de escritorio es **fijo al viewport** y el contenido le deja hueco con
   su `padding`, no ocupando una columna del flujo.
2. Los ganchos del colapso están puestos: el panel, el hueco, el tirador y una
   etiqueta por cada enlace del menú.
3. En la cabecera, logo **y nombre** con el panel abierto; colapsado, solo el
   logo, porque el nombre lleva el mismo gancho que las demás etiquetas.
4. Sin sesión no hay hueco reservado, porque no hay menú.
"""
import pytest
from django.urls import reverse


@pytest.fixture
def panel(client, admin_user):
    """El HTML del panel con sesión iniciada (cualquier página sirve: es base.html)."""
    client.force_login(admin_user)
    return client.get(reverse('patients:list')).content.decode()


@pytest.mark.django_db
class TestSidebarFijo:
    def test_el_panel_de_escritorio_es_fijo_al_viewport(self, panel):
        """Fijo y a toda la altura: el menú no se va con el scroll de la página."""
        assert 'lg:fixed lg:inset-y-0 lg:left-0' in panel

    def test_el_contenido_reserva_el_hueco_con_padding(self, panel):
        """Un elemento fijo no ocupa sitio: el hueco lo abre el `pl-*` del shell."""
        assert 'sidebar-shell lg:pl-72' in panel

    def test_el_menu_tiene_su_propio_scroll(self, panel):
        """Lo único que puede desbordar es la lista de secciones."""
        assert 'sidebar-pad flex-1 space-y-1 overflow-y-auto' in panel


@pytest.mark.django_db
class TestColapso:
    def test_estan_los_ganchos_que_gobierna_el_css(self, panel):
        for gancho in ('sidebar-desktop', 'sidebar-shell', 'sidebar-pad', 'sidebar-brand'):
            assert gancho in panel, gancho

    def test_hay_un_tirador_para_colapsar(self, panel):
        assert 'data-sidebar-toggle' in panel

    def test_el_icono_del_tirador_son_dos_y_no_uno_girado(self, panel):
        """El dibujo es el panel, con su columna a la izquierda: girarlo lo
        mandaría al lado contrario, así que se intercambian los dos."""
        assert 'sidebar-icon-collapse' in panel
        assert 'sidebar-icon-expand' in panel
        assert '.sidebar-collapsed .sidebar-icon-expand { @apply block; }' in panel

    def test_cada_enlace_del_menu_lleva_su_etiqueta_marcada(self, panel):
        """Sin `sidebar-label`, ese texto no se recoge al colapsar el panel."""
        import re

        enlaces = re.findall(r'<a [^>]*class="sidebar-link.*?</a>', panel, re.S)

        # Los dos sidebars (escritorio y móvil) comparten el mismo parcial.
        assert len(enlaces) >= 11
        assert all('class="sidebar-label' in enlace for enlace in enlaces)

    def test_las_etiquetas_no_se_borran_del_documento(self, panel):
        """Se ocultan con `sr-only`: un icono sin nombre accesible no es un enlace.

        La regla vive en el `<style>` del head, y es la que hace que el texto siga
        estando ahí para un lector de pantalla.
        """
        assert '.sidebar-collapsed .sidebar-label { @apply sr-only; }' in panel

    def test_el_estado_se_recuerda_entre_paginas(self, panel):
        """El panel navega con recargas: sin persistir, el colapso no duraría nada."""
        assert "'ac-sidebar'" in panel
        assert 'window.acSidebar' in panel

    def test_el_texto_de_cada_enlace_esta_tambien_como_title(self, panel):
        """Colapsado, el icono a secas no dice qué es a quien usa el ratón."""
        for etiqueta in ('title="Panel"', 'title="Pacientes"', 'title="Agenda"'):
            assert etiqueta in panel


@pytest.mark.django_db
class TestCabeceraDelPanel:
    def test_con_el_panel_abierto_se_ve_el_nombre_de_la_clinica(self, panel, clinic_a):
        assert clinic_a.name in panel
        assert '<h1' in panel

    def test_y_se_recoge_al_colapsar_junto_al_resto_de_etiquetas(self, panel):
        """Colapsado queda solo el logo, y el nombre sigue ahí para quien lo lea."""
        import re

        brand = re.search(r'<a href="/" class="sidebar-brand.*?</a>', panel, re.S)

        assert brand is not None
        assert 'class="sidebar-label' in brand.group(0)

    def test_el_enlace_dice_de_quien_es_el_logo(self, panel):
        """Colapsado, el logo es lo único que queda: su nombre accesible no puede faltar."""
        assert 'aria-label="Inicio · ' in panel


@pytest.mark.django_db
def test_sin_sesion_no_se_reserva_hueco_de_sidebar(client):
    """No hay menú que colapsar, así que tampoco `padding` que heredar.

    Se busca la clase **aplicada al contenido** y no la cadena a secas: las reglas
    del colapso se sirven en toda página, login incluido.
    """
    html = client.get(reverse('core:login')).content.decode()

    assert 'sidebar-shell lg:pl-72' not in html
    assert 'data-sidebar-toggle' not in html
