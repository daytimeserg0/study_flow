from django.core.validators import URLValidator
from django.utils import timezone
from rest_framework import serializers

from assignments.models import Assignment, Grade, Submission
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

    def validate_max_score(self, value):
        if self.instance and self.instance.submissions.filter(grade__score__gt=value).exists():
            raise serializers.ValidationError(
                "Максимальный балл не может быть ниже уже выставленных оценок."
            )
        return value


class PublishAssignmentSerializer(WritableFieldsMixin, serializers.Serializer):
    pass


class GradeSerializer(serializers.ModelSerializer):
    graded_by = CourseUserSerializer(read_only=True)

    class Meta:
        model = Grade
        fields = ("id", "score", "feedback", "graded_by", "graded_at")
        read_only_fields = fields


class GradeInputSerializer(WritableFieldsMixin, serializers.Serializer):
    score = serializers.IntegerField(min_value=0, max_value=1000)
    feedback = serializers.CharField(max_length=5000, required=False, allow_blank=True, default="")

    def validate_score(self, value):
        if value > self.context["assignment"].max_score:
            raise serializers.ValidationError("Балл превышает максимум задания.")
        return value


class SubmissionSerializer(WritableFieldsMixin, serializers.ModelSerializer):
    student = CourseUserSerializer(read_only=True)
    status = serializers.ChoiceField(choices=Submission.Status.choices, read_only=True)
    grade = GradeSerializer(read_only=True, allow_null=True)
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
            "status",
            "grade",
            "submitted_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "assignment",
            "student",
            "status",
            "grade",
            "submitted_at",
            "updated_at",
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        answer = attrs.get("answer", self.instance.answer if self.instance else "")
        solution_url = attrs.get(
            "solution_url", self.instance.solution_url if self.instance else ""
        )
        if not answer.strip() and not solution_url:
            raise serializers.ValidationError("Добавьте текст решения или ссылку на него.")
        return attrs
