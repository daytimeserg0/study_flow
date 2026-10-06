import json
from datetime import timedelta
from io import StringIO

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, Grade, Submission
from courses.management.commands.seed_demo import DEFAULT_PASSWORD, DEMO_GROUP
from courses.models import Course, Enrollment
from users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def debug_enabled(settings):
    settings.DEBUG = True


def seed(**options):
    output = StringIO()
    call_command("seed_demo", stdout=output, **options)
    return output.getvalue()


def snapshot():
    return {
        model.__name__: list(model.objects.order_by("pk").values())
        for model in (Group, User, Course, Enrollment, Assignment, Submission, Grade)
    }


def assert_no_learning_data():
    for model in (Course, Enrollment, Assignment, Submission, Grade):
        assert not model.objects.exists()


def test_seed_creates_expected_dataset_and_hashed_credentials():
    output = seed()

    assert User.objects.count() == 4
    assert Course.objects.count() == 2
    assert Assignment.objects.count() == 5
    assert Enrollment.objects.count() == 3
    assert Submission.objects.count() == 3
    assert Grade.objects.count() == 2
    group = Group.objects.get(name=DEMO_GROUP)
    assert group.user_set.count() == 4
    for user in User.objects.all():
        assert user.role == (User.Role.TEACHER if "teacher" in user.username else User.Role.STUDENT)
        assert user.email == f"{user.username}@studyflow.example"
        assert user.first_name and user.last_name
        assert user.is_active
        assert not user.is_staff
        assert not user.is_superuser
        assert user.password != DEFAULT_PASSWORD
        assert user.check_password(DEFAULT_PASSWORD)
        assert user.username in output
    assert DEFAULT_PASSWORD in output
    assert "[Demo] Основы Python" in output
    assert "[Demo] Базы данных" in output
    assert "Подсчёт слов" in output


def test_seed_assignment_states_deadlines_and_grades(monkeypatch):
    now = timezone.now()
    monkeypatch.setattr("courses.management.commands.seed_demo.timezone.now", lambda: now)

    seed()

    for assignment in Assignment.objects.all():
        draft = assignment.title == "Итераторы"
        overdue = assignment.title == "Работа с файлами"
        days = 21 if draft else -1 if overdue else 14
        assert assignment.due_at == now + timedelta(days=days)
        assert assignment.status == (
            Assignment.Status.DRAFT if draft else Assignment.Status.PUBLISHED
        )
        assert assignment.published_at == (None if draft else now - timedelta(days=2))
        expected_scores = {"Коллекции": 50, "Работа с файлами": 25}
        assert assignment.max_score == expected_scores.get(assignment.title, 100)
    words = Assignment.objects.get(title="Подсчёт слов")
    student_submission = words.submissions.get(student__username="demo_student")
    peer_submission = words.submissions.get(student__username="demo_peer")
    assert "Counter" in student_submission.answer
    assert student_submission.grade.score == 80
    assert student_submission.grade.graded_by == words.course.teacher
    assert peer_submission.status == Submission.Status.SUBMITTED
    sql_grade = Grade.objects.get(submission__assignment__title="SQL JOIN")
    assert sql_grade.score == 70
    assert sql_grade.graded_by.username == "demo_other_teacher"
    assert not Submission.objects.filter(assignment__status=Assignment.Status.DRAFT).exists()


def test_repeated_seed_preserves_passwords_profiles_work_and_deadlines():
    seed()
    user = User.objects.get(username="demo_student")
    user.set_password("Changed-credentials-2026!")
    user.first_name = "Александр"
    user.email = "changed@example.com"
    user.save()
    assignment = Assignment.objects.get(title="Подсчёт слов")
    assignment.description = "Изменённое условие"
    assignment.due_at = timezone.now() + timedelta(days=40)
    assignment.save()
    course = assignment.course
    course.description = "Изменённое описание курса"
    course.save()
    submission = Submission.objects.get(student=user)
    submission.answer = "Исправленный ответ"
    submission.save()
    grade = submission.grade
    grade.score = 92
    grade.feedback = "Повторная проверка"
    grade.save()
    before = snapshot()

    output = seed(password="Another-valid-password-2026!")

    assert snapshot() == before
    assert "Another-valid-password-2026!" not in output
    assert DEFAULT_PASSWORD not in output
    user.refresh_from_db()
    assert user.check_password("Changed-credentials-2026!")


@pytest.mark.parametrize("username", ["demo_teacher", "demo_peer"])
def test_reserved_username_collision_rolls_back_all_seed_writes(username):
    owner = User.objects.create_user(username=username, password="Existing-owner-2026!")
    before = snapshot()

    with pytest.raises(CommandError, match="не принадлежит демоданным"):
        seed()

    assert snapshot() == before
    assert User.objects.get(pk=owner.pk).check_password("Existing-owner-2026!")
    assert not Group.objects.filter(name=DEMO_GROUP).exists()
    assert_no_learning_data()


