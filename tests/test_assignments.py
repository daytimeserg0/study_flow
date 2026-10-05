from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment
from courses.models import Course, Enrollment
from users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def teacher():
    return User.objects.create_user(username="teacher", role=User.Role.TEACHER)


@pytest.fixture
def other_teacher():
    return User.objects.create_user(username="other_teacher", role=User.Role.TEACHER)


@pytest.fixture
def course(teacher):
    return Course.objects.create(title="Python", teacher=teacher)


@pytest.fixture
def other_course(other_teacher):
    return Course.objects.create(title="SQL", teacher=other_teacher)


@pytest.fixture
def student(course):
    user = User.objects.create_user(username="student")
    Enrollment.objects.create(course=course, student=user)
    return user


@pytest.fixture
def draft(course):
    return Assignment.objects.create(
        course=course,
        title="Закрытый черновик",
        description="Условие ещё не опубликовано",
        due_at=timezone.now() + timedelta(days=7),
    )


@pytest.fixture
def published(course):
    return Assignment.objects.create(
        course=course,
        title="Работа со словарями",
        description="Подсчитать частоту слов в тексте",
        status="published",
        published_at=timezone.now(),
        due_at=timezone.now() + timedelta(days=7),
    )


def assignment_url(course, assignment=None, *, publish=False):
    kwargs = {"course_pk": course.pk}
    if assignment is None:
        route = "assignment-list"
    else:
        kwargs["pk"] = assignment.pk
        route = "assignment-publish" if publish else "assignment-detail"
    return reverse(f"assignments:{route}", kwargs=kwargs)


@pytest.mark.parametrize(
    ("endpoint", "method"),
    [
        ("list", "get"),
        ("list", "post"),
        ("detail", "get"),
        ("detail", "put"),
        ("detail", "patch"),
        ("detail", "delete"),
        ("publish", "post"),
    ],
)
def test_assignment_api_requires_authentication(api_client, course, draft, endpoint, method):
    url = assignment_url(
        course, None if endpoint == "list" else draft, publish=endpoint == "publish"
    )

    response = getattr(api_client, method)(url)

    assert response.status_code == 401
    assert draft.description.encode() not in response.content


def test_teacher_creates_draft_with_default_score_and_no_deadline(api_client, teacher, course):
    api_client.force_authenticate(teacher)

    response = api_client.post(
        assignment_url(course),
        {"title": "  Первая программа  ", "description": "  Вывести приветствие  "},
        format="json",
    )

    assert response.status_code == 201
    assignment = Assignment.objects.get()
    assert assignment.course == course
    assert assignment.title == "Первая программа"
    assert assignment.description == "Вывести приветствие"
    assert assignment.max_score == 100
    assert assignment.due_at is None
    assert assignment.status == "draft"
    assert assignment.published_at is None
    data = response.json()
    assert data["id"] == assignment.pk
    assert data["course"] == course.pk
    assert data["status"] == "draft"
    assert data["published_at"] is None


@pytest.mark.parametrize("max_score", [1, 1000])
def test_teacher_creates_assignment_at_valid_limits(api_client, teacher, course, max_score):
    api_client.force_authenticate(teacher)
    due_at = timezone.now() + timedelta(days=1)

    response = api_client.post(
        assignment_url(course),
        {
            "title": "x" * 200,
            "description": "Условие задания",
            "due_at": due_at.isoformat(),
            "max_score": max_score,
        },
        format="json",
    )

    assert response.status_code == 201
    assignment = Assignment.objects.get()
    assert assignment.max_score == max_score
    assert assignment.due_at == due_at
    assert len(assignment.title) == 200


@pytest.mark.parametrize("method", ["put", "patch"])
@pytest.mark.parametrize("assignment_state", ["draft", "published"])
def test_teacher_can_edit_own_draft_and_published_assignment(
    api_client, teacher, course, draft, published, method, assignment_state
):
    assignment = draft if assignment_state == "draft" else published
    original_published_at = assignment.published_at
    api_client.force_authenticate(teacher)

    response = getattr(api_client, method)(
        assignment_url(course, assignment),
        {"title": "Новое название", "description": "Уточнённое условие", "max_score": 25},
        format="json",
    )

    assert response.status_code == 200
    assignment.refresh_from_db()
    assert assignment.title == "Новое название"
    assert assignment.description == "Уточнённое условие"
    assert assignment.max_score == 25
    assert assignment.status == assignment_state
    assert assignment.published_at == original_published_at


