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
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from assignments.filters import SubmissionFilter, SubmissionListQuerySerializer
from assignments.models import Assignment, Grade, Submission
from assignments.permissions import SubmissionPermission
from assignments.serializers import GradeInputSerializer, SubmissionSerializer
from config.filters import CollectionFilteringMixin, StableOrderingFilter
from users.models import User


class AlreadySubmitted(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Решение уже отправлено. Измените существующую работу."
    default_code = "already_submitted"


class SubmissionAlreadyGraded(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Проверенное решение нельзя изменить."
    default_code = "submission_already_graded"


@extend_schema(
    tags=["Решения"],
    parameters=[
        OpenApiParameter("course_pk", OpenApiTypes.INT, OpenApiParameter.PATH),
        OpenApiParameter("assignment_pk", OpenApiTypes.INT, OpenApiParameter.PATH),
    ],
)
@extend_schema_view(
    list=extend_schema(
        summary="Доступные решения задания", parameters=[SubmissionListQuerySerializer]
    ),
    retrieve=extend_schema(summary="Информация о решении"),
    create=extend_schema(
        summary="Отправка решения студентом",
        responses={
            201: SubmissionSerializer,
            409: OpenApiResponse(description="У студента уже есть решение этого задания."),
        },
    ),
    update=extend_schema(
        summary="Редактирование своего решения до проверки и дедлайна",
        responses={
            200: SubmissionSerializer,
            409: OpenApiResponse(description="Решение уже проверено."),
        },
    ),
    partial_update=extend_schema(
        summary="Частичное редактирование своего решения до проверки и дедлайна",
        responses={
            200: SubmissionSerializer,
            409: OpenApiResponse(description="Решение уже проверено."),
        },
    ),
)
class SubmissionViewSet(
    CollectionFilteringMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = SubmissionSerializer
    permission_classes = (IsAuthenticated, SubmissionPermission)
    http_method_names = ("get", "post", "put", "patch", "head", "options")
    filter_backends = (SubmissionFilter, SearchFilter, StableOrderingFilter)
    search_fields = ("student__username", "student__first_name", "student__last_name")
    ordering_fields = ("submitted_at", "updated_at")
    ordering = ("-submitted_at", "-id")

    def get_assignment(self):
        if not hasattr(self, "_assignment"):
            assignments = Assignment.objects.filter(
                course_id=self.kwargs["course_pk"]
            ).select_related("course")
            user = self.request.user
            if user.role == User.Role.TEACHER:
                assignments = assignments.filter(course__teacher=user)
            else:
                assignments = assignments.filter(
                    course__enrollments__student=user, status=Assignment.Status.PUBLISHED
                )
            if self.action in ("create", "update", "partial_update", "grade"):
                assignments = assignments.select_for_update(of=("self",))
            self._assignment = get_object_or_404(assignments, pk=self.kwargs["assignment_pk"])
        return self._assignment

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Submission.objects.none()
        queryset = Submission.objects.filter(assignment=self.get_assignment()).select_related(
            "student", "grade__graded_by"
        )
        if self.request.user.role != User.Role.TEACHER:
            queryset = queryset.filter(student=self.request.user)
        if self.action in ("update", "partial_update", "grade"):
            queryset = queryset.select_for_update(of=("self",))
        return queryset

    def check_deadline(self):
        due_at = self.get_assignment().due_at
        if due_at is not None and due_at <= timezone.now():
            raise ValidationError({"due_at": "Срок сдачи задания истёк."})

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        self.get_assignment()
        if request.user.role != User.Role.STUDENT:
            raise PermissionDenied("Отправлять решения могут только студенты курса.")
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        assignment = self.get_assignment()
        if Submission.objects.filter(assignment=assignment, student=self.request.user).exists():
            raise AlreadySubmitted
        self.check_deadline()
        serializer.save(assignment=assignment, student=self.request.user)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        if serializer.instance.status == Submission.Status.GRADED:
            raise SubmissionAlreadyGraded
        self.check_deadline()
        serializer.save()

    @extend_schema(
        summary="Выставление или исправление оценки преподавателем",
        request=GradeInputSerializer,
        responses=SubmissionSerializer,
    )
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def grade(self, request, course_pk=None, assignment_pk=None, pk=None):
        submission = self.get_object()
        serializer = GradeInputSerializer(
            data=request.data, context={"assignment": self.get_assignment()}
        )
        serializer.is_valid(raise_exception=True)
        grade, _ = Grade.objects.update_or_create(
            submission=submission,
            defaults={**serializer.validated_data, "graded_by": request.user},
        )
        submission.grade = grade
        return Response(self.get_serializer(submission).data)
