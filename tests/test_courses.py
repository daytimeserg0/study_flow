import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework.test import APIClient

from courses.models import Course, Enrollment
from users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def teacher():
    return User.objects.create_user(
        username="teacher",
        email="teacher@example.com",
        first_name="Анна",
        last_name="Соколова",
        role=User.Role.TEACHER,
    )


@pytest.fixture
def other_teacher():
    return User.objects.create_user(username="other_teacher", role=User.Role.TEACHER)


@pytest.fixture
def student():
    return User.objects.create_user(
        username="student",
        email="student@example.com",
        first_name="Иван",
        last_name="Петров",
    )


@pytest.fixture
def other_student():
    return User.objects.create_user(username="other_student", email="other@example.com")


@pytest.fixture
def course(teacher):
    return Course.objects.create(title="Основы Python", description="Первый курс", teacher=teacher)


@pytest.fixture
def other_course(other_teacher):
    return Course.objects.create(title="Базы данных", teacher=other_teacher)


def public_user(user):
    return {
        "id": user.pk,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


@pytest.mark.parametrize(
    ("route", "method"),
    [
        ("course-list", "get"),
        ("course-list", "post"),
        ("course-detail", "get"),
        ("course-detail", "put"),
        ("course-detail", "patch"),
        ("course-students", "get"),
        ("course-students", "post"),
    ],
)
def test_course_api_requires_authentication(api_client, course, route, method):
    kwargs = {} if route == "course-list" else {"pk": course.pk}
    response = getattr(api_client, method)(reverse(f"courses:{route}", kwargs=kwargs))

    assert response.status_code == 401
    assert not response.json().get("title")


def test_teacher_creates_course_with_server_assigned_ownership(api_client, teacher):
    api_client.force_authenticate(teacher)

    response = api_client.post(
        reverse("courses:course-list"),
        {"title": "  Основы Python  ", "description": "Введение в Python"},
        format="json",
    )

    assert response.status_code == 201
    created_course = Course.objects.get()
    assert created_course.title == "Основы Python"
    assert created_course.description == "Введение в Python"
    assert created_course.teacher == teacher
    data = response.json()
    assert data["id"] == created_course.pk
    assert data["title"] == created_course.title
    assert data["teacher"] == public_user(teacher)
    assert data["created_at"]
    assert data["updated_at"]
    assert teacher.email.encode() not in response.content


@pytest.mark.parametrize("title", ["", " \t\n ", "x" * 201])
def test_course_rejects_empty_or_oversized_title(api_client, teacher, title):
    api_client.force_authenticate(teacher)

    response = api_client.post(reverse("courses:course-list"), {"title": title}, format="json")

    assert response.status_code == 400
    assert "title" in response.json()
    assert not Course.objects.exists()


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("teacher", 9999),
        ("teacher_id", 9999),
        ("id", 9999),
        ("created_at", "2020-01-01T00:00:00Z"),
        ("updated_at", "2020-01-01T00:00:00Z"),
        ("unexpected_field", "value"),
    ],
)
def test_course_rejects_ownership_and_read_only_field_overrides(
    api_client, teacher, course, method, field, value
):
    api_client.force_authenticate(teacher)
    url = (
        reverse("courses:course-list")
        if method == "post"
        else reverse("courses:course-detail", kwargs={"pk": course.pk})
    )

    response = getattr(api_client, method)(
        url, {"title": "Изменённый курс", field: value}, format="json"
    )

    assert response.status_code == 400
    assert Course.objects.count() == 1
    course.refresh_from_db()
    assert course.title == "Основы Python"
    assert course.teacher == teacher


@pytest.mark.parametrize("method", ["put", "patch"])
def test_teacher_updates_own_course(api_client, teacher, course, method):
    api_client.force_authenticate(teacher)

    response = getattr(api_client, method)(
        reverse("courses:course-detail", kwargs={"pk": course.pk}),
        {"title": "Python для начинающих", "description": ""},
        format="json",
    )

    assert response.status_code == 200
    course.refresh_from_db()
    assert course.title == "Python для начинающих"
    assert course.description == ""
    assert course.teacher == teacher


def test_course_deletion_is_not_supported(api_client, teacher, course):
    api_client.force_authenticate(teacher)

    response = api_client.delete(reverse("courses:course-detail", kwargs={"pk": course.pk}))

    assert response.status_code == 405
    assert Course.objects.filter(pk=course.pk).exists()


@pytest.mark.parametrize("privileges", [{}, {"is_staff": True}, {"is_superuser": True}])
def test_student_cannot_create_course_even_with_admin_privileges(api_client, student, privileges):
    for field, value in privileges.items():
        setattr(student, field, value)
    student.save()
    api_client.force_authenticate(student)

    response = api_client.post(
        reverse("courses:course-list"), {"title": "Чужой курс"}, format="json"
    )

    assert response.status_code == 403
    assert not Course.objects.exists()


