from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
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
    return User.objects.create_user(username="teacher", role=User.Role.TEACHER)


@pytest.fixture
def course(teacher):
    return Course.objects.create(title="Python", teacher=teacher)


@pytest.fixture
def student(course):
    user = User.objects.create_user(
        username="student", email="private@example.com", first_name="Иван", last_name="Петров"
    )
    Enrollment.objects.create(course=course, student=user)
    return user


@pytest.fixture
def peer(course):
    user = User.objects.create_user(username="peer")
    Enrollment.objects.create(course=course, student=user)
    return user


def create_assignment(course, **overrides):
    fields = {
        "course": course,
        "title": "Словари",
        "description": "Подсчитать частоту слов",
        "status": Assignment.Status.PUBLISHED,
        "published_at": timezone.now(),
        "due_at": timezone.now() + timedelta(days=7),
        "max_score": 100,
    }
    fields.update(overrides)
    if fields["status"] == Assignment.Status.DRAFT:
        fields["published_at"] = None
    return Assignment.objects.create(**fields)


def submit(assignment, student, *, score=None):
    submission = Submission.objects.create(assignment=assignment, student=student, answer="Решение")
    if score is not None:
        Grade.objects.create(
            submission=submission, score=score, graded_by=assignment.course.teacher
        )
    return submission


def progress_url(course):
    return reverse("courses:course-progress", kwargs={"pk": course.pk})


def progress_rows(api_client, course):
    response = api_client.get(progress_url(course))
    assert response.status_code == 200, response.content
    return response.json()["results"]


def test_progress_requires_authentication(api_client, course):
    response = api_client.get(progress_url(course))

    assert response.status_code == 401


def test_progress_calculates_partial_completion_and_zero_grade(api_client, course, student):
    first = create_assignment(course, max_score=10, due_at=timezone.now() - timedelta(days=1))
    second = create_assignment(course, max_score=20)
    create_assignment(course, max_score=30, due_at=timezone.now() - timedelta(days=1))
    submit(first, student, score=0)
    submit(second, student)
    api_client.force_authenticate(student)

    rows = progress_rows(api_client, course)

    assert rows == [
        {
            "student": {
                "id": student.pk,
                "username": "student",
                "first_name": "Иван",
                "last_name": "Петров",
            },
            "assignments_count": 3,
            "submitted_count": 2,
            "graded_count": 1,
            "overdue_count": 1,
            "earned_score": 0,
            "max_score": 60,
            "completion_percent": 66.67,
        }
    ]


def test_progress_counts_equal_scores_separately_and_full_completion(api_client, course, student):
    for _ in range(3):
        assignment = create_assignment(
            course, max_score=20, due_at=timezone.now() - timedelta(days=1)
        )
        submit(assignment, student, score=15)
    api_client.force_authenticate(student)

    row = progress_rows(api_client, course)[0]

    assert row["assignments_count"] == 3
    assert row["submitted_count"] == 3
    assert row["graded_count"] == 3
    assert row["earned_score"] == 45
    assert row["max_score"] == 60
    assert row["overdue_count"] == 0
    assert row["completion_percent"] == 100.0


def test_progress_without_assignments_is_zero(api_client, course, student):
    api_client.force_authenticate(student)

    row = progress_rows(api_client, course)[0]

    assert all(value == 0 for field, value in row.items() if field != "student")


def test_progress_without_submissions_counts_only_passed_deadlines(api_client, course, student):
    create_assignment(course, due_at=timezone.now() - timedelta(days=1))
    create_assignment(course, due_at=timezone.now() + timedelta(days=1))
    create_assignment(course, due_at=None)
    api_client.force_authenticate(student)

    row = progress_rows(api_client, course)[0]

    assert row["assignments_count"] == 3
    assert row["max_score"] == 300
    assert row["overdue_count"] == 1
    assert row["submitted_count"] == row["graded_count"] == row["earned_score"] == 0
    assert row["completion_percent"] == 0.0


def test_deadline_equal_to_now_is_overdue(api_client, course, student, monkeypatch):
    now = timezone.now()
    create_assignment(course, due_at=now)
    monkeypatch.setattr("courses.progress.timezone.now", lambda: now)
    api_client.force_authenticate(student)

    assert progress_rows(api_client, course)[0]["overdue_count"] == 1


def test_progress_ignores_drafts_and_other_courses(api_client, course, student, teacher):
    current = create_assignment(course, max_score=10)
    draft = create_assignment(
        course,
        status=Assignment.Status.DRAFT,
        due_at=timezone.now() - timedelta(days=1),
        max_score=80,
    )
    other_course = Course.objects.create(title="SQL", teacher=teacher)
    Enrollment.objects.create(course=other_course, student=student)
    foreign = create_assignment(
        other_course, due_at=timezone.now() - timedelta(days=1), max_score=90
    )
    create_assignment(course, status=Assignment.Status.DRAFT)
    create_assignment(other_course, due_at=timezone.now() - timedelta(days=1))
    submit(current, student, score=7)
    submit(draft, student, score=80)
    submit(foreign, student, score=90)
    api_client.force_authenticate(student)

    row = progress_rows(api_client, course)[0]

    assert row["assignments_count"] == row["submitted_count"] == row["graded_count"] == 1
    assert row["earned_score"] == 7
    assert row["max_score"] == 10
    assert row["overdue_count"] == 0
    assert row["completion_percent"] == 100.0


