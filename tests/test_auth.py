from datetime import timedelta

import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from users.models import User

pytestmark = pytest.mark.django_db

PASSWORD = "Orion!47-pinebridge"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def registration_data():
    return {
        "username": "new_student",
        "email": "student@example.com",
        "password": PASSWORD,
        "first_name": "Анна",
        "last_name": "Соколова",
    }


@pytest.fixture
def student():
    return User.objects.create_user(
        username="existing_student",
        email="existing@example.com",
        password=PASSWORD,
    )


def test_registration_creates_student_with_hashed_password(api_client, registration_data):
    response = api_client.post(reverse("users:register"), registration_data, format="json")

    assert response.status_code == 201
    user = User.objects.get(username=registration_data["username"])
    assert user.password != PASSWORD
    assert user.check_password(PASSWORD)
    assert user.role == User.Role.STUDENT
    assert user.is_active
    assert not user.is_staff
    assert not user.is_superuser
    assert not user.groups.exists()
    assert not user.user_permissions.exists()
    assert response.json() == {
        "id": user.pk,
        "username": registration_data["username"],
        "email": registration_data["email"],
        "first_name": registration_data["first_name"],
        "last_name": registration_data["last_name"],
        "role": User.Role.STUDENT,
    }


@pytest.mark.parametrize("password", ["short", "password", "12345678901", "new_student"])
def test_registration_rejects_weak_and_similar_passwords(api_client, registration_data, password):
    registration_data["password"] = password

    response = api_client.post(reverse("users:register"), registration_data, format="json")

    assert response.status_code == 400
    assert "password" in response.json()
    assert not User.objects.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "invalid-email"),
        ("email", ""),
        ("email", "a" * 64 + "@" + ".".join(["b" * 63] * 3) + ".com"),
        ("username", "not a valid username"),
        ("username", ""),
    ],
)
def test_registration_rejects_invalid_identity(api_client, registration_data, field, value):
    registration_data[field] = value

    response = api_client.post(reverse("users:register"), registration_data, format="json")

    assert response.status_code == 400
    assert field in response.json()
    assert not User.objects.exists()


@pytest.mark.parametrize("field", ["username", "email", "password"])
def test_registration_requires_identity_and_password(api_client, registration_data, field):
    del registration_data[field]

    response = api_client.post(reverse("users:register"), registration_data, format="json")

    assert response.status_code == 400
    assert field in response.json()
    assert not User.objects.exists()


def test_registration_rejects_duplicate_username(api_client, registration_data, student):
    registration_data["username"] = student.username

    response = api_client.post(reverse("users:register"), registration_data, format="json")

    assert response.status_code == 400
    assert "username" in response.json()
    assert User.objects.count() == 1
    student.refresh_from_db()
    assert student.email == "existing@example.com"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", "teacher"),
        ("role", "student"),
        ("is_staff", True),
        ("is_superuser", True),
        ("is_active", False),
        ("groups", [1]),
        ("user_permissions", [1]),
        ("id", 1234),
        ("unexpected_field", "value"),
    ],
)
def test_registration_rejects_unwritable_fields(api_client, registration_data, field, value):
    registration_data[field] = value

    response = api_client.post(reverse("users:register"), registration_data, format="json")

    assert response.status_code == 400
    assert not User.objects.exists()


@pytest.mark.parametrize(
    ("username", "password"),
    [("existing_student", "wrong-password"), ("unknown_student", PASSWORD)],
)
def test_login_rejects_incorrect_credentials(api_client, student, username, password):
    response = api_client.post(
        reverse("users:token-obtain-pair"),
        {"username": username, "password": password},
        format="json",
    )

    assert response.status_code == 401
    assert "access" not in response.json()
    assert "refresh" not in response.json()


@pytest.mark.parametrize("token_kind", ["missing", "invalid", "expired", "refresh"])
def test_profile_requires_valid_access_token(api_client, student, token_kind):
    if token_kind == "invalid":
        api_client.credentials(HTTP_AUTHORIZATION="Bearer invalid-token")
    elif token_kind == "expired":
        token = AccessToken.for_user(student)
        token.set_exp(lifetime=timedelta(seconds=-1))
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    elif token_kind == "refresh":
        token = RefreshToken.for_user(student)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = api_client.get(reverse("users:me"))

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert "email" not in response.json()


