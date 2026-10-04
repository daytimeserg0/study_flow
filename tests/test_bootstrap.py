from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.db import OperationalError, connection
from django.urls import reverse
from drf_spectacular.validation import validate_schema

from users.models import User


@pytest.mark.django_db
def test_custom_user_is_persisted_with_hashed_password():
    user_model = get_user_model()
    assert user_model is User

    user = user_model.objects.create_user(username="student", password="test-user-password")
    user.refresh_from_db()

    assert "users_user" in connection.introspection.table_names()
    assert user.password != "test-user-password"
    assert user.check_password("test-user-password")


@pytest.mark.django_db
def test_superuser_can_log_in_and_manage_custom_users(client):
    User.objects.create_superuser(username="admin", password="test-admin-password")

    response = client.post(
        reverse("admin:login"),
        {
            "username": "admin",
            "password": "test-admin-password",
            "next": reverse("admin:index"),
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("admin:index")

    response = client.get(reverse("admin:users_user_changelist"))

    assert response.status_code == 200
    assert b"admin" in response.content


@pytest.mark.django_db
def test_health_check_reaches_database_without_authentication(client):
    response = client.get(reverse("health-check"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_check_reports_database_failure_without_details(client):
    with patch(
        "config.views.connection.cursor",
        side_effect=OperationalError("database password or private host details"),
    ):
        response = client.get(reverse("health-check"))

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unavailable"}
    assert b"private host" not in response.content


def test_schema_is_public_and_describes_health_responses(client):
    response = client.get(reverse("schema"), HTTP_ACCEPT="application/vnd.oai.openapi+json")

    assert response.status_code == 200
    schema = response.json()
    validate_schema(schema)

    health_operation = schema["paths"][reverse("health-check")]["get"]
    assert {"200", "503"} <= health_operation["responses"].keys()
    assert not any(health_operation.get("security", []))


def test_swagger_is_public_and_uses_bundled_assets(client):
    response = client.get(reverse("swagger-ui"))

    assert response.status_code == 200
    for asset in ["swagger-ui.css", "swagger-ui-bundle.js"]:
        static_path = f"drf_spectacular_sidecar/swagger-ui-dist/{asset}"
        assert f"/static/{static_path}".encode() in response.content
        assert finders.find(static_path)
    assert reverse("schema").encode() in response.content


def test_root_redirects_to_api_documentation(client):
    response = client.get("/")

    assert response.status_code == 302
    assert response.url == reverse("swagger-ui")
