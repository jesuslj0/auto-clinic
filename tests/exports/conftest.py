import pytest

from tests.exports.factory import build_file


@pytest.fixture
def file_a(db, clinic_a, admin_user, patient_a, service_a):
    return build_file(clinic_a, admin_user, patient_a, service_a, 'ALFA')


@pytest.fixture
def file_b(db, clinic_b, admin_user_b, patient_b, service_b):
    return build_file(clinic_b, admin_user_b, patient_b, service_b, 'BETA')
