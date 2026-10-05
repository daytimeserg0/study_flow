from django.utils import timezone
from rest_framework import serializers

from assignments.models import Assignment
from courses.serializers import WritableFieldsMixin


class AssignmentSerializer(WritableFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Assignment
        fields = (
            "id",
            "course",
            "title",
            "description",
            "due_at",
            "max_score",
            "status",
            "published_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "course", "status", "published_at", "created_at", "updated_at")

    def validate_due_at(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError("Срок сдачи должен быть в будущем.")
        return value


class PublishAssignmentSerializer(WritableFieldsMixin, serializers.Serializer):
    pass