@pytest.mark.parametrize("actor", ["teacher", "other_teacher", "student", "other_student"])
def test_course_list_contains_only_owned_or_enrolled_courses(
    api_client, teacher, other_teacher, student, other_student, course, other_course, actor
):
    shared_course = Course.objects.create(title="Алгоритмы", teacher=other_teacher)
    Enrollment.objects.create(course=course, student=student)
    Enrollment.objects.create(course=shared_course, student=student)
    Enrollment.objects.create(course=other_course, student=other_student)
    users = {
        "teacher": teacher,
        "other_teacher": other_teacher,
        "student": student,
        "other_student": other_student,
    }
    visible_ids = {
        "teacher": {course.pk},
        "other_teacher": {other_course.pk, shared_course.pk},
        "student": {course.pk, shared_course.pk},
        "other_student": {other_course.pk},
    }
    api_client.force_authenticate(users[actor])

    response = api_client.get(reverse("courses:course-list"))

    assert response.status_code == 200
    assert isinstance(response.json()["results"], list)
    assert response.json()["count"] == len(visible_ids[actor])
    assert {item["id"] for item in response.json()["results"]} == visible_ids[actor]
    for item in response.json()["results"]:
        assert set(item["teacher"]) == {"id", "username", "first_name", "last_name"}


@pytest.mark.parametrize("role", [User.Role.STUDENT, User.Role.TEACHER])
def test_user_without_courses_gets_empty_list(api_client, role):
    user = User.objects.create_user(username="new_user", role=role)
    api_client.force_authenticate(user)

    response = api_client.get(reverse("courses:course-list"))

    assert response.status_code == 200
    assert response.json() == {"count": 0, "next": None, "previous": None, "results": []}


def test_enrolled_student_can_read_course_without_private_teacher_data(
    api_client, student, course, teacher
):
    Enrollment.objects.create(course=course, student=student)
    api_client.force_authenticate(student)

    response = api_client.get(reverse("courses:course-detail", kwargs={"pk": course.pk}))

    assert response.status_code == 200
    assert response.json()["title"] == course.title
    assert response.json()["teacher"] == public_user(teacher)
    assert teacher.email.encode() not in response.content


@pytest.mark.parametrize("actor", ["teacher", "student", "staff_student", "superuser_student"])
@pytest.mark.parametrize(
    ("route", "method"),
    [
        ("course-detail", "get"),
        ("course-detail", "patch"),
        ("course-detail", "put"),
        ("course-students", "get"),
        ("course-students", "post"),
    ],
)
def test_course_is_hidden_from_unrelated_users(
    api_client, teacher, student, other_course, actor, route, method
):
    if actor == "teacher":
        user = teacher
    else:
        user = student
        user.is_staff = actor == "staff_student"
        user.is_superuser = actor == "superuser_student"
        user.save()
    api_client.force_authenticate(user)

    response = getattr(api_client, method)(
        reverse(f"courses:{route}", kwargs={"pk": other_course.pk}),
        {"title": "Изменение", "username": student.username},
        format="json",
    )

    assert response.status_code == 404
    assert other_course.title.encode() not in response.content
    other_course.refresh_from_db()
    assert other_course.title == "Базы данных"
    assert not Enrollment.objects.exists()


@pytest.mark.parametrize(
    ("route", "method"),
    [
        ("course-detail", "patch"),
        ("course-detail", "put"),
        ("course-students", "get"),
        ("course-students", "post"),
    ],
)
def test_enrollment_does_not_grant_teacher_permissions(
    api_client, student, other_student, course, route, method
):
    Enrollment.objects.create(course=course, student=student)
    api_client.force_authenticate(student)

    response = getattr(api_client, method)(
        reverse(f"courses:{route}", kwargs={"pk": course.pk}),
        {"title": "Изменение", "username": other_student.username},
        format="json",
    )

    assert response.status_code == 403
    course.refresh_from_db()
    assert course.title == "Основы Python"
    assert Enrollment.objects.filter(course=course).count() == 1


def test_teacher_roster_contains_only_students_from_requested_course(
    api_client, teacher, student, other_student, course, other_course
):
    enrollment = Enrollment.objects.create(course=course, student=student)
    Enrollment.objects.create(course=other_course, student=other_student)
    api_client.force_authenticate(teacher)

    response = api_client.get(reverse("courses:course-students", kwargs={"pk": course.pk}))

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert len(response.json()["results"]) == 1
    item = response.json()["results"][0]
    assert item["id"] == enrollment.pk
    assert item["course"] == course.pk
    assert item["student"] == public_user(student)
    assert item["created_at"]
    assert student.email.encode() not in response.content
    assert other_student.username.encode() not in response.content