def test_teacher_list_includes_own_drafts_and_published_assignments_only(
    api_client, teacher, course, other_course, draft, published
):
    Assignment.objects.create(course=other_course, title="Чужое задание", description="Секрет")
    api_client.force_authenticate(teacher)

    response = api_client.get(assignment_url(course))

    assert response.status_code == 200
    assert isinstance(response.json()["results"], list)
    assert response.json()["count"] == 2
    assert {item["id"] for item in response.json()["results"]} == {draft.pk, published.pk}
    assert {item["status"] for item in response.json()["results"]} == {"draft", "published"}


def test_student_list_hides_drafts_and_assignments_from_other_courses(
    api_client, student, course, other_course, draft, published
):
    Assignment.objects.create(
        course=other_course,
        title="Чужое опубликованное задание",
        description="Чужое условие",
        status="published",
        published_at=timezone.now(),
    )
    api_client.force_authenticate(student)

    response = api_client.get(assignment_url(course))

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["results"]] == [published.pk]
    assert draft.title.encode() not in response.content
    assert draft.description.encode() not in response.content


def test_student_can_read_published_assignment_after_deadline(
    api_client, student, course, published
):
    published.due_at = timezone.now() - timedelta(days=1)
    published.save(update_fields=["due_at"])
    api_client.force_authenticate(student)

    response = api_client.get(assignment_url(course, published))

    assert response.status_code == 200
    assert response.json()["title"] == published.title
    assert response.json()["description"] == published.description
    assert response.json()["status"] == "published"


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete", "publish"])
def test_enrolled_student_cannot_discover_or_modify_draft(
    api_client, student, course, draft, method
):
    api_client.force_authenticate(student)
    url = assignment_url(course, draft, publish=method == "publish")
    request_method = "post" if method == "publish" else method

    response = getattr(api_client, request_method)(url, {}, format="json")

    assert response.status_code == 404
    assert draft.description.encode() not in response.content
    draft.refresh_from_db()
    assert draft.status == "draft"
    assert draft.published_at is None


@pytest.mark.parametrize("actor", ["teacher", "student", "staff_student", "superuser_student"])
@pytest.mark.parametrize(
    ("endpoint", "method"),
    [
        ("list", "get"),
        ("list", "post"),
        ("detail", "get"),
        ("detail", "patch"),
        ("detail", "delete"),
        ("publish", "post"),
    ],
)
def test_unrelated_users_cannot_access_course_assignments(
    api_client, other_teacher, course, published, actor, endpoint, method
):
    user = other_teacher
    if actor != "teacher":
        user = User.objects.create_user(
            username="outsider",
            is_staff=actor == "staff_student",
            is_superuser=actor == "superuser_student",
        )
    api_client.force_authenticate(user)
    url = assignment_url(
        course, None if endpoint == "list" else published, publish=endpoint == "publish"
    )

    response = getattr(api_client, method)(
        url, {"title": "Изменение", "description": "Новый текст"}, format="json"
    )

    assert response.status_code == 404
    assert published.description.encode() not in response.content
    published.refresh_from_db()
    assert published.title == "Работа со словарями"
    assert Assignment.objects.count() == 1


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete", "publish"])
def test_assignment_cannot_be_addressed_through_another_course(
    api_client, teacher, course, draft, method
):
    second_course = Course.objects.create(title="Второй курс", teacher=teacher)
    api_client.force_authenticate(teacher)
    url = assignment_url(second_course, draft, publish=method == "publish")
    request_method = "post" if method == "publish" else method

    response = getattr(api_client, request_method)(
        url, {"title": "Изменение", "description": "Новое условие"}, format="json"
    )

    assert response.status_code == 404
    draft.refresh_from_db()
    assert draft.course == course
    assert draft.title == "Закрытый черновик"
    assert draft.status == "draft"