def test_login_profile_and_refresh_flow(api_client, student):
    login_response = api_client.post(
        reverse("users:token-obtain-pair"),
        {"username": student.username, "password": PASSWORD},
        format="json",
    )

    assert login_response.status_code == 200
    tokens = login_response.json()
    assert set(tokens) == {"access", "refresh"}
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    profile_response = api_client.get(reverse("users:me"))

    assert profile_response.status_code == 200
    assert profile_response.json() == {
        "id": student.pk,
        "username": student.username,
        "email": student.email,
        "first_name": "",
        "last_name": "",
        "role": User.Role.STUDENT,
    }

    api_client.credentials()
    refresh_response = api_client.post(
        reverse("users:token-refresh"), {"refresh": tokens["refresh"]}, format="json"
    )

    assert refresh_response.status_code == 200
    assert set(refresh_response.json()) == {"access"}
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh_response.json()['access']}")
    refreshed_profile_response = api_client.get(reverse("users:me"))
    assert refreshed_profile_response.status_code == 200
    assert refreshed_profile_response.json() == profile_response.json()


def test_inactive_user_cannot_log_in(api_client, student):
    student.is_active = False
    student.save(update_fields=["is_active"])

    response = api_client.post(
        reverse("users:token-obtain-pair"),
        {"username": student.username, "password": PASSWORD},
        format="json",
    )

    assert response.status_code == 401
    assert "access" not in response.json()


@pytest.mark.parametrize("token_kind", ["access", "refresh"])
def test_deactivated_user_cannot_use_existing_tokens(api_client, student, token_kind):
    refresh = RefreshToken.for_user(student)
    access = str(refresh.access_token)
    student.is_active = False
    student.save(update_fields=["is_active"])

    if token_kind == "access":
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = api_client.get(reverse("users:me"))
    else:
        response = api_client.post(
            reverse("users:token-refresh"), {"refresh": str(refresh)}, format="json"
        )

    assert response.status_code == 401
    assert "access" not in response.json()


@pytest.mark.parametrize("method", ["post", "patch", "put", "delete"])
def test_profile_cannot_be_modified(api_client, student, method):
    token = AccessToken.for_user(student)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = getattr(api_client, method)(
        reverse("users:me"),
        {"username": "changed", "role": "teacher", "is_superuser": True},
        format="json",
    )

    assert response.status_code == 405
    student.refresh_from_db()
    assert student.username == "existing_student"
    assert student.role == User.Role.STUDENT
    assert not student.is_superuser


def test_user_cannot_retrieve_another_users_profile(api_client, student):
    other_user = User.objects.create_user(username="other_student", email="other@example.com")
    token = AccessToken.for_user(student)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    response = api_client.get(f"/api/users/{other_user.pk}/")

    assert response.status_code == 404
    assert other_user.email.encode() not in response.content


def test_profile_reflects_role_change_without_new_token(api_client, student):
    token = AccessToken.for_user(student)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    assert api_client.get(reverse("users:me")).json()["role"] == User.Role.STUDENT
    student.role = User.Role.TEACHER
    student.save(update_fields=["role"])

    response = api_client.get(reverse("users:me"))

    assert response.status_code == 200
    assert response.json()["role"] == User.Role.TEACHER
    student.refresh_from_db()
    assert not student.is_staff
    assert not student.is_superuser


def test_django_session_does_not_authenticate_api(api_client, student):
    api_client.force_login(student)

    response = api_client.get(reverse("users:me"))

    assert response.status_code == 401


@pytest.mark.parametrize("token_kind", ["invalid", "expired", "access"])
def test_refresh_rejects_invalid_tokens(api_client, student, token_kind):
    if token_kind == "invalid":
        token = "invalid-token"
    elif token_kind == "expired":
        token = RefreshToken.for_user(student)
        token.set_exp(lifetime=timedelta(seconds=-1))
    else:
        token = AccessToken.for_user(student)

    response = api_client.post(
        reverse("users:token-refresh"), {"refresh": str(token)}, format="json"
    )

    assert response.status_code == 401
    assert "access" not in response.json()