def test_teacher_sees_each_enrolled_students_own_totals(api_client, teacher, course, student, peer):
    assignment = create_assignment(course, due_at=timezone.now() - timedelta(days=1))
    submit(assignment, student, score=85)
    api_client.force_authenticate(teacher)

    rows = progress_rows(api_client, course)

    assert [row["student"]["id"] for row in rows] == [student.pk, peer.pk]
    assert rows[0]["earned_score"] == 85
    assert rows[0]["submitted_count"] == 1
    assert rows[0]["overdue_count"] == 0
    assert rows[1]["earned_score"] == 0
    assert rows[1]["submitted_count"] == 0
    assert rows[1]["overdue_count"] == 1


@pytest.mark.parametrize("privileged", [False, True])
def test_student_sees_only_own_progress(api_client, course, student, peer, privileged):
    student.is_staff = student.is_superuser = privileged
    student.save(update_fields=("is_staff", "is_superuser"))
    assignment = create_assignment(course)
    submit(assignment, peer, score=100)
    api_client.force_authenticate(student)

    response = api_client.get(progress_url(course))

    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert [row["student"]["id"] for row in data["results"]] == [student.pk]
    assert data["results"][0]["submitted_count"] == 0
    assert data["results"][0]["earned_score"] == 0


@pytest.mark.parametrize(
    ("role", "privileged"),
    [
        (User.Role.STUDENT, False),
        (User.Role.TEACHER, False),
        (User.Role.STUDENT, True),
        (User.Role.TEACHER, True),
    ],
)
def test_outsiders_cannot_read_course_progress(api_client, course, student, role, privileged):
    outsider = User.objects.create_user(
        username="outsider", role=role, is_staff=privileged, is_superuser=privileged
    )
    api_client.force_authenticate(outsider)

    response = api_client.get(progress_url(course))

    assert response.status_code == 404
    assert "results" not in response.json()


def test_removed_enrollment_disappears_without_deleting_work(api_client, teacher, course, student):
    assignment = create_assignment(course)
    submission = submit(assignment, student, score=70)
    Enrollment.objects.filter(course=course, student=student).delete()
    api_client.force_authenticate(teacher)

    assert progress_rows(api_client, course) == []
    assert Submission.objects.filter(pk=submission.pk).exists()
    assert Grade.objects.filter(submission=submission, score=70).exists()

    api_client.force_authenticate(student)
    assert api_client.get(progress_url(course)).status_code == 404


def test_course_owner_sees_empty_roster(api_client, teacher, course):
    create_assignment(course)
    api_client.force_authenticate(teacher)

    response = api_client.get(progress_url(course))

    assert response.status_code == 200
    assert response.json() == {"count": 0, "next": None, "previous": None, "results": []}


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_progress_endpoint_is_read_only(api_client, teacher, course, student, method):
    api_client.force_authenticate(teacher)

    response = getattr(api_client, method)(
        progress_url(course), {"earned_score": 100}, format="json"
    )

    assert response.status_code == 405


def add_students(course, count):
    students = User.objects.bulk_create([User(username=f"extra-{index}") for index in range(count)])
    Enrollment.objects.bulk_create(
        [Enrollment(course=course, student=student) for student in students]
    )
    return students


def test_progress_paginates_in_stable_order(api_client, teacher, course):
    students = add_students(course, 23)
    api_client.force_authenticate(teacher)

    first = api_client.get(progress_url(course))
    second = api_client.get(progress_url(course), {"page": 2})

    assert first.status_code == second.status_code == 200
    first_data, second_data = first.json(), second.json()
    assert first_data["count"] == second_data["count"] == 23
    assert first_data["next"]
    assert first_data["previous"] is None
    assert second_data["previous"]
    assert second_data["next"] is None
    assert len(first_data["results"]) == 20
    assert len(second_data["results"]) == 3
    ids = [row["student"]["id"] for row in first_data["results"] + second_data["results"]]
    assert ids == [student.pk for student in students]


@pytest.mark.parametrize(("page_size", "expected"), [(5, 5), (1000, 100)])
def test_progress_limits_custom_page_size(api_client, teacher, course, page_size, expected):
    add_students(course, 105)
    api_client.force_authenticate(teacher)

    response = api_client.get(progress_url(course), {"page_size": page_size})

    assert response.status_code == 200
    assert response.json()["count"] == 105
    assert len(response.json()["results"]) == expected


def test_progress_queries_do_not_scale_with_roster(api_client, teacher, course, student, peer):
    assignment = create_assignment(course)
    submit(assignment, student, score=50)
    api_client.force_authenticate(teacher)

    with CaptureQueriesContext(connection) as small_queries:
        small = api_client.get(progress_url(course))

    students = add_students(course, 35)
    Submission.objects.bulk_create(
        [Submission(assignment=assignment, student=user, answer="Решение") for user in students]
    )

    with CaptureQueriesContext(connection) as large_queries:
        large = api_client.get(progress_url(course))

    assert small.status_code == large.status_code == 200
    assert small.json()["count"] == 2
    assert large.json()["count"] == 37
    assert len(small_queries) == len(large_queries)
    assert len(large_queries) <= 4
