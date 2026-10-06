from django.conf import settings
from django.db import OperationalError, connection
from django.views.generic import TemplateView
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class StudyFlowView(TemplateView):
    template_name = "studyflow/index.html"

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "debug": settings.DEBUG}


class HealthCheckSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "error"])
    database = serializers.ChoiceField(choices=["ok", "unavailable"])


class HealthCheckView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Проверка состояния приложения и базы данных",
        tags=["System"],
        responses={200: HealthCheckSerializer, 503: HealthCheckSerializer},
        examples=[
            OpenApiExample(
                "Healthy",
                value={"status": "ok", "database": "ok"},
                response_only=True,
                status_codes=["200"],
            ),
            OpenApiExample(
                "Database unavailable",
                value={"status": "error", "database": "unavailable"},
                response_only=True,
                status_codes=["503"],
            ),
        ],
    )
    def get(self, request):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except OperationalError:
            return Response(
                {"status": "error", "database": "unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({"status": "ok", "database": "ok"})
