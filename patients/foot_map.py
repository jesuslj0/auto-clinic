"""Geometría del mapa del pie: qué dibujo se ve en cada combinación y dónde.

Este módulo es puro (no toca la base de datos) y es la **única fuente** de tres
decisiones que antes no existían y que la plantilla y el JavaScript leen de aquí:

1. **El tamaño del dibujo** (`WIDTH` × `HEIGHT`, las unidades del `viewBox`).
2. **Qué combinaciones se pintan en espejo** (`MIRRORED`).
3. **Dónde va cada rótulo** de zona en cada combinación (`scene_labels`).

Dibujos canónicos y espejo
--------------------------
Hay cuatro dibujos, uno por vista, y cada uno está hecho para **un** pie:

======== ============================ ==========================
Vista    Dibujo canónico              Qué se ve a la izquierda
======== ============================ ==========================
dorsal   pie derecho, desde arriba    el hallux (lado medial)
plantar  pie izquierdo, desde abajo   el hallux (lado medial)
medial   pie derecho, lado interno    los dedos
lateral  pie izquierdo, lado externo  los dedos
======== ============================ ==========================

El otro pie de cada vista es exactamente su imagen especular: el izquierdo visto
desde arriba es el derecho visto desde arriba reflejado, y el lado interno del
izquierdo se mira desde el lado contrario que el del derecho, así que los dedos
apuntan al otro lado. Por eso basta un espejo horizontal y no hacen falta ocho
dibujos. Lo que **no** se refleja nunca son los textos: los rótulos se calculan
aquí ya colocados para cada combinación y se pintan fuera del grupo reflejado.

Cómo se reparten (observador mirando la vista, dedos arriba en dorsal/plantar):

- Dorsal: el pie derecho tiene el hallux a la izquierda; el izquierdo, a la
  derecha.
- Plantar: al revés que en dorsal, porque se mira desde abajo.
- Medial: el lado interno del pie derecho se mira desde la izquierda del
  paciente, y los dedos quedan a la izquierda del observador; en el izquierdo,
  a la derecha.
- Lateral: al revés que en medial.

Las coordenadas guardadas son del dibujo canónico
-------------------------------------------------
`Lesion.x`/`Lesion.y` son fracciones del dibujo **sin reflejar**. Así un punto
significa lo mismo anatómicamente sea cual sea el pie: «x pequeña» en dorsal es
siempre el lado del hallux. El clic se desrefleja al capturarlo (`1 - x` en las
combinaciones de `MIRRORED`) y el marcador se pinta dentro del mismo grupo
reflejado que el dibujo, así que el atributo `cx` sigue siendo `x · WIDTH`.

El dibujo provisional anterior pintaba las ocho combinaciones en esta misma
orientación canónica. Por eso las lesiones ya registradas no necesitan migración:
quien marcó el hallux en el plantar derecho antiguo lo marcó a la izquierda, y
ahora ese punto se refleja sobre el hallux del dibujo correcto.
"""
from dataclasses import dataclass

#: Unidades del `viewBox`. Cambiarlas aquí cambia el mapa entero.
WIDTH = 400
HEIGHT = 520

#: Transformación SVG que refleja en horizontal dentro del `viewBox`.
MIRROR_TRANSFORM = f'matrix(-1 0 0 1 {WIDTH} 0)'

VIEWS = ('dorsal', 'plantar', 'medial', 'lateral')
LATERALITIES = ('left', 'right')

#: (vista, pie) que se pintan reflejando el dibujo canónico de su vista.
MIRRORED = frozenset({
    ('dorsal', 'left'),
    ('plantar', 'right'),
    ('medial', 'left'),
    ('lateral', 'right'),
})

#: Cómo se nombra la vista dentro del dibujo: qué cara del pie se está viendo.
VIEW_CAPTIONS = {
    'dorsal': 'dorsal (empeine)',
    'plantar': 'plantar (planta)',
    'medial': 'medial (lado interno)',
    'lateral': 'lateral (lado externo)',
}
LATERALITY_CAPTIONS = {'left': 'Pie izquierdo', 'right': 'Pie derecho'}


def is_mirrored(view, laterality):
    """¿Se pinta esta combinación reflejando el dibujo canónico de su vista?"""
    return (view, laterality) in MIRRORED


def mirror_transform(view, laterality):
    """El atributo `transform` de la combinación, o cadena vacía si no refleja."""
    return MIRROR_TRANSFORM if is_mirrored(view, laterality) else ''


def mirror_table():
    """`{vista: {pie: bool}}`, para que el navegador desrefleje el clic.

    El JavaScript no repite la regla: la recibe ya resuelta.
    """
    return {
        view: {laterality: is_mirrored(view, laterality) for laterality in LATERALITIES}
        for view in VIEWS
    }


@dataclass(frozen=True)
class Label:
    """Un rótulo de zona en coordenadas del dibujo **canónico**.

    `cx`/`cy` es el punto de la zona; `tx`/`ty`, dónde empieza el texto, y
    `anchor` su `text-anchor` (`start`, `end` o `middle`).
    """

    text: str
    cx: float
    cy: float
    tx: float
    ty: float
    anchor: str