@pytest.mark.parametrize(
    "attributes", [{"role": User.Role.TEACHER}, {"role": User.Role.STUDENT, "is_active": False}]
)
def test_changed_marked_account_prevents_partial_creation(attributes):
    group = Group.objects.create(name=DEMO_GROUP)
    user = User.objects.create_user(username="demo_peer", **attributes)
    user.groups.add(group)
    before = snapshot()

    with pytest.raises(CommandError, match="изменена роль или отключён доступ"):
        seed()

    assert snapshot() == before
    assert_no_learning_data()


def test_seed_is_disabled_outside_debug(settings):
    settings.DEBUG = False

    with pytest.raises(CommandError, match="DEBUG=True"):
        seed()

    assert not User.objects.exists()
    assert not Group.objects.exists()
    assert_no_learning_data()


@pytest.mark.parametrize("password", ["123", "password", "123456789012"])
def test_invalid_password_is_rejected_before_any_writes(password):
    with pytest.raises(CommandError, match="Недопустимый пароль"):
        seed(password=password)

    assert not User.objects.exists()
    assert not Group.objects.exists()
    assert_no_learning_data()


def test_custom_password_applies_only_to_new_accounts():
    group = Group.objects.create(name=DEMO_GROUP)
    existing = User.objects.create_user(
        username="demo_student",
        password="Existing-student-password-2026!",
        email="existing@example.com",
        first_name="Егор",
        role=User.Role.STUDENT,
    )
    existing.groups.add(group)

    output = seed(password="Custom-demo-password-2026!")

    existing.refresh_from_db()
    assert existing.check_password("Existing-student-password-2026!")
    assert existing.email == "existing@example.com"
    assert existing.first_name == "Егор"
    for user in User.objects.exclude(pk=existing.pk):
        assert user.check_password("Custom-demo-password-2026!")
    assert "Custom-demo-password-2026!" in output
    new_accounts_line = next(
        line for line in output.splitlines() if line.startswith("Новые аккаунты")
    )
    assert "demo_student" not in new_accounts_line


def test_refresh_deadlines_is_explicit_and_affects_only_demo_assignments(monkeypatch):
    now = timezone.now()
    monkeypatch.setattr("courses.management.commands.seed_demo.timezone.now", lambda: now)
    seed()
    assignments_before = {assignment.title: assignment for assignment in Assignment.objects.all()}
    custom_deadline = now + timedelta(days=60)
    custom = Assignment.objects.create(
        course=Course.objects.get(title="[Demo] Основы Python"),
        title="Самостоятельная практика",
        description="Дополнительное задание",
        due_at=custom_deadline,
    )
    later = now + timedelta(days=10)
    monkeypatch.setattr("courses.management.commands.seed_demo.timezone.now", lambda: later)
    before = snapshot()

    seed()
    assert snapshot() == before
    seed(refresh_deadlines=True)

    for title, previous in assignments_before.items():
        current = Assignment.objects.get(pk=previous.pk)
        assert current.due_at == previous.due_at + timedelta(days=10)
        assert current.status == previous.status
        assert current.published_at == previous.published_at
        assert current.description == previous.description
        assert current.title == title
    custom.refresh_from_db()
    assert custom.due_at == custom_deadline


def test_json_output_contains_public_identifiers_and_no_passwords():
    output = seed(json=True)
    data = json.loads(output)

    assert DEFAULT_PASSWORD not in output
    assert "password" not in output.lower()
    assert len(data["users"]) == 4
    assert data["courses"]["python"]["id"] == Course.objects.get(title="[Demo] Основы Python").pk
    assert data["assignments"]["words"]["id"] == Assignment.objects.get(title="Подсчёт слов").pk
    assert data["submissions"]["student_words"]["student"] == "demo_student"


def test_seeded_student_api_shows_own_course_and_progress():
    seed()
    student = User.objects.get(username="demo_student")
    python = Course.objects.get(title="[Demo] Основы Python")
    databases = Course.objects.get(title="[Demo] Базы данных")
    api = APIClient()
    api.force_authenticate(student)

    courses = api.get(reverse("courses:course-list"))
    foreign = api.get(reverse("courses:course-detail", kwargs={"pk": databases.pk}))
    assignments = api.get(reverse("assignments:assignment-list", kwargs={"course_pk": python.pk}))
    progress = api.get(reverse("courses:course-progress", kwargs={"pk": python.pk}))

    assert courses.status_code == assignments.status_code == progress.status_code == 200
    assert courses.json()["count"] == 1
    assert courses.json()["results"][0]["id"] == python.pk
    assert foreign.status_code == 404
    assert assignments.json()["count"] == 3
    assert "Итераторы" not in {item["title"] for item in assignments.json()["results"]}
    assert progress.json()["count"] == 1
    row = progress.json()["results"][0]
    assert row["student"]["id"] == student.pk
    assert row["assignments_count"] == 3
    assert row["submitted_count"] == row["graded_count"] == row["overdue_count"] == 1
    assert row["earned_score"] == 80
    assert row["max_score"] == 175
    assert row["completion_percent"] == 33.33