@pytest.mark.parametrize("method", ["create", "put", "patch", "delete", "publish"])
def test_enrolled_student_cannot_write_assignments(api_client, student, course, published, method):
    api_client.force_authenticate(student)
    url = assignment_url(
        course, None if method == "create" else published, publish=method == "publish"
    )
    request_method = "post" if method in {"create", "publish"} else method

    response = getattr(api_client, request_method)(
        url, {"title": "Изменение", "description": "Новое условие"}, format="json"
    )

    assert response.status_code == 403
    assert Assignment.objects.count() == 1
    published.refresh_from_db()
    assert published.title == "Работа со словарями"
    assert published.status == "published"


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("course", 9999),
        ("course_id", 9999),
        ("status", "published"),
        ("published_at", "2026-01-01T00:00:00Z"),
        ("id", 9999),
        ("created_at", "2026-01-01T00:00:00Z"),
        ("updated_at", "2026-01-01T00:00:00Z"),
        ("unexpected_field", "value"),
    ],
)
def test_assignment_rejects_course_status_and_read_only_field_overrides(
    api_client, teacher, course, draft, method, field, value
):
    api_client.force_authenticate(teacher)
    url = assignment_url(course, None if method == "post" else draft)

    response = getattr(api_client, method)(
        url, {"title": "Изменение", "description": "Новое условие", field: value}, format="json"
    )

    assert response.status_code == 400
    assert Assignment.objects.count() == 1
    draft.refresh_from_db()
    assert draft.course == course
    assert draft.title == "Закрытый черновик"
    assert draft.status == "draft"
    assert draft.published_at is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", ""),
        ("title", " \t\n "),
        ("title", "x" * 201),
        ("description", ""),
        ("description", " \t\n "),
        ("max_score", 0),
        ("max_score", -1),
        ("max_score", 1001),
        ("max_score", 1.5),
        ("max_score", True),
        ("max_score", "invalid"),
    ],
)
def test_assignment_rejects_invalid_content_and_score(api_client, teacher, course, field, value):
    api_client.force_authenticate(teacher)
    payload = {"title": "Задание", "description": "Условие", field: value}

    response = api_client.post(assignment_url(course), payload, format="json")

    assert response.status_code == 400
    assert field in response.json()
    assert not Assignment.objects.exists()


@pytest.mark.parametrize("field", ["title", "description"])
def test_assignment_requires_title_and_description(api_client, teacher, course, field):
    api_client.force_authenticate(teacher)
    payload = {"title": "Задание", "description": "Условие"}
    del payload[field]

    response = api_client.post(assignment_url(course), payload, format="json")

    assert response.status_code == 400
    assert field in response.json()
    assert not Assignment.objects.exists()


@pytest.mark.parametrize("deadline", ["past", "invalid"])
@pytest.mark.parametrize("method", ["post", "patch"])
def test_assignment_rejects_past_or_malformed_deadline(
    api_client, teacher, course, draft, deadline, method
):
    api_client.force_authenticate(teacher)
    original_due_at = draft.due_at
    due_at = (
        (timezone.now() - timedelta(seconds=1)).isoformat() if deadline == "past" else "invalid"
    )
    url = assignment_url(course, None if method == "post" else draft)

    response = getattr(api_client, method)(
        url, {"title": "Задание", "description": "Условие", "due_at": due_at}, format="json"
    )

    assert response.status_code == 400
    assert "due_at" in response.json()
    assert Assignment.objects.count() == 1
    draft.refresh_from_db()
    assert draft.due_at == original_due_at


def test_teacher_can_remove_optional_deadline(api_client, teacher, course, draft):
    api_client.force_authenticate(teacher)

    response = api_client.patch(assignment_url(course, draft), {"due_at": None}, format="json")

    assert response.status_code == 200
    assert response.json()["due_at"] is None
    draft.refresh_from_db()
    assert draft.due_at is None