#: Los rótulos de cada vista, sobre su dibujo canónico. Orientan; no son un dato:
#: la zona clínica de cada lesión se elige en el formulario.
LABELS = {
    'dorsal': (
        Label('Hallux', 153, 100, 98, 104, 'end'),
        Label('5.º dedo', 272, 120, 318, 114, 'start'),
        Label('1.er meta.', 146, 196, 98, 200, 'end'),
        Label('Mediopié', 206, 316, 98, 320, 'end'),
        Label('Tobillo', 206, 452, 318, 456, 'start'),
    ),
    'plantar': (
        Label('Hallux', 153, 74, 98, 78, 'end'),
        Label('1.er meta.', 146, 196, 98, 200, 'end'),
        Label('5.º meta.', 270, 210, 318, 214, 'start'),
        Label('Arco medial', 158, 330, 98, 334, 'end'),
        Label('Borde lateral', 268, 352, 302, 356, 'start'),
        Label('Talón', 206, 462, 302, 466, 'start'),
    ),
    'medial': (
        Label('Hallux', 62, 410, 60, 352, 'middle'),
        Label('1.er meta.', 128, 412, 120, 474, 'middle'),
        Label('Arco medial', 212, 412, 212, 474, 'middle'),
        Label('Talón', 346, 404, 330, 474, 'middle'),
        Label('Tobillo', 308, 266, 362, 230, 'start'),
    ),
    'lateral': (
        Label('5.º dedo', 82, 424, 60, 352, 'middle'),
        Label('5.º meta.', 140, 420, 120, 474, 'middle'),
        Label('Borde lateral', 226, 424, 222, 474, 'middle'),
        Label('Talón', 346, 410, 330, 474, 'middle'),
        Label('Tobillo', 320, 288, 362, 252, 'start'),
    ),
}

#: Hacia qué lado queda la cara medial en el dibujo canónico de dorsal/plantar
#: (los rótulos de orientación «Medial»/«Lateral» al pie del dibujo).
_SIDE_TAGS = {
    'dorsal': (('Medial', 16, 'start'), ('Lateral', WIDTH - 16, 'end')),
    'plantar': (('Medial', 16, 'start'), ('Lateral', WIDTH - 16, 'end')),
    'medial': (('Dedos', 16, 'start'), ('Talón', WIDTH - 16, 'end')),
    'lateral': (('Dedos', 16, 'start'), ('Talón', WIDTH - 16, 'end')),
}

_SWAPPED_ANCHOR = {'start': 'end', 'end': 'start', 'middle': 'middle'}


def _leader_end(label):
    """Dónde acaba la línea guía que une el punto con su texto."""
    if label.anchor == 'start':
        return label.tx - 5, label.ty - 4
    if label.anchor == 'end':
        return label.tx + 5, label.ty - 4
    # Texto centrado encima o debajo del punto.
    if label.ty < label.cy:
        return label.tx, label.ty + 5
    return label.tx, label.ty - 14


def scene_labels(view, laterality):
    """Los rótulos de una combinación, ya colocados para pintarse sin reflejar.

    En una combinación reflejada se refleja la posición (`x → WIDTH - x`) y se
    intercambia el anclaje, pero el texto se escribe derecho.
    """
    mirrored = is_mirrored(view, laterality)

    def flip(x):
        return WIDTH - x if mirrored else x

    labels = []
    for label in LABELS[view]:
        lx, ly = _leader_end(label)
        labels.append({
            'text': label.text,
            'cx': flip(label.cx),
            'cy': label.cy,
            'tx': flip(label.tx),
            'ty': label.ty,
            'lx': flip(lx),
            'ly': ly,
            'anchor': _SWAPPED_ANCHOR[label.anchor] if mirrored else label.anchor,
        })
    return labels


def scene_side_tags(view, laterality):
    """Los dos rótulos de orientación del pie del dibujo, ya colocados."""
    mirrored = is_mirrored(view, laterality)
    tags = []
    for text, x, anchor in _SIDE_TAGS[view]:
        tags.append({
            'text': text,
            'x': WIDTH - x if mirrored else x,
            'anchor': _SWAPPED_ANCHOR[anchor] if mirrored else anchor,
        })
    return tags


def build_map_scenes():
    """Las ocho combinaciones con todo lo que el dibujo necesita para pintarse."""
    return [
        {
            'view': view,
            'laterality': laterality,
            'mirrored': is_mirrored(view, laterality),
            'caption': f'{LATERALITY_CAPTIONS[laterality]} · vista {VIEW_CAPTIONS[view]}',
            'labels': scene_labels(view, laterality),
            'side_tags': scene_side_tags(view, laterality),
        }
        for view in VIEWS
        for laterality in LATERALITIES
    ]


def canonical_x(x, view, laterality):
    """Pasa una x de pantalla (fracción) al dibujo canónico. Es su propia inversa."""
    return 1 - x if is_mirrored(view, laterality) else x
