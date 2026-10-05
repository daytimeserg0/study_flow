from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from assignments.models import Assignment
from assignments.permissions import AssignmentPermission
from assignments.serializers import AssignmentSerializer, PublishAssignmentSerializer
from courses.models import Course
from users.models import User


class PublishedAssignmentDeletionError(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Опубликованное задание нельзя удалить."
    default_code = "assignment_already_published"


@extend_schema(
    tags=["Задания"],
    parameters=[OpenApiParameter("course_pk", OpenApiTypes.INT, OpenApiParameter.PATH)],
)
@extend_schema_view(
    list=extend_schema(summary="Задания курса"),
    retrieve=extend_schema(summary="Информация о задании"),
    create=extend_schema(summary="Создание черновика задания"),
    update=extend_schema(summary="Редактирование задания"),
    partial_update=extend_schema(summary="Частичное редактирование задания"),
    destroy=extend_schema(
        summary="Удаление черновика",
        responses={204: None, 409: OpenApiResponse(description="Задание уже опубликовано.")},
    ),
)
class AssignmentViewSet(viewsets.ModelViewSet):
    serializer_class = AssignmentSerializer
    permission_classes = (IsAuthenticated, AssignmentPermission)
    http_method_names = ("get", "post", "put", "patch", "delete", "head", "options")

    def get_course(self):
        if not hasattr(self, "_course"):
            courses = Course.objects.all()
            user = self.request.user
            if user.role == User.Role.TEACHER:
                courses = courses.filter(teacher=user)
            else:
                courses = courses.filter(enrollments__student=user)
            self._course = get_object_or_404(courses, pk=self.kwargs["course_pk"])
        return self._course

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Assignment.objects.none()
        queryset = Assignment.objects.filter(course=self.get_course()).select_related("course")
        if self.request.user.role != User.Role.TEACHER:
            queryset = queryset.filter(status=Assignment.Status.PUBLISHED)
        if self.action in ("update", "partial_update"):
            queryset = queryset.select_for_update(of=("self",))
        return queryset

    def perform_create(self, serializer):
        serializer.save(course=self.get_course())

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def perform_destroy(self, instance):
        with transaction.atomic():
            assignment = get_object_or_404(Assignment.objects.select_for_update(), pk=instance.pk)
            if assignment.status == Assignment.Status.PUBLISHED:
                raise PublishedAssignmentDeletionError
            assignment.delete()

    @extend_schema(
        summary="Публикация задания",
        request=None,
        responses=AssignmentSerializer,
    )
    @action(detail=True, methods=["post"])
    def publish(self, request, course_pk=None, pk=None):
        instance = self.get_object()
        payload = PublishAssignmentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        with transaction.atomic():
            assignment = get_object_or_404(Assignment.objects.select_for_update(), pk=instance.pk)
            if assignment.status == Assignment.Status.DRAFT:
                now = timezone.now()
                if assignment.due_at is not None and assignment.due_at <= now:
                    raise ValidationError({"due_at": "Срок сдачи должен быть в будущем."})
                assignment.status = Assignment.Status.PUBLISHED
                assignment.published_at = now
                assignment.save(update_fields=("status", "published_at", "updated_at"))
        return Response(self.get_serializer(assignment).data)
