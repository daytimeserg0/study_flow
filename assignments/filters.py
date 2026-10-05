from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.filters import BaseFilterBackend

from assignments.models import Assignment, Submission


class AssignmentListQuerySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Assignment.Status.choices, required=False)
    due_before = serializers.DateTimeField(required=False)
    due_after = serializers.DateTimeField(required=False)
    overdue = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if (
            "due_before" in attrs
            and "due_after" in attrs
            and attrs["due_after"] > attrs["due_before"]
        ):
            raise serializers.ValidationError({"due_after": "Начало диапазона позже его конца."})
        return attrs


class AssignmentFilter(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        serializer = AssignmentListQuerySerializer(data=request.query_params.dict())
        serializer.is_valid(raise_exception=True)
        filters = serializer.validated_data
        if "status" in filters:
            queryset = queryset.filter(status=filters["status"])
        if "due_before" in filters:
            queryset = queryset.filter(due_at__lte=filters["due_before"])
        if "due_after" in filters:
            queryset = queryset.filter(due_at__gte=filters["due_after"])
        if "overdue" in filters:
            overdue = Q(status=Assignment.Status.PUBLISHED, due_at__lte=timezone.now())
            queryset = queryset.filter(overdue) if filters["overdue"] else queryset.exclude(overdue)
        return queryset


class SubmissionListQuerySerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Submission.Status.choices, required=False)
    student = serializers.CharField(max_length=150, required=False)


class SubmissionFilter(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        serializer = SubmissionListQuerySerializer(data=request.query_params.dict())
        serializer.is_valid(raise_exception=True)
        filters = serializer.validated_data
        if "status" in filters:
            queryset = queryset.filter(
                grade__isnull=filters["status"] == Submission.Status.SUBMITTED
            )
        if "student" in filters:
            queryset = queryset.filter(student__username=filters["student"])
        return queryset