def test_seeded_teacher_api_sees_draft_and_pending_peer_submission():
    seed()
    teacher = User.objects.get(username="demo_teacher")
    course = Course.objects.get(teacher=teacher)
    words = Assignment.objects.get(course=course, title="Подсчёт слов")
    api = APIClient()
    api.force_authenticate(teacher)

    assignments = api.get(reverse("assignments:assignment-list", kwargs={"course_pk": course.pk}))
    submissions = api.get(
        reverse(
            "assignments:submission-list",
            kwargs={"course_pk": course.pk, "assignment_pk": words.pk},
        )
    )
    progress = api.get(reverse("courses:course-progress", kwargs={"pk": course.pk}))

    assert assignments.status_code == submissions.status_code == progress.status_code == 200
    assert assignments.json()["count"] == 4
    assert submissions.json()["count"] == 2
    assert {item["status"] for item in submissions.json()["results"]} == {"submitted", "graded"}
    assert progress.json()["count"] == 2


def test_new_demo_credentials_authenticate_via_jwt():
    seed()

    response = APIClient().post(
        reverse("users:token-obtain-pair"),
        {"username": "demo_student", "password": DEFAULT_PASSWORD},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["access"]
    assert response.json()["refresh"]


@pytest.mark.parametrize("title", ["Подсчёт слов", "SQL JOIN"])
def test_restoring_grade_above_changed_maximum_rolls_back(title):
    seed()
    assignment = Assignment.objects.get(title=title)
    assignment.max_score = 60
    assignment.save(update_fields=("max_score",))
    Grade.objects.filter(submission__assignment=assignment).delete()
    Assignment.objects.get(title="Коллекции").delete()
    before = snapshot()

    with pytest.raises(CommandError, match="превышает максимальный балл") as error:
        seed(refresh_deadlines=True)

    assert title in str(error.value)
    assert snapshot() == before


@pytest.mark.parametrize("title", ["Подсчёт слов", "SQL JOIN"])
def test_existing_modified_grade_is_preserved_below_reduced_maximum(title):
    seed()
    assignment = Assignment.objects.get(title=title)
    Grade.objects.filter(submission__assignment=assignment).update(
        score=40, feedback="Пересмотрено"
    )
    assignment.max_score = 50
    assignment.save(update_fields=("max_score",))
    before = snapshot()

    seed()

    assert snapshot() == before


def test_seed_restores_missing_objects_without_replacing_existing_accounts():
    seed()
    accounts = dict(User.objects.values_list("pk", "password"))
    python = Course.objects.get(title="[Demo] Основы Python")
    words = Assignment.objects.get(title="Подсчёт слов")
    Course.objects.get(title="[Demo] Базы данных").delete()
    Assignment.objects.get(title="Итераторы").delete()
    Submission.objects.get(assignment=words, student__username="demo_peer").delete()
    Grade.objects.get(submission__assignment=words).delete()
    Enrollment.objects.get(course=python, student__username="demo_student").delete()

    seed()

    assert dict(User.objects.values_list("pk", "password")) == accounts
    assert Course.objects.count() == 2
    assert Assignment.objects.count() == 5
    assert Enrollment.objects.count() == 3
    assert Submission.objects.count() == 3
    assert Grade.objects.count() == 2
    assert Course.objects.get(title=python.title).pk == python.pk
    assert Assignment.objects.get(title=words.title).pk == words.pk
    assert Grade.objects.get(submission__assignment=words).score == 80


@pytest.mark.parametrize("title", ["Подсчёт слов", "SQL JOIN"])
def test_seed_does_not_create_work_for_existing_draft(title):
    seed()
    assignment = Assignment.objects.get(title=title)
    assignment.submissions.all().delete()
    assignment.status = Assignment.Status.DRAFT
    assignment.published_at = None
    assignment.save(update_fields=("status", "published_at"))
    Enrollment.objects.get(student__username="demo_student").delete()
    before = snapshot()

    with pytest.raises(CommandError, match="является черновиком") as error:
        seed(refresh_deadlines=True)

    assert title in str(error.value)
    assert snapshot() == before
    assert not Submission.objects.filter(assignment=assignment).exists()


@pytest.mark.parametrize("object_kind", ["course", "assignment"])
def test_ambiguous_reserved_titles_raise_command_error_and_roll_back(object_kind):
    seed()
    databases = Course.objects.get(title="[Demo] Базы данных")
    if object_kind == "course":
        title = databases.title
        Course.objects.create(title=title, teacher=databases.teacher)
        Course.objects.get(title="[Demo] Основы Python").delete()
    else:
        title = "SQL JOIN"
        Assignment.objects.create(course=databases, title=title, description="Другое задание")
        Assignment.objects.get(title="Коллекции").delete()
    before = snapshot()

    with pytest.raises(CommandError, match="Неоднозначные демоданные") as error:
        seed(refresh_deadlines=True)

    assert title in str(error.value)
    assert snapshot() == before
