from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, Grade, Submission
from courses.models import Course, Enrollment
from users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def teacher():
    return User.objects.create_user(
        username="teacher", role=User.Role.TEACHER, first_name="Иван", last_name="Орлов"
    )


@pytest.fixture
def course(teacher):
    return Course.objects.create(title="Python", teacher=teacher)


@pytest.fixture
def student(course):
    user = User.objects.create_user(username="student")
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
        max_score=100,
    )


@pytest.fixture
def submission(assignment, student):
    return Submission.objects.create(
        assignment=assignment,
        student=student,
        answer="Первое решение",
        solution_url="https://example.com/solution",
    )


@pytest.fixture
def grade(submission, teacher):
    return Grade.objects.create(
        submission=submission,
        score=75,
        feedback="Добавить обработку пустого ввода.",
        graded_by=teacher,
    )


def submission_url(course, assignment, submission=None, *, grade=False):
    kwargs = {"course_pk": course.pk, "assignment_pk": assignment.pk}
    name = "submission-list"
    if submission is not None:
        kwargs["pk"] = submission.pk
        name = "submission-grade" if grade else "submission-detail"
    return reverse(f"assignments:{name}", kwargs=kwargs)


def submission_state(submission):
    submission.refresh_from_db()
    return (
        submission.assignment_id,
        submission.student_id,
        submission.answer,
        submission.solution_url,
        submission.submitted_at,
        submission.updated_at,
    )


def grade_state(grade):
    grade.refresh_from_db()
    return grade.score, grade.feedback, grade.graded_by_id, grade.graded_at


def test_grading_requires_authentication(api_client, course, assignment, submission):
    response = api_client.post(
        submission_url(course, assignment, submission, grade=True), {"score": 50}, format="json"
    )

    assert response.status_code == 401
    assert not Grade.objects.exists()


@pytest.mark.parametrize("score", [0, 50, 100])
def test_owner_grades_submission_without_changing_its_content_or_dates(
    api_client, teacher, course, assignment, submission, score
):
    api_client.force_authenticate(teacher)
    previous = submission_state(submission)
    before = timezone.now()

    response = api_client.post(
        submission_url(course, assignment, submission, grade=True),
        {"score": score, "feedback": "Проверены граничные случаи."},
        format="json",
    )

    assert response.status_code == 200
    saved = Grade.objects.get()
    assert saved.submission == submission
    assert saved.score == score
    assert saved.feedback == "Проверены граничные случаи."
    assert saved.graded_by == teacher
    assert before <= saved.graded_at <= timezone.now()
    assert submission_state(submission) == previous
    data = response.json()
    assert data["id"] == submission.pk
    assert data["status"] == "graded"
    assert data["answer"] == submission.answer
    assert data["solution_url"] == submission.solution_url
    assert data["grade"] == {
        "id": saved.pk,
        "score": score,
        "feedback": saved.feedback,
        "graded_by": {
            "id": teacher.pk,
            "username": teacher.username,
            "first_name": teacher.first_name,
            "last_name": teacher.last_name,
        },
        "graded_at": data["grade"]["graded_at"],
    }
    assert data["grade"]["graded_at"]


@pytest.mark.parametrize("feedback", [None, "Исправлены все замечания."])
def test_regrading_overwrites_one_grade_and_refreshes_date(
    api_client, teacher, course, assignment, submission, grade, feedback
):
    previous = submission_state(submission)
    old_time = timezone.now() - timedelta(days=1)
    Grade.objects.filter(pk=grade.pk).update(graded_at=old_time)
    payload = {"score": 90}
    if feedback is not None:
        payload["feedback"] = feedback
    api_client.force_authenticate(teacher)

    response = api_client.post(
        submission_url(course, assignment, submission, grade=True), payload, format="json"
    )

    assert response.status_code == 200
    assert Grade.objects.count() == 1
    grade.refresh_from_db()
    assert grade.score == 90
    assert grade.feedback == (feedback or "")
    assert grade.graded_by == teacher
    assert grade.graded_at > old_time
    assert response.json()["grade"]["id"] == grade.pk
    assert response.json()["grade"]["score"] == 90
    assert response.json()["grade"]["feedback"] == (feedback or "")
    assert submission_state(submission) == previous


@pytest.mark.parametrize("already_graded", [False, True])
def test_teacher_can_grade_and_regrade_after_deadline(
    api_client, teacher, course, assignment, submission, already_graded
):
    assignment.due_at = timezone.now() - timedelta(days=1)
    assignment.save(update_fields=["due_at"])
    if already_graded:
        Grade.objects.create(submission=submission, score=50, graded_by=teacher)
    api_client.force_authenticate(teacher)

    response = api_client.post(
        submission_url(course, assignment, submission, grade=True), {"score": 95}, format="json"
    )

    assert response.status_code == 200
    assert Grade.objects.get().score == 95


