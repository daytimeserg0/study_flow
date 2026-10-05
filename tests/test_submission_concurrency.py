from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connections
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, Submission
from courses.models import Course, Enrollment
from users.models import User


@pytest.mark.django_db(transaction=True)
def test_concurrent_submissions_create_one_work_and_return_conflict():
    teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
    student = User.objects.create_user(username="student")
    course = Course.objects.create(title="Python", teacher=teacher)
    Enrollment.objects.create(course=course, student=student)
    assignment = Assignment.objects.create(
        course=course,
        title="Первая программа",
        description="Вывести приветствие",
        status=Assignment.Status.PUBLISHED,
        published_at=timezone.now(),
    )
    url = reverse(
        "assignments:submission-list",
        kwargs={"course_pk": course.pk, "assignment_pk": assignment.pk},
    )
    start = Barrier(2, timeout=10)

    def submit(answer):
        try:
            client = APIClient()
            client.force_authenticate(student)
            start.wait()
            response = client.post(url, {"answer": answer}, format="json")
            return response.status_code, answer
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(submit, answer) for answer in ("Первое решение", "Второе решение")
        ]
        results = [future.result(timeout=15) for future in futures]

    assert sorted(code for code, _ in results) == [201, 409]
    submission = Submission.objects.get(assignment=assignment, student=student)
    successful_answer = next(answer for code, answer in results if code == 201)
    assert submission.answer == successful_answer
