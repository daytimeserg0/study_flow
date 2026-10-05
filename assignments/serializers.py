from django.core.validators import URLValidator
from django.utils import timezone
from rest_framework import serializers

from assignments.models import Assignment, Submission
from courses.serializers import CourseUserSerializer, WritableFieldsMixin


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


class SubmissionSerializer(WritableFieldsMixin, serializers.ModelSerializer):
    student = CourseUserSerializer(read_only=True)
    answer = serializers.CharField(
        max_length=20000, required=False, allow_blank=True, trim_whitespace=False
    )
    solution_url = serializers.URLField(
        max_length=500,
        required=False,
        allow_blank=True,
        validators=(URLValidator(schemes=("http", "https")),),
    )

    class Meta:
        model = Submission
        fields = (
            "id",
            "assignment",
            "student",
            "answer",
            "solution_url",
            "submitted_at",
            "updated_at",
        )
        read_only_fields = ("id", "assignment", "student", "submitted_at", "updated_at")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        answer = attrs.get("answer", self.instance.answer if self.instance else "")
        solution_url = attrs.get(
            "solution_url", self.instance.solution_url if self.instance else ""
        )
        if not answer.strip() and not solution_url:
            raise serializers.ValidationError("Добавьте текст решения или ссылку на него.")
        return attrs
