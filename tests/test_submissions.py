from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, Submission
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
def course(teacher):
    return Course.objects.create(title="Python", teacher=teacher)


@pytest.fixture
def student(course):
    user = User.objects.create_user(
        username="student", first_name="Анна", last_name="Соколова", email="anna@example.com"
    )
    Enrollment.objects.create(course=course, student=user)
    return user


@pytest.fixture
def peer(course):
    user = User.objects.create_user(username="peer")
    Enrollment.objects.create(course=course, student=user)
    return user


@pytest.fixture
def assignment(course):
    return Assignment.objects.create(
        course=course,
        title="Словари",
        description="Подсчитать частоту слов",
        status=Assignment.Status.PUBLISHED,
        published_at=timezone.now(),
        due_at=timezone.now() + timedelta(days=7),
    )


@pytest.fixture
def submission(assignment, student):
    return Submission.objects.create(
        assignment=assignment, student=student, answer="Первое решение"
    )


def submission_url(course, assignment, submission=None):
    kwargs = {"course_pk": course.pk, "assignment_pk": assignment.pk}
    name = "submission-list"
    if submission is not None:
        kwargs["pk"] = submission.pk
        name = "submission-detail"
    return reverse(f"assignments:{name}", kwargs=kwargs)


@pytest.mark.parametrize(
    ("method", "detail"),
    [("get", False), ("post", False), ("get", True), ("patch", True), ("delete", True)],
)
def test_submission_api_requires_authentication(
    api_client, course, assignment, submission, method, detail
):
    response = getattr(api_client, method)(
        submission_url(course, assignment, submission if detail else None),
        {"answer": "Новое решение"},
        format="json",
    )

    assert response.status_code == 401
    submission.refresh_from_db()
    assert submission.answer == "Первое решение"


@pytest.mark.parametrize(
    "payload",
    [
        {"answer": "  for word in words:\n      counts[word] += 1\n"},
        {"solution_url": "https://example.com/student/solution"},
        {"answer": "Объяснение", "solution_url": "http://example.com/student/solution"},
    ],
)
def test_student_submits_answer_or_link_and_owns_result(
    api_client, course, assignment, student, payload
):
    api_client.force_authenticate(student)
    before = timezone.now()

    response = api_client.post(submission_url(course, assignment), payload, format="json")

    assert response.status_code == 201
    saved = Submission.objects.get()
    assert saved.assignment == assignment
    assert saved.student == student
    assert saved.answer == payload.get("answer", "")
    assert saved.solution_url == payload.get("solution_url", "")
    assert before <= saved.submitted_at <= saved.updated_at <= timezone.now()
    data = response.json()
    assert set(data) == {
        "id",
        "assignment",
        "student",
        "answer",
        "solution_url",
        "submitted_at",
        "updated_at",
    }
    assert data["id"] == saved.pk
    assert data["assignment"] == assignment.pk
    assert data["student"] == {
        "id": student.pk,
        "username": student.username,
        "first_name": student.first_name,
        "last_name": student.last_name,
    }
    assert data["answer"] == saved.answer
    assert data["solution_url"] == saved.solution_url
    assert data["submitted_at"]
    assert data["updated_at"]


def test_jwt_student_needs_enrollment_and_publication_before_submitting(
    api_client, course, teacher
):
    user = User.objects.create_user(username="jwt_student", password="Willow!83-river-stone")
    draft = Assignment.objects.create(course=course, title="Черновик", description="Условие")
    login = api_client.post(
        reverse("users:token-obtain-pair"),
        {"username": user.username, "password": "Willow!83-river-stone"},
        format="json",
    )
    assert login.status_code == 200
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
    url = submission_url(course, draft)
    assert api_client.post(url, {"answer": "Ответ"}, format="json").status_code == 404
    Enrollment.objects.create(course=course, student=user)
    assert api_client.post(url, {"answer": "Ответ"}, format="json").status_code == 404

    teacher_client = APIClient()
    teacher_client.force_authenticate(teacher)
    published = teacher_client.post(
        reverse("assignments:assignment-publish", kwargs={"course_pk": course.pk, "pk": draft.pk})
    )
    assert published.status_code == 200

    response = api_client.post(url, {"answer": "Ответ"}, format="json")

    assert response.status_code == 201
    assert Submission.objects.get().student == user
    assert api_client.get(url).json()[0]["id"] == response.json()["id"]
    user.role = User.Role.TEACHER
    user.save(update_fields=["role"])
    assert api_client.get(url).status_code == 404