@pytest.mark.parametrize(
    ("identity", "expected_status"),
    [
        ("author", 403),
        ("staff_author", 403),
        ("superuser_author", 403),
        ("peer", 404),
        ("teacher", 404),
        ("student", 404),
        ("superuser_teacher", 404),
    ],
)
def test_only_course_teacher_can_grade_and_denied_requests_do_not_reveal_feedback(
    api_client, course, assignment, student, peer, submission, grade, identity, expected_status
):
    if identity in {"author", "staff_author", "superuser_author"}:
        actor = student
        actor.is_staff = identity == "staff_author"
        actor.is_superuser = identity == "superuser_author"
        actor.save(update_fields=["is_staff", "is_superuser"])
    elif identity == "peer":
        actor = peer
    else:
        actor = User.objects.create_user(
            username="outsider",
            role=User.Role.TEACHER if "teacher" in identity else User.Role.STUDENT,
            is_superuser=identity == "superuser_teacher",
        )
    previous_grade = grade_state(grade)
    previous_submission = submission_state(submission)
    api_client.force_authenticate(actor)

    response = api_client.post(
        submission_url(course, assignment, submission, grade=True),
        {"score": 100, "feedback": "Подмена"},
        format="json",
    )

    assert response.status_code == expected_status
    assert grade.feedback.encode() not in response.content
    assert grade_state(grade) == previous_grade
    assert submission_state(submission) == previous_submission


@pytest.mark.parametrize("mismatch", ["course", "assignment"])
def test_grading_requires_matching_course_and_assignment_parents(
    api_client, teacher, course, assignment, submission, mismatch
):
    second_course = Course.objects.create(title="SQL", teacher=teacher)
    second_assignment = Assignment.objects.create(
        course=course, title="Второе", description="Условие"
    )
    api_client.force_authenticate(teacher)
    target_course = second_course if mismatch == "course" else course
    target_assignment = assignment if mismatch == "course" else second_assignment

    response = api_client.post(
        submission_url(target_course, target_assignment, submission, grade=True),
        {"score": 100},
        format="json",
    )

    assert response.status_code == 404
    assert not Grade.objects.exists()


@pytest.mark.parametrize("method", ["get", "put", "patch", "delete"])
def test_grade_action_only_accepts_post(
    api_client, teacher, course, assignment, submission, method
):
    api_client.force_authenticate(teacher)

    response = getattr(api_client, method)(
        submission_url(course, assignment, submission, grade=True), {"score": 50}, format="json"
    )

    assert response.status_code == 405
    assert not Grade.objects.exists()


@pytest.mark.parametrize("score", [-1, 101, 1001, 1.5, None, "invalid"])
def test_invalid_score_does_not_replace_existing_grade(
    api_client, teacher, course, assignment, submission, grade, score
):
    api_client.force_authenticate(teacher)
    previous = grade_state(grade)

    response = api_client.post(
        submission_url(course, assignment, submission, grade=True),
        {"score": score, "feedback": "Подмена"},
        format="json",
    )

    assert response.status_code == 400
    assert "score" in response.json()
    assert grade_state(grade) == previous


def test_regrade_requires_score(api_client, teacher, course, assignment, submission, grade):
    api_client.force_authenticate(teacher)
    previous = grade_state(grade)

    response = api_client.post(
        submission_url(course, assignment, submission, grade=True),
        {"feedback": "Без балла"},
        format="json",
    )

    assert response.status_code == 400
    assert "score" in response.json()
    assert grade_state(grade) == previous


@pytest.mark.parametrize("feedback", [None, "x" * 5001])
def test_invalid_feedback_does_not_create_grade(
    api_client, teacher, course, assignment, submission, feedback
):
    api_client.force_authenticate(teacher)

    response = api_client.post(
        submission_url(course, assignment, submission, grade=True),
        {"score": 50, "feedback": feedback},
        format="json",
    )

    assert response.status_code == 400
    assert "feedback" in response.json()
    assert not Grade.objects.exists()


