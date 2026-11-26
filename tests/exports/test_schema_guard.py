"""El esquema de exportación no puede quedarse atrás de los modelos.

Si alguien añade un campo a un modelo exportado, estos tests fallan hasta que se
decida en `exports/schema.py` si sale o no (y, si no, por qué). Es lo que impide
que una exportación «completa» deje de serlo en silencio.
"""
import pytest

from exports.schema import CLINIC, ENTITIES, PATIENT, Scope, entities_for


@pytest.mark.parametrize('entity', ENTITIES, ids=lambda e: e.name)
def test_every_model_field_is_either_exported_or_explicitly_excluded(entity):
    concrete = entity.concrete_attnames()
    accounted = entity.accounted_for()

    assert concrete - accounted == set(), (
        f'{entity.model.__name__}: campos sin decidir {sorted(concrete - accounted)}. '
        f'Añádelos a `fields`/`clinic_only` o a `excluded` con su motivo.'
    )
    assert accounted - concrete == set(), (
        f'{entity.model.__name__}: campos que no existen {sorted(accounted - concrete)}'
    )


@pytest.mark.parametrize('entity', ENTITIES, ids=lambda e: e.name)
def test_no_field_is_listed_twice(entity):
    listed = list(entity.fields) + list(entity.clinic_only) + list(entity.excluded)
    assert len(listed) == len(set(listed))


def test_entity_names_are_unique():
    names = [entity.name for entity in ENTITIES]
    assert len(names) == len(set(names))


@pytest.mark.parametrize('kind', [PATIENT, CLINIC])
def test_every_query_runs_for_both_scopes(db, clinic_a, patient_a, kind):
    """Un campo mal escrito o un filtro roto revienta aquí y no en producción."""
    scope = Scope(clinic=clinic_a, patient=patient_a if kind == PATIENT else None)
    for entity in entities_for(scope):
        queryset = entity.queryset(scope).order_by(entity.pk_field).values(*entity.fields_for(scope))
        list(queryset[:1])


def test_the_file_fields_point_at_real_file_fields():
    for entity in ENTITIES:
        if entity.file is None:
            continue
        field = entity.model._meta.get_field(entity.file.field)
        assert field.get_internal_type() in {'FileField', 'ImageField'}
        assert entity.file.field in entity.fields + entity.clinic_only


def test_secrets_are_never_in_the_exported_fields():
    secret_names = {
        'password', 'agent_api_key', 'api_key', 'whatsapp_token', 'whatsapp_verify_token',
        'calendly_token', 'confirmation_token', 'token_hash', 'secret',
    }
    for entity in ENTITIES:
        exported = set(entity.fields) | set(entity.clinic_only)
        assert exported & secret_names == set(), entity.name
