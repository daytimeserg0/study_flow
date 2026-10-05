from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from courses.models import Course, Enrollment
from courses.permissions import CoursePermission
from courses.serializers import (
    CourseSerializer,
    EnrollmentCreateSerializer,
    EnrollmentSerializer,
)
from users.models import User


class AlreadyEnrolled(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Студент уже зачислен на этот курс."
    default_code = "already_enrolled"


@extend_schema_view(
    list=extend_schema(summary="Список доступных курсов", tags=["Курсы"]),
    retrieve=extend_schema(summary="Информация о курсе", tags=["Курсы"]),
    create=extend_schema(summary="Создание курса преподавателем", tags=["Курсы"]),
    update=extend_schema(summary="Редактирование курса", tags=["Курсы"]),
    partial_update=extend_schema(summary="Частичное редактирование курса", tags=["Курсы"]),
)
class CourseViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = CourseSerializer
    permission_classes = (IsAuthenticated, CoursePermission)
    http_method_names = ("get", "post", "put", "patch", "head", "options")

    def get_queryset(self):
        queryset = Course.objects.select_related("teacher")
        user = self.request.user
        if not user.is_authenticated:
            return queryset.none()
        if user.role == User.Role.TEACHER:
            return queryset.filter(teacher=user)
        return queryset.filter(enrollments__student=user)

    def get_serializer_class(self):
        if self.action == "students":
            if self.request.method == "POST":
                return EnrollmentCreateSerializer
            return EnrollmentSerializer
        return CourseSerializer

    def perform_create(self, serializer):
        serializer.save(teacher=self.request.user)

    @extend_schema(
        methods=["GET"],
        summary="Список студентов курса",
        tags=["Курсы"],
        responses=EnrollmentSerializer(many=True),
    )
    @extend_schema(
        methods=["POST"],
        summary="Зачисление студента по username",
        tags=["Курсы"],
        request=EnrollmentCreateSerializer,
        responses={
            201: EnrollmentSerializer,
            409: OpenApiResponse(description="Студент уже зачислен на курс."),
        },
    )
    @action(detail=True, methods=["get", "post"])
    def students(self, request, pk=None):
        course = self.get_object()
        if request.method == "POST":
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            enrollment, created = Enrollment.objects.get_or_create(
                course=course, student=serializer.validated_data["student"]
            )
            if not created:
                raise AlreadyEnrolled
            return Response(
                EnrollmentSerializer(enrollment, context=self.get_serializer_context()).data,
                status=status.HTTP_201_CREATED,
            )
        enrollments = course.enrollments.select_related("student")
        return Response(self.get_serializer(enrollments, many=True).data)