@pytest.mark.parametrize("method", ["put", "patch"])
def test_student_updates_own_submission_without_changing_submission_time(
    api_client, course, assignment, student, submission, method
):
    api_client.force_authenticate(student)
    submitted_at = submission.submitted_at
    previous_updated_at = submission.updated_at
    answer = "\n    return Counter(words)\n"

    response = getattr(api_client, method)(
        submission_url(course, assignment, submission),
        {"answer": answer, "solution_url": "https://example.com/revised"},
        format="json",
    )

    assert response.status_code == 200
    submission.refresh_from_db()
    assert submission.answer == answer
    assert submission.solution_url == "https://example.com/revised"
    assert submission.submitted_at == submitted_at
    assert submission.updated_at >= previous_updated_at
    assert submission.student == student
    assert submission.assignment == assignment


def test_repeated_submission_is_conflict_and_does_not_replace_answer(
    api_client, course, assignment, student, submission
):
    api_client.force_authenticate(student)

    response = api_client.post(
        submission_url(course, assignment), {"answer": "Повторная отправка"}, format="json"
    )

    assert response.status_code == 409
    assert Submission.objects.count() == 1
    submission.refresh_from_db()
    assert submission.answer == "Первое решение"


def test_teacher_reads_all_submissions_only_for_requested_assignment(
    api_client, teacher, course, assignment, student, peer, submission
):
    peer_submission = Submission.objects.create(
        assignment=assignment, student=peer, answer="Ответ 2"
    )
    second = Assignment.objects.create(course=course, title="Второе", description="Условие")
    Submission.objects.create(assignment=second, student=student, answer="Секрет другого задания")
    api_client.force_authenticate(teacher)

    response = api_client.get(submission_url(course, assignment))

    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert {item["id"] for item in response.json()} == {submission.pk, peer_submission.pk}
    assert {item["student"]["id"] for item in response.json()} == {student.pk, peer.pk}
    assert api_client.get(submission_url(course, assignment, peer_submission)).status_code == 200


@pytest.mark.parametrize("method", ["get", "put", "patch"])
def test_student_list_and_detail_do_not_expose_classmates_answers(
    api_client, course, assignment, student, peer, submission, method
):
    peer_submission = Submission.objects.create(
        assignment=assignment, student=peer, answer="Закрытое решение однокурсника"
    )
    api_client.force_authenticate(student)
    own_list = api_client.get(submission_url(course, assignment))
    assert own_list.status_code == 200
    assert [item["id"] for item in own_list.json()] == [submission.pk]
    assert api_client.get(submission_url(course, assignment, submission)).status_code == 200

    response = getattr(api_client, method)(
        submission_url(course, assignment, peer_submission), {"answer": "Подмена"}, format="json"
    )

    assert response.status_code == 404
    peer_submission.refresh_from_db()
    assert peer_submission.answer == "Закрытое решение однокурсника"


@pytest.mark.parametrize("role", ["teacher", "student", "staff_student", "superuser_student"])
@pytest.mark.parametrize("operation", ["list", "create", "detail", "update"])
def test_outsiders_cannot_discover_or_change_submissions(
    api_client, course, assignment, submission, role, operation
):
    outsider = User.objects.create_user(
        username="outsider",
        role=User.Role.TEACHER if role == "teacher" else User.Role.STUDENT,
        is_staff=role == "staff_student",
        is_superuser=role == "superuser_student",
    )
    api_client.force_authenticate(outsider)
    method = {"list": "get", "create": "post", "detail": "get", "update": "patch"}[operation]
    target = submission if operation in {"detail", "update"} else None

    response = getattr(api_client, method)(
        submission_url(course, assignment, target), {"answer": "Подмена"}, format="json"
    )

    assert response.status_code == 404
    assert Submission.objects.count() == 1
    submission.refresh_from_db()
    assert submission.answer == "Первое решение"


@pytest.mark.parametrize("method", ["post", "put", "patch"])
def test_owning_teacher_cannot_submit_or_edit_a_students_solution(
    api_client, teacher, course, assignment, submission, method
):
    api_client.force_authenticate(teacher)

    response = getattr(api_client, method)(
        submission_url(course, assignment, None if method == "post" else submission),
        {"answer": "Подмена преподавателем"},
        format="json",
    )

    assert response.status_code == 403
    submission.refresh_from_db()
    assert submission.answer == "Первое решение"
    assert Submission.objects.count() == 1


def test_draft_submission_list_is_visible_only_to_course_teacher(
    api_client, teacher, student, course
):
    draft = Assignment.objects.create(course=course, title="Черновик", description="Условие")
    url = submission_url(course, draft)
    api_client.force_authenticate(teacher)
    response = api_client.get(url)
    assert response.status_code == 200
    assert response.json() == []
    api_client.force_authenticate(student)
    assert api_client.get(url).status_code == 404
    assert api_client.post(url, {"answer": "Ответ"}, format="json").status_code == 404
    assert not Submission.objects.exists()


