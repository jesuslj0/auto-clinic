"""
API tests for billing app: /api/subscriptions/
Covers CRUD, export, clinic isolation, permission rules (admin-only writes).
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from billing.models import Subscription


@pytest.mark.django_db
class TestSubscriptionViewSetList:
    def test_unauthenticated_denied(self, api_client):
        response = api_client.get("/api/subscriptions/")
        assert response.status_code in (401, 403)

    def test_admin_can_list(self, admin_client, subscription_a):
        response = admin_client.get("/api/subscriptions/")
        assert response.status_code == 200
        assert "results" in response.data

    def test_staff_can_read(self, staff_client, subscription_a):
        response = staff_client.get("/api/subscriptions/")
        assert response.status_code == 200

    def test_clinic_isolation(self, admin_client, subscription_a, clinic_b):
        sub_b = Subscription.objects.create(
            clinic=clinic_b, plan_name="Beta Plan",
            status=Subscription.Status.ACTIVE, starts_at=timezone.now(),
        )
        response = admin_client.get("/api/subscriptions/")
        ids = [s["id"] for s in response.data["results"]]
        assert subscription_a.pk in ids
        assert sub_b.pk not in ids

    def test_superuser_sees_all(self, superuser_client, subscription_a, clinic_b):
        sub_b = Subscription.objects.create(
            clinic=clinic_b, plan_name="Super Plan",
            starts_at=timezone.now(),
        )
        response = superuser_client.get("/api/subscriptions/")
        ids = [s["id"] for s in response.data["results"]]
        assert subscription_a.pk in ids
        assert sub_b.pk in ids


@pytest.mark.django_db
class TestSubscriptionViewSetCreate:
    def test_admin_can_create(self, admin_client, clinic_a):
        data = {
            "clinic": clinic_a.pk,
            "plan_name": "Premium",
            "status": "active",
            "starts_at": timezone.now().isoformat(),
        }
        response = admin_client.post("/api/subscriptions/", data)
        assert response.status_code == 201
        assert response.data["plan_name"] == "Premium"

    def test_staff_cannot_create(self, staff_client, clinic_a):
        data = {
            "clinic": clinic_a.pk,
            "plan_name": "Staff Plan",
            "starts_at": timezone.now().isoformat(),
        }
        response = staff_client.post("/api/subscriptions/", data)
        assert response.status_code == 403

    def test_create_requires_fields(self, admin_client):
        response = admin_client.post("/api/subscriptions/", {})
        assert response.status_code == 400


@pytest.mark.django_db
class TestSubscriptionViewSetRetrieve:
    def test_retrieve_own_clinic_subscription(self, admin_client, subscription_a):
        response = admin_client.get(f"/api/subscriptions/{subscription_a.pk}/")
        assert response.status_code == 200
        assert response.data["id"] == subscription_a.pk

    def test_retrieve_other_clinic_subscription_returns_404(self, admin_client, clinic_b):
        sub_b = Subscription.objects.create(
            clinic=clinic_b, plan_name="Other", starts_at=timezone.now(),
        )
        response = admin_client.get(f"/api/subscriptions/{sub_b.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestSubscriptionViewSetUpdate:
    def test_admin_can_update(self, admin_client, subscription_a):
        response = admin_client.patch(
            f"/api/subscriptions/{subscription_a.pk}/", {"plan_name": "Updated Plan"}
        )
        assert response.status_code == 200
        assert response.data["plan_name"] == "Updated Plan"

    def test_staff_cannot_update(self, staff_client, subscription_a):
        response = staff_client.patch(
            f"/api/subscriptions/{subscription_a.pk}/", {"plan_name": "Hacked"}
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestSubscriptionViewSetDelete:
    def test_admin_can_delete(self, admin_client, clinic_a):
        sub = Subscription.objects.create(
            clinic=clinic_a, plan_name="To Delete", starts_at=timezone.now(),
        )
        response = admin_client.delete(f"/api/subscriptions/{sub.pk}/")
        assert response.status_code == 204

    def test_staff_cannot_delete(self, staff_client, subscription_a):
        response = staff_client.delete(f"/api/subscriptions/{subscription_a.pk}/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestSubscriptionViewSetExport:
    def test_export_returns_list(self, admin_client, subscription_a):
        response = admin_client.get("/api/subscriptions/export/")
        assert response.status_code == 200
        assert isinstance(response.data, list)

    def test_export_respects_clinic_isolation(self, admin_client, subscription_a, clinic_b):
        sub_b = Subscription.objects.create(
            clinic=clinic_b, plan_name="Isolated", starts_at=timezone.now(),
        )
        response = admin_client.get("/api/subscriptions/export/")
        ids = [s["id"] for s in response.data]
        assert subscription_a.pk in ids
        assert sub_b.pk not in ids
