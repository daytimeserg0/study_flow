import pytest
from django.urls import reverse
from django.utils import timezone

from assignments.models import Assignment, Grade, Submission
from courses.models import Course
from users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def graded_work(client):
    admin = User.objects.create_superuser(username="admin")
    client.force_login(admin)
    teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
    student = User.objects.create_user(username="student")
    course = Course.objects.create(title="Python", teacher=teacher)
    assignment = Assignment.objects.create(
        course=course,
        title="Словари",
        description="Подсчитать частоту слов",
        status=Assignment.Status.PUBLISHED,
        published_at=timezone.now(),
    )
    submission = Submission.objects.create(assignment=assignment, student=student, answer="Ответ")
    return Grade.objects.create(submission=submission, score=80, graded_by=teacher)


def test_admin_can_edit_assignment_text_without_overriding_score_limit(client, graded_work):
    assignment = graded_work.submission.assignment

    response = client.post(
        reverse("admin:assignments_assignment_change", args=[assignment.pk]),
        {
            "course": assignment.course_id,
            "title": "Уточнённое название",
            "description": assignment.description,
            "max_score": 1,
            "due_at_0": "",
            "due_at_1": "",
            "_save": "Сохранить",
        },
    )

    assert response.status_code == 302
    assignment.refresh_from_db()
    graded_work.refresh_from_db()
    assert assignment.title == "Уточнённое название"
    assert assignment.max_score == 100
    assert graded_work.score == 80


def test_admin_can_view_grade_but_cannot_change_or_delete_it(client, graded_work):
    url = reverse("admin:assignments_grade_change", args=[graded_work.pk])
    assert client.get(url).status_code == 200

    response = client.post(url, {"score": 5, "feedback": "Подмена"})

    assert response.status_code == 403
    delete_url = reverse("admin:assignments_grade_delete", args=[graded_work.pk])
    assert client.post(delete_url, {"post": "yes"}).status_code == 403
    graded_work.refresh_from_db()
    assert graded_work.score == 80
    assert graded_work.feedback == ""