def test_grade_accepts_maximum_score_and_feedback_length(
    api_client, teacher, course, assignment, submission
):
    assignment.max_score = 1000
    assignment.save(update_fields=["max_score"])
    api_client.force_authenticate(teacher)

    response = api_client.post(
        submission_url(course, assignment, submission, grade=True),
        {"score": 1000, "feedback": "x" * 5000},
        format="json",
    )

    assert response.status_code == 200
    assert Grade.objects.get().score == 1000
    assert Grade.objects.get().feedback == "x" * 5000


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", 99999),
        ("student", 99999),
        ("submission", 99999),
        ("submission_id", 99999),
        ("graded_by", 99999),
        ("graded_by_id", 99999),
        ("graded_at", "2026-01-01T00:00:00Z"),
        ("status", "graded"),
        ("unexpected", "value"),
    ],
)
def test_grade_rejects_read_only_and_unknown_fields(
    api_client, teacher, course, assignment, submission, field, value
):
    api_client.force_authenticate(teacher)

    response = api_client.post(
        submission_url(course, assignment, submission, grade=True),
        {"score": 50, field: value},
        format="json",
    )

    assert response.status_code == 400
    assert field in response.json()
    assert not Grade.objects.exists()


@pytest.mark.parametrize("method", ["put", "patch"])
@pytest.mark.parametrize("expired", [False, True])
def test_student_cannot_edit_graded_submission_even_after_deadline(
    api_client, student, course, assignment, submission, grade, method, expired
):
    if expired:
        assignment.due_at = timezone.now() - timedelta(days=1)
        assignment.save(update_fields=["due_at"])
    api_client.force_authenticate(student)
    previous_submission = submission_state(submission)
    previous_grade = grade_state(grade)

    response = getattr(api_client, method)(
        submission_url(course, assignment, submission),
        {"answer": "Изменённый ответ", "solution_url": "https://example.com/revised"},
        format="json",
    )

    assert response.status_code == 409
    assert submission_state(submission) == previous_submission
    assert grade_state(grade) == previous_grade


@pytest.mark.parametrize(("field", "value"), [("grade", {"score": 100}), ("status", "graded")])
def test_student_cannot_supply_grading_fields_when_updating_ungraded_submission(
    api_client, student, course, assignment, submission, field, value
):
    api_client.force_authenticate(student)
    previous = submission_state(submission)

    response = api_client.patch(
        submission_url(course, assignment, submission),
        {"answer": "Подмена", field: value},
        format="json",
    )

    assert response.status_code == 400
    assert field in response.json()
    assert submission_state(submission) == previous
    assert not Grade.objects.exists()


def test_ungraded_submission_has_submitted_status_and_remains_editable(
    api_client, student, course, assignment, submission
):
    api_client.force_authenticate(student)
    url = submission_url(course, assignment, submission)

    detail = api_client.get(url)
    assert detail.status_code == 200
    assert detail.json()["status"] == "submitted"
    assert detail.json()["grade"] is None
    listing = api_client.get(submission_url(course, assignment))
    assert listing.status_code == 200
    assert listing.json()["results"][0]["status"] == "submitted"
    assert listing.json()["results"][0]["grade"] is None

    response = api_client.patch(url, {"answer": "Исправленное решение"}, format="json")

    assert response.status_code == 200
    assert response.json()["status"] == "submitted"
    assert response.json()["grade"] is None
    submission.refresh_from_db()
    assert submission.answer == "Исправленное решение"
    assert not Grade.objects.exists()


def test_student_reads_only_own_grade_while_teacher_reads_all_course_grades(
    api_client, teacher, student, peer, course, assignment, submission, grade
):
    peer_submission = Submission.objects.create(
        assignment=assignment, student=peer, answer="Ответ 2"
    )
    peer_grade = Grade.objects.create(
        submission=peer_submission, score=90, feedback="Закрытый комментарий", graded_by=teacher
    )
    api_client.force_authenticate(student)
    response = api_client.get(submission_url(course, assignment))
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["results"]] == [submission.pk]
    assert response.json()["results"][0]["grade"]["score"] == grade.score
    own_detail = api_client.get(submission_url(course, assignment, submission))
    assert own_detail.status_code == 200
    assert own_detail.json()["grade"]["feedback"] == grade.feedback
    forbidden = api_client.get(submission_url(course, assignment, peer_submission))
    assert forbidden.status_code == 404
    assert peer_grade.feedback.encode() not in forbidden.content

    api_client.force_authenticate(teacher)
    teacher_list = api_client.get(submission_url(course, assignment))
    assert teacher_list.status_code == 200
    assert {item["grade"]["id"] for item in teacher_list.json()["results"]} == {
        grade.pk,
        peer_grade.pk,
    }
    assert all(item["status"] == "graded" for item in teacher_list.json()["results"])


