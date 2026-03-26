"""
API tests for knowledge app:
  /api/knowledge/entries/   (ClinicKnowledgeBaseViewSet)
  /api/knowledge/queries/   (ClinicInfoQueryViewSet)
  /api/knowledge/cache/     (ClinicInfoCacheViewSet)
"""
import pytest

from knowledge.models import ClinicInfoCache, ClinicInfoQuery, ClinicKnowledgeBase


# ---------------------------------------------------------------------------
# Shared fixtures for this module
# ---------------------------------------------------------------------------

@pytest.fixture
def kb_entry_a(db, clinic_a):
    return ClinicKnowledgeBase.objects.create(
        clinic=clinic_a,
        kb_type="faq",
        title="FAQ Entry A",
        content="Answer A",
    )


@pytest.fixture
def kb_entry_b(db, clinic_b):
    return ClinicKnowledgeBase.objects.create(
        clinic=clinic_b,
        kb_type="services",
        title="Services B",
        content="Details B",
    )


@pytest.fixture
def query_a(db, clinic_a):
    return ClinicInfoQuery.objects.create(
        clinic=clinic_a,
        question="What are your hours?",
        intent_category="schedule",
    )


@pytest.fixture
def query_b(db, clinic_b):
    return ClinicInfoQuery.objects.create(
        clinic=clinic_b,
        question="Where are you located?",
        intent_category="location",
    )


@pytest.fixture
def cache_a(db, clinic_a):
    return ClinicInfoCache.objects.create(
        clinic=clinic_a,
        normalized_question="opening hours",
        answer="9am to 6pm",
    )


@pytest.fixture
def cache_b(db, clinic_b):
    return ClinicInfoCache.objects.create(
        clinic=clinic_b,
        normalized_question="location address",
        answer="123 Main St",
    )


