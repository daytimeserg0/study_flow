from rest_framework import serializers

from courses.models import Course, Enrollment
from users.models import User


class WritableFieldsMixin:
    def validate(self, attrs):
        writable_fields = {name for name, field in self.fields.items() if not field.read_only}
        unexpected_fields = set(self.initial_data) - writable_fields
        if unexpected_fields:
            raise serializers.ValidationError(
                {name: "Это поле нельзя передавать в запросе." for name in unexpected_fields}
            )
        return super().validate(attrs)


class CourseUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "first_name", "last_name")
        read_only_fields = fields


class CourseSerializer(WritableFieldsMixin, serializers.ModelSerializer):
    teacher = CourseUserSerializer(read_only=True)

    class Meta:
        model = Course
        fields = ("id", "title", "description", "teacher", "created_at", "updated_at")
        read_only_fields = ("id", "teacher", "created_at", "updated_at")


class EnrollmentSerializer(serializers.ModelSerializer):
    student = CourseUserSerializer(read_only=True)

    class Meta:
        model = Enrollment
        fields = ("id", "course", "student", "created_at")
        read_only_fields = fields


class EnrollmentCreateSerializer(WritableFieldsMixin, serializers.Serializer):
    username = serializers.CharField(max_length=150)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        try:
            attrs["student"] = User.objects.get(
                username=attrs["username"], role=User.Role.STUDENT, is_active=True
            )
        except User.DoesNotExist as error:
            raise serializers.ValidationError(
                {"username": "Активный студент с таким username не найден."}
            ) from error
        return attrs


class CourseProgressSerializer(serializers.Serializer):
    student = CourseUserSerializer(source="*", read_only=True)
    assignments_count = serializers.IntegerField(read_only=True)
    submitted_count = serializers.IntegerField(read_only=True)
    graded_count = serializers.IntegerField(read_only=True)
    overdue_count = serializers.IntegerField(read_only=True)
    earned_score = serializers.IntegerField(read_only=True)
    max_score = serializers.IntegerField(read_only=True)
    completion_percent = serializers.SerializerMethodField()

    def get_completion_percent(self, obj) -> float:
        if not obj.assignments_count:
            return 0.0
        return round(100 * obj.submitted_count / obj.assignments_count, 2)