@pytest.mark.parametrize("removed_access", ["enrollment", "publication"])
def test_previous_author_cannot_read_grade_after_losing_assignment_access(
    api_client, student, course, assignment, submission, grade, removed_access
):
    if removed_access == "enrollment":
        Enrollment.objects.filter(course=course, student=student).delete()
    else:
        assignment.status = Assignment.Status.DRAFT
        assignment.published_at = None
        assignment.save(update_fields=["status", "published_at"])
    api_client.force_authenticate(student)

    response = api_client.get(submission_url(course, assignment, submission))

    assert response.status_code == 404
    assert grade.feedback.encode() not in response.content


@pytest.mark.parametrize(("max_score", "expected_status"), [(84, 400), (85, 200), (120, 200)])
def test_assignment_maximum_cannot_be_less_than_its_highest_grade(
    api_client, teacher, student, peer, course, assignment, submission, max_score, expected_status
):
    Grade.objects.create(submission=submission, score=40, graded_by=teacher)
    other_submission = Submission.objects.create(
        assignment=assignment, student=peer, answer="Ответ 2"
    )
    Grade.objects.create(submission=other_submission, score=85, graded_by=teacher)
    unrelated_assignment = Assignment.objects.create(
        course=course, title="Другое", description="Условие", max_score=1000
    )
    unrelated_submission = Submission.objects.create(
        assignment=unrelated_assignment, student=student, answer="Ответ 3"
    )
    Grade.objects.create(submission=unrelated_submission, score=1000, graded_by=teacher)
    api_client.force_authenticate(teacher)
    previous_updated_at = assignment.updated_at

    response = api_client.patch(
        reverse(
            "assignments:assignment-detail", kwargs={"course_pk": course.pk, "pk": assignment.pk}
        ),
        {"max_score": max_score},
        format="json",
    )

    assert response.status_code == expected_status
    assignment.refresh_from_db()
    if expected_status == 400:
        assert "max_score" in response.json()
        assert assignment.max_score == 100
        assert assignment.updated_at == previous_updated_at
    else:
        assert assignment.max_score == max_score
    assert set(Grade.objects.values_list("score", flat=True)) == {40, 85, 1000}


def test_lowering_a_grade_allows_lowering_assignment_maximum(
    api_client, teacher, course, assignment, submission, grade
):
    api_client.force_authenticate(teacher)
    grading = api_client.post(
        submission_url(course, assignment, submission, grade=True), {"score": 30}, format="json"
    )
    assert grading.status_code == 200

    response = api_client.patch(
        reverse(
            "assignments:assignment-detail", kwargs={"course_pk": course.pk, "pk": assignment.pk}
        ),
        {"max_score": 30},
        format="json",
    )

    assert response.status_code == 200
    assignment.refresh_from_db()
    assert assignment.max_score == 30
    assert Grade.objects.get().score == 30


@pytest.mark.parametrize("score", [-1, 1001])
def test_database_rejects_out_of_range_scores(submission, teacher, score):
    with pytest.raises(IntegrityError), transaction.atomic():
        Grade.objects.create(submission=submission, score=score, graded_by=teacher)

    assert not Grade.objects.exists()


def test_database_rejects_duplicate_grade_for_submission(submission, teacher, grade):
    previous = grade_state(grade)

    with pytest.raises(IntegrityError), transaction.atomic():
        Grade.objects.create(submission=submission, score=100, graded_by=teacher)

    assert Grade.objects.count() == 1
    assert grade_state(grade) == previous


def test_grader_cannot_be_deleted_while_grade_exists(submission):
    grader = User.objects.create_user(username="former_teacher", role=User.Role.TEACHER)
    Grade.objects.create(submission=submission, score=50, graded_by=grader)

    with pytest.raises(ProtectedError):
        grader.delete()

    assert User.objects.filter(pk=grader.pk).exists()
    assert Grade.objects.get().graded_by == grader


def test_existing_jwt_does_not_preserve_teacher_permissions_after_role_change(
    api_client, teacher, course, assignment, submission
):
    password = "Willow!83-river-stone"
    teacher.set_password(password)
    teacher.save(update_fields=["password"])
    login = api_client.post(
        reverse("users:token-obtain-pair"),
        {"username": teacher.username, "password": password},
        format="json",
    )
    assert login.status_code == 200
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
    url = submission_url(course, assignment, submission, grade=True)
    assert api_client.post(url, {"score": 75}, format="json").status_code == 200
    existing = Grade.objects.get()
    previous = grade_state(existing)
    teacher.role = User.Role.STUDENT
    teacher.save(update_fields=["role"])

    response = api_client.post(url, {"score": 100}, format="json")

    assert response.status_code == 404
    assert grade_state(existing) == previous