# ---------------------------------------------------------------------------
# ClinicKnowledgeBase
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestKnowledgeBaseViewSetList:
    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/knowledge/entries/")
        assert response.status_code in (401, 403)

    def test_admin_can_list(self, admin_client, kb_entry_a):
        response = admin_client.get("/api/knowledge/entries/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_staff_can_read(self, staff_client, kb_entry_a):
        response = staff_client.get("/api/knowledge/entries/")
        assert response.status_code == 200

    def test_clinic_isolation(self, admin_client, kb_entry_a, kb_entry_b):
        response = admin_client.get("/api/knowledge/entries/")
        ids = [str(e["id"]) for e in response.data["results"]]
        assert str(kb_entry_a.pk) in ids
        assert str(kb_entry_b.pk) not in ids

    def test_superuser_sees_all(self, superuser_client, kb_entry_a, kb_entry_b):
        response = superuser_client.get("/api/knowledge/entries/")
        ids = [str(e["id"]) for e in response.data["results"]]
        assert str(kb_entry_a.pk) in ids
        assert str(kb_entry_b.pk) in ids


@pytest.mark.django_db
class TestKnowledgeBaseViewSetCreate:
    def test_admin_can_create(self, admin_client, clinic_a):
        data = {
            "clinic": clinic_a.pk,
            "kb_type": "pricing",
            "title": "Price List",
            "content": "Consultation: €50",
        }
        response = admin_client.post("/api/knowledge/entries/", data)
        assert response.status_code == 201
        assert response.data["title"] == "Price List"

    def test_staff_cannot_create(self, staff_client, clinic_a):
        data = {
            "clinic": clinic_a.pk,
            "kb_type": "faq",
            "content": "Staff answer",
        }
        response = staff_client.post("/api/knowledge/entries/", data)
        assert response.status_code == 403

    def test_create_requires_fields(self, admin_client):
        response = admin_client.post("/api/knowledge/entries/", {})
        assert response.status_code == 400


@pytest.mark.django_db
class TestKnowledgeBaseViewSetRetrieve:
    def test_retrieve_own_entry(self, admin_client, kb_entry_a):
        response = admin_client.get(f"/api/knowledge/entries/{kb_entry_a.pk}/")
        assert response.status_code == 200

    def test_retrieve_other_clinic_entry_returns_404(self, admin_client, kb_entry_b):
        response = admin_client.get(f"/api/knowledge/entries/{kb_entry_b.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestKnowledgeBaseViewSetUpdate:
    def test_admin_can_update(self, admin_client, kb_entry_a):
        response = admin_client.patch(f"/api/knowledge/entries/{kb_entry_a.pk}/", {"title": "Updated"})
        assert response.status_code == 200
        assert response.data["title"] == "Updated"

    def test_staff_cannot_update(self, staff_client, kb_entry_a):
        response = staff_client.patch(f"/api/knowledge/entries/{kb_entry_a.pk}/", {"title": "Hacked"})
        assert response.status_code == 403


@pytest.mark.django_db
class TestKnowledgeBaseViewSetDelete:
    def test_admin_can_delete(self, admin_client, kb_entry_a):
        pk = kb_entry_a.pk
        response = admin_client.delete(f"/api/knowledge/entries/{kb_entry_a.pk}/")
        assert response.status_code == 204
        assert not ClinicKnowledgeBase.objects.filter(pk=pk).exists()


@pytest.mark.django_db
class TestKnowledgeBaseExport:
    def test_export_returns_list(self, admin_client, kb_entry_a):
        response = admin_client.get("/api/knowledge/entries/export/")
        assert response.status_code == 200
        assert isinstance(response.data, list)

    def test_export_respects_clinic_isolation(self, admin_client, kb_entry_a, kb_entry_b):
        response = admin_client.get("/api/knowledge/entries/export/")
        ids = [str(e["id"]) for e in response.data]
        assert str(kb_entry_a.pk) in ids
        assert str(kb_entry_b.pk) not in ids


@pytest.mark.django_db
class TestKnowledgeBaseBulkCreate:
    def test_bulk_create(self, admin_client, clinic_a):
        payload = [
            {"clinic": clinic_a.pk, "kb_type": "faq", "content": f"Answer {i}", "title": f"FAQ {i}"}
            for i in range(3)
        ]
        response = admin_client.post("/api/knowledge/entries/bulk-create/", payload, format="json")
        assert response.status_code == 201
        assert len(response.data) == 3


@pytest.mark.django_db
class TestKnowledgeBaseBulkUpdate:
    def test_bulk_update(self, admin_client, kb_entry_a):
        payload = [{"id": str(kb_entry_a.pk), "title": "Bulk Updated"}]
        response = admin_client.patch("/api/knowledge/entries/bulk-update/", payload, format="json")
        assert response.status_code == 200
        assert len(response.data["updated"]) == 1
        assert response.data["updated"][0]["title"] == "Bulk Updated"


# ---------------------------------------------------------------------------
# ClinicInfoQuery
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestInfoQueryViewSetList:
    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/knowledge/queries/")
        assert response.status_code in (401, 403)

    def test_admin_can_list(self, admin_client, query_a):
        response = admin_client.get("/api/knowledge/queries/")
        assert response.status_code == 200

    def test_clinic_isolation(self, admin_client, query_a, query_b):
        response = admin_client.get("/api/knowledge/queries/")
        ids = [str(q["id"]) for q in response.data["results"]]
        assert str(query_a.pk) in ids
        assert str(query_b.pk) not in ids


@pytest.mark.django_db
class TestInfoQueryViewSetCreate:
    def test_admin_can_create(self, admin_client, clinic_a):
        data = {"clinic": clinic_a.pk, "question": "New question?"}
        response = admin_client.post("/api/knowledge/queries/", data)
        assert response.status_code == 201

    def test_staff_can_create(self, staff_client, clinic_a):
        data = {"clinic": clinic_a.pk, "question": "Staff question?"}
        response = staff_client.post("/api/knowledge/queries/", data)
        assert response.status_code == 201


@pytest.mark.django_db
class TestInfoQueryBulkCreate:
    def test_bulk_create(self, admin_client, clinic_a):
        payload = [
            {"clinic": clinic_a.pk, "question": f"Question {i}?"}
            for i in range(3)
        ]
        response = admin_client.post("/api/knowledge/queries/bulk-create/", payload, format="json")
        assert response.status_code == 201
        assert len(response.data) == 3


@pytest.mark.django_db
class TestInfoQueryExport:
    def test_export_returns_list(self, admin_client, query_a):
        response = admin_client.get("/api/knowledge/queries/export/")
        assert response.status_code == 200
        assert isinstance(response.data, list)


# ---------------------------------------------------------------------------
# ClinicInfoCache
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestInfoCacheViewSetList:
    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/knowledge/cache/")
        assert response.status_code in (401, 403)

    def test_admin_can_list(self, admin_client, cache_a):
        response = admin_client.get("/api/knowledge/cache/")
        assert response.status_code == 200

    def test_clinic_isolation(self, admin_client, cache_a, cache_b):
        response = admin_client.get("/api/knowledge/cache/")
        ids = [str(c["id"]) for c in response.data["results"]]
        assert str(cache_a.pk) in ids
        assert str(cache_b.pk) not in ids


@pytest.mark.django_db
class TestInfoCacheViewSetCreate:
    def test_admin_can_create(self, admin_client, clinic_a):
        data = {
            "clinic": clinic_a.pk,
            "normalized_question": "unique cache question",
            "answer": "cached answer",
        }
        response = admin_client.post("/api/knowledge/cache/", data)
        assert response.status_code == 201

    def test_unique_constraint_per_clinic_enforced(self, admin_client, clinic_a, cache_a):
        data = {
            "clinic": clinic_a.pk,
            "normalized_question": cache_a.normalized_question,
            "answer": "duplicate",
        }
        response = admin_client.post("/api/knowledge/cache/", data)
        assert response.status_code == 400


@pytest.mark.django_db
class TestInfoCacheExport:
    def test_export_returns_list(self, admin_client, cache_a):
        response = admin_client.get("/api/knowledge/cache/export/")
        assert response.status_code == 200
        assert isinstance(response.data, list)