@pytest.mark.parametrize("mismatch", ["course", "assignment"])
@pytest.mark.parametrize("method", ["get", "patch"])
def test_submission_cannot_be_addressed_through_another_parent(
    api_client, teacher, student, course, assignment, submission, mismatch, method
):
    second_course = Course.objects.create(title="Второй курс", teacher=teacher)
    Enrollment.objects.create(course=second_course, student=student)
    second_assignment = Assignment.objects.create(
        course=course,
        title="Второе задание",
        description="Условие",
        status=Assignment.Status.PUBLISHED,
        published_at=timezone.now(),
    )
    target_course = second_course if mismatch == "course" else course
    target_assignment = assignment if mismatch == "course" else second_assignment
    api_client.force_authenticate(student)

    response = getattr(api_client, method)(
        submission_url(target_course, target_assignment, submission),
        {"answer": "Подмена"},
        format="json",
    )

    assert response.status_code == 404
    if mismatch == "course":
        create_response = api_client.post(
            submission_url(target_course, target_assignment), {"answer": "Ответ"}, format="json"
        )
        assert create_response.status_code == 404
    submission.refresh_from_db()
    assert submission.assignment == assignment
    assert submission.answer == "Первое решение"


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("assignment", 99999),
        ("assignment_id", 99999),
        ("student", 99999),
        ("student_id", 99999),
        ("id", 99999),
        ("status", "graded"),
        ("submitted_at", "2026-01-01T00:00:00Z"),
        ("updated_at", "2026-01-01T00:00:00Z"),
        ("unexpected", "value"),
    ],
)
def test_submission_rejects_ownership_and_read_only_field_overrides(
    api_client, course, assignment, student, submission, method, field, value
):
    api_client.force_authenticate(student)
    if method == "post":
        submission.delete()
    url = submission_url(course, assignment, None if method == "post" else submission)

    response = getattr(api_client, method)(
        url, {"answer": "Новый ответ", field: value}, format="json"
    )

    assert response.status_code == 400
    assert field in response.json()
    if method == "post":
        assert not Submission.objects.exists()
    else:
        submission.refresh_from_db()
        assert submission.student == student
        assert submission.assignment == assignment
        assert submission.answer == "Первое решение"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"answer": ""},
        {"answer": " \t\n "},
        {"solution_url": ""},
        {"answer": " ", "solution_url": " "},
        {"answer": None},
        {"solution_url": None},
        {"answer": "x" * 20001},
        {"solution_url": "https://example.com/" + "a" * 481},
        {"solution_url": "not a URL"},
        {"solution_url": "ftp://example.com/solution"},
        {"solution_url": "javascript:alert(1)"},
    ],
)
def test_submission_rejects_empty_invalid_or_oversized_content(
    api_client, course, assignment, student, payload
):
    api_client.force_authenticate(student)

    response = api_client.post(submission_url(course, assignment), payload, format="json")

    assert response.status_code == 400
    assert not Submission.objects.exists()


def test_submission_accepts_maximum_content_lengths(api_client, course, assignment, student):
    api_client.force_authenticate(student)
    payload = {"answer": "x" * 20000, "solution_url": "https://example.com/" + "a" * 480}
    assert len(payload["solution_url"]) == 500

    response = api_client.post(submission_url(course, assignment), payload, format="json")

    assert response.status_code == 201
    saved = Submission.objects.get()
    assert saved.answer == payload["answer"]
    assert saved.solution_url == payload["solution_url"]


@pytest.mark.parametrize("clear_field", ["answer", "solution_url"])
def test_patch_can_clear_one_field_when_other_existing_field_has_content(
    api_client, course, assignment, student, submission, clear_field
):
    submission.solution_url = "https://example.com/solution"
    submission.save(update_fields=["solution_url"])
    api_client.force_authenticate(student)

    response = api_client.patch(
        submission_url(course, assignment, submission), {clear_field: ""}, format="json"
    )

    assert response.status_code == 200
    submission.refresh_from_db()
    assert getattr(submission, clear_field) == ""
    remaining_field = "solution_url" if clear_field == "answer" else "answer"
    assert getattr(submission, remaining_field)


@pytest.mark.parametrize(
    "payload", [{"answer": ""}, {"answer": " \n "}, {"answer": "", "solution_url": ""}]
)
def test_patch_cannot_remove_all_submission_content(
    api_client, course, assignment, student, submission, payload
):
    api_client.force_authenticate(student)

    response = api_client.patch(
        submission_url(course, assignment, submission), payload, format="json"
    )

    assert response.status_code == 400
    submission.refresh_from_db()
    assert submission.answer == "Первое решение"
    assert submission.solution_url == ""


