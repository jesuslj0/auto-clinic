import uuid

from django.db import models

from core.models import Clinic


class ClinicKnowledgeBase(models.Model):
    KB_TYPE_CHOICES = [
        ("pricing", "Pricing"),
        ("schedule", "Schedule"),
        ("faq", "FAQ"),
        ("location", "Location"),
        ("policies", "Policies"),
        ("team", "Team"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, related_name="knowledge_base", db_column="clinic_id"
    )
    kb_type = models.CharField(max_length=50, choices=KB_TYPE_CHOICES)
    title = models.CharField(max_length=200, blank=True)
    content = models.TextField()
    metadata = models.JSONField(default=dict)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "clinic_knowledge_base"
        indexes = [
            models.Index(fields=["clinic", "active", "kb_type"], name="idx_kb_clinic_active_type"),
        ]

    def __str__(self):
        return f"{self.clinic_id} – {self.kb_type} – {self.title}"


class ClinicInfoQuery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="info_queries",
        db_column="clinic_id",
    )
    question = models.TextField()
    intent_category = models.CharField(max_length=50, blank=True)
    answer = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "clinic_info_queries"

    def __str__(self):
        return f"{self.clinic_id} – {self.intent_category} – {self.created_at:%Y-%m-%d %H:%M}"


class ClinicInfoCache(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(
        Clinic, on_delete=models.CASCADE, related_name="info_cache", db_column="clinic_id"
    )
    normalized_question = models.TextField()
    answer = models.TextField(blank=True)
    intent_category = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "clinic_info_cache"
        constraints = [
            models.UniqueConstraint(
                fields=["clinic", "normalized_question"], name="uq_cache_clinic_question"
            )
        ]

    def __str__(self):
        return f"{self.clinic_id} – {self.normalized_question[:60]}"