def test_publishing_records_time_and_makes_assignment_visible_to_student(
    api_client, teacher, student, course, draft
):
    api_client.force_authenticate(student)
    assert api_client.get(assignment_url(course)).json() == {
        "count": 0,
        "next": None,
        "previous": None,
        "results": [],
    }
    api_client.force_authenticate(teacher)
    before = timezone.now()

    response = api_client.post(assignment_url(course, draft, publish=True))

    assert response.status_code == 200
    draft.refresh_from_db()
    assert draft.status == "published"
    assert before <= draft.published_at <= timezone.now()
    assert response.json()["status"] == "published"
    assert response.json()["published_at"]
    api_client.force_authenticate(student)
    assert api_client.get(assignment_url(course, draft)).status_code == 200
    assert [item["id"] for item in api_client.get(assignment_url(course)).json()["results"]] == [
        draft.pk
    ]


@pytest.mark.parametrize("expired_deadline", [False, True])
def test_publishing_again_is_idempotent_even_after_deadline(
    api_client, teacher, course, published, expired_deadline
):
    if expired_deadline:
        published.due_at = timezone.now() - timedelta(days=1)
        published.save(update_fields=["due_at"])
    published_at = published.published_at
    api_client.force_authenticate(teacher)

    response = api_client.post(assignment_url(course, published, publish=True))

    assert response.status_code == 200
    published.refresh_from_db()
    assert published.status == "published"
    assert published.published_at == published_at
    assert Assignment.objects.count() == 1


def test_expired_draft_cannot_be_published(api_client, teacher, course, draft):
    draft.due_at = timezone.now() - timedelta(seconds=1)
    draft.save(update_fields=["due_at"])
    api_client.force_authenticate(teacher)

    response = api_client.post(assignment_url(course, draft, publish=True))

    assert response.status_code == 400
    assert "due_at" in response.json()
    draft.refresh_from_db()
    assert draft.status == "draft"
    assert draft.published_at is None


@pytest.mark.parametrize(
    "payload",
    [{"status": "published"}, {"title": "Новое название"}, {"unexpected_field": "value"}],
)
def test_publish_rejects_nonempty_payload(api_client, teacher, course, draft, payload):
    api_client.force_authenticate(teacher)

    response = api_client.post(assignment_url(course, draft, publish=True), payload, format="json")

    assert response.status_code == 400
    draft.refresh_from_db()
    assert draft.title == "Закрытый черновик"
    assert draft.status == "draft"
    assert draft.published_at is None


def test_teacher_can_edit_description_without_resubmitting_expired_deadline(
    api_client, teacher, course, published
):
    published.due_at = timezone.now() - timedelta(days=1)
    published.save(update_fields=["due_at"])
    original_due_at = published.due_at
    api_client.force_authenticate(teacher)

    response = api_client.patch(
        assignment_url(course, published), {"description": "Уточнённое условие"}, format="json"
    )

    assert response.status_code == 200
    published.refresh_from_db()
    assert published.description == "Уточнённое условие"
    assert published.due_at == original_due_at
    assert published.status == "published"


def test_teacher_can_delete_draft(api_client, teacher, course, draft):
    api_client.force_authenticate(teacher)

    response = api_client.delete(assignment_url(course, draft))

    assert response.status_code == 204
    assert not Assignment.objects.filter(pk=draft.pk).exists()


def test_published_assignment_cannot_be_deleted(api_client, teacher, course, published):
    api_client.force_authenticate(teacher)

    response = api_client.delete(assignment_url(course, published))

    assert response.status_code == 409
    published.refresh_from_db()
    assert published.status == "published"
    assert published.title == "Работа со словарями"


@pytest.mark.parametrize("max_score", [0, -1, 1001])
def test_database_rejects_out_of_range_score(course, max_score):
    with pytest.raises(IntegrityError), transaction.atomic():
        Assignment.objects.create(
            course=course, title="Задание", description="Условие", max_score=max_score
        )

    assert not Assignment.objects.exists()


@pytest.mark.parametrize(
    ("status", "has_published_at"),
    [("draft", True), ("published", False), ("unknown", False)],
)
def test_database_rejects_inconsistent_publication_state(course, status, has_published_at):
    with pytest.raises(IntegrityError), transaction.atomic():
        Assignment.objects.create(
            course=course,
            title="Задание",
            description="Условие",
            status=status,
            published_at=timezone.now() if has_published_at else None,
        )

    assert not Assignment.objects.exists()