@pytest.mark.parametrize("seconds_until_deadline", [-1, 0])
@pytest.mark.parametrize("method", ["post", "put", "patch"])
def test_closed_deadline_rejects_create_and_edit_without_changing_data(
    api_client, course, assignment, student, submission, seconds_until_deadline, method
):
    now = timezone.now()
    assignment.due_at = now + timedelta(seconds=seconds_until_deadline)
    assignment.save(update_fields=["due_at"])
    api_client.force_authenticate(student)
    previous_updated_at = submission.updated_at
    if method == "post":
        submission.delete()
    url = submission_url(course, assignment, None if method == "post" else submission)

    with patch("django.utils.timezone.now", return_value=now):
        response = getattr(api_client, method)(url, {"answer": "Опоздание"}, format="json")

    assert response.status_code == 400
    assert "due_at" in response.json()
    if method == "post":
        assert not Submission.objects.exists()
    else:
        submission.refresh_from_db()
        assert submission.answer == "Первое решение"
        assert submission.updated_at == previous_updated_at


@pytest.mark.parametrize("method", ["post", "patch"])
def test_missing_deadline_allows_submission_and_revision(
    api_client, course, assignment, student, submission, method
):
    assignment.due_at = None
    assignment.save(update_fields=["due_at"])
    api_client.force_authenticate(student)
    if method == "post":
        submission.delete()
    url = submission_url(course, assignment, None if method == "post" else submission)

    response = getattr(api_client, method)(url, {"answer": "Без срока сдачи"}, format="json")

    assert response.status_code == (201 if method == "post" else 200)
    assert Submission.objects.get().answer == "Без срока сдачи"


def test_student_and_teacher_can_read_submitted_work_after_deadline(
    api_client, teacher, course, assignment, student, submission
):
    assignment.due_at = timezone.now() - timedelta(days=1)
    assignment.save(update_fields=["due_at"])

    for user in (student, teacher):
        api_client.force_authenticate(user)
        response = api_client.get(submission_url(course, assignment, submission))
        assert response.status_code == 200
        assert response.json()["answer"] == "Первое решение"
        assert [
            item["id"] for item in api_client.get(submission_url(course, assignment)).json()
        ] == [submission.pk]


def test_removing_enrollment_revokes_access_but_keeps_work_for_teacher(
    api_client, teacher, course, assignment, student, submission
):
    Enrollment.objects.filter(course=course, student=student).delete()
    api_client.force_authenticate(student)
    assert api_client.get(submission_url(course, assignment)).status_code == 404
    assert api_client.get(submission_url(course, assignment, submission)).status_code == 404
    assert (
        api_client.patch(
            submission_url(course, assignment, submission), {"answer": "Изменение"}, format="json"
        ).status_code
        == 404
    )
    assert Submission.objects.filter(pk=submission.pk).exists()

    api_client.force_authenticate(teacher)
    assert api_client.get(submission_url(course, assignment, submission)).status_code == 200


@pytest.mark.parametrize("actor", ["student", "teacher"])
def test_submission_cannot_be_deleted_through_api(
    api_client, teacher, course, assignment, student, submission, actor
):
    api_client.force_authenticate(student if actor == "student" else teacher)

    response = api_client.delete(submission_url(course, assignment, submission))

    assert response.status_code == 405
    assert Submission.objects.filter(pk=submission.pk).exists()


def test_database_enforces_one_submission_per_student_and_assignment(
    course, assignment, student, peer, submission
):
    with pytest.raises(IntegrityError), transaction.atomic():
        Submission.objects.create(assignment=assignment, student=student, answer="Дубликат")
    assert Submission.objects.count() == 1
    Submission.objects.create(assignment=assignment, student=peer, answer="Другой студент")
    second = Assignment.objects.create(course=course, title="Второе задание", description="Условие")
    Submission.objects.create(assignment=second, student=student, answer="Другое задание")
    assert Submission.objects.count() == 3


def test_database_rejects_submission_without_answer_and_link(assignment, student):
    with pytest.raises(IntegrityError), transaction.atomic():
        Submission.objects.create(
            assignment=assignment, student=student, answer="", solution_url=""
        )

    assert not Submission.objects.exists()


def test_student_deletion_cannot_remove_submitted_work(student, submission):
    with pytest.raises(ProtectedError):
        student.delete()

    assert User.objects.filter(pk=student.pk).exists()
    assert Submission.objects.filter(pk=submission.pk).exists()
