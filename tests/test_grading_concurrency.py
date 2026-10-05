from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connections
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, Grade, Submission
from courses.models import Course, Enrollment
from users.models import User

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def submitted_work():
    teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
    student = User.objects.create_user(username="student")
    course = Course.objects.create(title="Python", teacher=teacher)
    Enrollment.objects.create(course=course, student=student)
    assignment = Assignment.objects.create(
        course=course,
        title="Словари",
        description="Подсчитать частоту слов",
        status=Assignment.Status.PUBLISHED,
        published_at=timezone.now(),
    )
    submission = Submission.objects.create(
        assignment=assignment, student=student, answer="Первый ответ"
    )
    return teacher, student, assignment, submission


def parallel_requests(*requests):
    start = Barrier(len(requests), timeout=10)

    def send(user, method, url, payload):
        try:
            client = APIClient()
            client.force_authenticate(user)
            start.wait()
            response = getattr(client, method)(url, payload, format="json")
            return response.status_code, response.json()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(requests)) as executor:
        futures = [executor.submit(send, *request) for request in requests]
        return [future.result(timeout=15) for future in futures]


def test_concurrent_grading_and_revision_keep_the_graded_answer(submitted_work):
    teacher, student, assignment, submission = submitted_work
    kwargs = {
        "course_pk": assignment.course_id,
        "assignment_pk": assignment.pk,
        "pk": submission.pk,
    }
    grade_url = reverse("assignments:submission-grade", kwargs=kwargs)
    submission_url = reverse("assignments:submission-detail", kwargs=kwargs)

    grading, revision = parallel_requests(
        (teacher, "post", grade_url, {"score": 80}),
        (student, "patch", submission_url, {"answer": "Исправленный ответ"}),
    )

    assert grading[0] == 200
    assert revision[0] in (200, 409)
    submission.refresh_from_db()
    assert submission.answer == grading[1]["answer"]
    assert submission.answer == ("Исправленный ответ" if revision[0] == 200 else "Первый ответ")
    assert submission.grade.score == 80


@pytest.mark.parametrize("existing_grade", [False, True])
def test_concurrent_grading_and_maximum_change_preserve_score_limit(submitted_work, existing_grade):
    teacher, _, assignment, submission = submitted_work
    if existing_grade:
        Grade.objects.create(submission=submission, score=10, graded_by=teacher)
    grade_url = reverse(
        "assignments:submission-grade",
        kwargs={
            "course_pk": assignment.course_id,
            "assignment_pk": assignment.pk,
            "pk": submission.pk,
        },
    )
    assignment_url = reverse(
        "assignments:assignment-detail",
        kwargs={"course_pk": assignment.course_id, "pk": assignment.pk},
    )

    grading, maximum_change = parallel_requests(
        (teacher, "post", grade_url, {"score": 80}),
        (teacher, "patch", assignment_url, {"max_score": 50}),
    )

    assert (grading[0], maximum_change[0]) in ((200, 400), (400, 200))
    assignment.refresh_from_db()
    grade = Grade.objects.filter(submission=submission).first()
    if grading[0] == 200:
        assert grade.score == 80
        assert assignment.max_score == 100
    else:
        assert assignment.max_score == 50
        if existing_grade:
            assert grade.score == 10
        else:
            assert grade is None