def test_teacher_enrolls_student_and_student_gains_course_access(
    api_client, teacher, student, course
):
    detail_url = reverse("courses:course-detail", kwargs={"pk": course.pk})
    api_client.force_authenticate(student)
    assert api_client.get(detail_url).status_code == 404
    api_client.force_authenticate(teacher)

    response = api_client.post(
        reverse("courses:course-students", kwargs={"pk": course.pk}),
        {"username": student.username},
        format="json",
    )

    assert response.status_code == 201
    enrollment = Enrollment.objects.get()
    assert enrollment.course == course
    assert enrollment.student == student
    assert response.json()["id"] == enrollment.pk
    assert response.json()["course"] == course.pk
    assert response.json()["student"] == public_user(student)
    api_client.force_authenticate(student)
    assert api_client.get(detail_url).status_code == 200
    assert [
        item["id"] for item in api_client.get(reverse("courses:course-list")).json()["results"]
    ] == [course.pk]


def test_duplicate_enrollment_returns_conflict_without_modifying_existing_record(
    api_client, teacher, student, course
):
    api_client.force_authenticate(teacher)
    url = reverse("courses:course-students", kwargs={"pk": course.pk})
    payload = {"username": student.username}
    first_response = api_client.post(url, payload, format="json")
    assert first_response.status_code == 201
    original = Enrollment.objects.get()

    response = api_client.post(url, payload, format="json")

    assert response.status_code == 409
    assert Enrollment.objects.count() == 1
    enrollment = Enrollment.objects.get()
    assert enrollment.pk == original.pk
    assert enrollment.created_at == original.created_at


@pytest.mark.parametrize("target", ["unknown", "inactive", "teacher"])
def test_only_active_students_can_be_enrolled(api_client, teacher, student, course, target):
    username = "unknown_student"
    if target == "inactive":
        student.is_active = False
        student.save(update_fields=["is_active"])
        username = student.username
    elif target == "teacher":
        username = teacher.username
    api_client.force_authenticate(teacher)

    response = api_client.post(
        reverse("courses:course-students", kwargs={"pk": course.pk}),
        {"username": username},
        format="json",
    )

    assert response.status_code == 400
    assert "username" in response.json()
    assert not Enrollment.objects.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [("course", 9999), ("student", 9999), ("id", 9999), ("unexpected_field", "value")],
)
def test_enrollment_rejects_unknown_fields_and_relationship_overrides(
    api_client, teacher, student, course, field, value
):
    api_client.force_authenticate(teacher)

    response = api_client.post(
        reverse("courses:course-students", kwargs={"pk": course.pk}),
        {"username": student.username, field: value},
        format="json",
    )

    assert response.status_code == 400
    assert not Enrollment.objects.exists()


@pytest.mark.parametrize("payload", [{}, {"username": " \t "}, {"username": "x" * 151}])
def test_enrollment_requires_valid_username(api_client, teacher, course, payload):
    api_client.force_authenticate(teacher)

    response = api_client.post(
        reverse("courses:course-students", kwargs={"pk": course.pk}), payload, format="json"
    )

    assert response.status_code == 400
    assert "username" in response.json()
    assert not Enrollment.objects.exists()


def test_database_prevents_duplicate_enrollment_but_allows_multiple_courses(
    student, course, other_course
):
    Enrollment.objects.create(course=course, student=student)
    Enrollment.objects.create(course=other_course, student=student)

    with pytest.raises(IntegrityError), transaction.atomic():
        Enrollment.objects.create(course=course, student=student)

    assert Enrollment.objects.filter(student=student).count() == 2


def test_course_permissions_follow_current_role_with_existing_jwt(api_client, teacher):
    password = "Orion!47-pinebridge"
    teacher.set_password(password)
    teacher.save(update_fields=["password"])
    login_response = api_client.post(
        reverse("users:token-obtain-pair"),
        {"username": teacher.username, "password": password},
        format="json",
    )
    assert login_response.status_code == 200
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_response.json()['access']}")
    url = reverse("courses:course-list")
    assert api_client.post(url, {"title": "Первый курс"}, format="json").status_code == 201
    teacher.role = User.Role.STUDENT
    teacher.save(update_fields=["role"])

    response = api_client.post(url, {"title": "Запрещённый курс"}, format="json")

    assert response.status_code == 403
    assert api_client.get(url).json() == {"count": 0, "next": None, "previous": None, "results": []}
    assert Course.objects.count() == 1
    teacher.role = User.Role.TEACHER
    teacher.save(update_fields=["role"])
    assert api_client.post(url, {"title": "Второй курс"}, format="json").status_code == 201
    assert Course.objects.filter(teacher=teacher).count() == 2
