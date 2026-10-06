import json
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from assignments.models import Assignment, Grade, Submission
from courses.models import Course, Enrollment
from users.models import User

DEMO_GROUP = "studyflow_demo"
DEFAULT_PASSWORD = "StudyFlow-demo-2026!"
DEMO_USERS = (
    ("demo_teacher", User.Role.TEACHER, "Анна", "Соколова"),
    ("demo_other_teacher", User.Role.TEACHER, "Дмитрий", "Орлов"),
    ("demo_student", User.Role.STUDENT, "Иван", "Петров"),
    ("demo_peer", User.Role.STUDENT, "Мария", "Волкова"),
)


class Command(BaseCommand):
    help = "Создаёт демонстрационные курсы, задания и решения в режиме DEBUG."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password", default=DEFAULT_PASSWORD, help="Пароль только для новых демоаккаунтов."
        )
        parser.add_argument(
            "--refresh-deadlines",
            action="store_true",
            help="Обновить сроки демонстрационных заданий относительно текущей даты.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Вывести идентификаторы объектов в JSON без паролей.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo доступна только при DEBUG=True.")

        password = options["password"]
        for username, role, first_name, last_name in DEMO_USERS:
            user = User(
                username=username,
                email=f"{username}@studyflow.example",
                role=role,
                first_name=first_name,
                last_name=last_name,
            )
            try:
                validate_password(password, user=user)
            except ValidationError as error:
                raise CommandError("Недопустимый пароль: " + " ".join(error.messages)) from error

        manifest, created_users = self.seed(password, options["refresh_deadlines"])
        if options["json"]:
            self.stdout.write(json.dumps(manifest, ensure_ascii=False))
            return

        self.stdout.write(self.style.SUCCESS("Демонстрационные данные готовы."))
        for course in manifest["courses"].values():
            self.stdout.write(f"Курс {course['id']}: {course['title']}")
            for assignment in manifest["assignments"].values():
                if assignment["course_id"] == course["id"]:
                    self.stdout.write(f"  Задание {assignment['id']}: {assignment['title']}")
        if created_users:
            self.stdout.write("Новые аккаунты: " + ", ".join(created_users))
            self.stdout.write(f"Пароль новых аккаунтов: {password}")
        else:
            self.stdout.write("Аккаунты уже существуют; их пароли сохранены.")
        if options["refresh_deadlines"]:
            self.stdout.write("Сроки демонстрационных заданий обновлены.")

    @transaction.atomic
    def seed(self, password, refresh_deadlines):
        group, _ = Group.objects.get_or_create(name=DEMO_GROUP)
        group = Group.objects.select_for_update().get(pk=group.pk)
        users, created_users = self.seed_users(group, password)
        now = timezone.now()

        python, _ = self.get_named_object(
            Course.objects,
            teacher=users["demo_teacher"],
            title="[Demo] Основы Python",
            defaults={"description": "Практика Python: строки, коллекции и работа с файлами."},
        )
        databases, _ = self.get_named_object(
            Course.objects,
            teacher=users["demo_other_teacher"],
            title="[Demo] Базы данных",
            defaults={"description": "Реляционные базы данных и запросы SQL."},
        )
        courses = {"python": python, "databases": databases}
        for course, username in (
            (python, "demo_student"),
            (python, "demo_peer"),
            (databases, "demo_peer"),
        ):
            Enrollment.objects.get_or_create(course=course, student=users[username])

        assignments = {}
        for key, course, title, description, max_score, days, status in (
            (
                "words",
                python,
                "Подсчёт слов",
                "Подсчитайте частоту слов в тексте без учёта регистра. "
                "Результат представьте словарём: слово - количество вхождений.",
                100,
                14,
                Assignment.Status.PUBLISHED,
            ),
            (
                "collections",
                python,
                "Коллекции",
                "Удалите повторяющиеся элементы списка, сохранив исходный порядок.",
                50,
                14,
                Assignment.Status.PUBLISHED,
            ),
            (
                "files",
                python,
                "Работа с файлами",
                "Прочитайте текстовый файл и вычислите количество строк и непустых слов.",
                25,
                -1,
                Assignment.Status.PUBLISHED,
            ),
            (
                "iterators",
                python,
                "Итераторы",
                "Реализуйте генератор последовательности чисел Фибоначчи.",
                100,
                21,
                Assignment.Status.DRAFT,
            ),
            (
                "joins",
                databases,
                "SQL JOIN",
                "Выведите имена студентов и названия их курсов, объединив таблицы "
                "students, enrollments и courses.",
                100,
                14,
                Assignment.Status.PUBLISHED,
            ),
        ):
            due_at = now + timedelta(days=days)
            assignment, created = self.get_named_object(
                Assignment.objects.select_for_update(),
                course=course,
                title=title,
                defaults={
                    "description": description,
                    "max_score": max_score,
                    "status": status,
                    "published_at": now - timedelta(days=2)
                    if status == Assignment.Status.PUBLISHED
                    else None,
                    "due_at": due_at,
                },
            )
            if status == Assignment.Status.PUBLISHED and assignment.status != status:
                raise CommandError(
                    f"Демозадание «{title}» является черновиком вместо опубликованного задания."
                )
            if refresh_deadlines and not created:
                Assignment.objects.filter(pk=assignment.pk).update(due_at=due_at)
            assignments[key] = assignment

        submissions = {}
        for key, assignment, username, answer, score, feedback in (
            (
                "student_words",
                assignments["words"],
                "demo_student",
                'from collections import Counter\n\ntext = "Python - это просто. Python!"\n'
                "counts = dict(Counter(text.lower().split()))\nprint(counts)",
                80,
                "Верный выбор Counter. Добавьте удаление знаков препинания перед подсчётом.",
            ),
            (
                "peer_words",
                assignments["words"],
                "demo_peer",
                'text = "Python помогает учиться Python"\ncounts = {}\n'
                "for word in text.lower().split():\n    counts[word] = counts.get(word, 0) + 1",
                None,
                "",
            ),
            (
                "peer_joins",
                assignments["joins"],
                "demo_peer",
                "SELECT students.name, courses.title\nFROM students\n"
                "JOIN enrollments ON enrollments.student_id = students.id\n"
                "JOIN courses ON courses.id = enrollments.course_id;",
                70,
                "Связи таблиц выбраны верно. Добавьте устойчивую сортировку результата.",
            ),
        ):
            submission, _ = Submission.objects.get_or_create(
                assignment=assignment,
                student=users[username],
                defaults={"answer": answer},
            )
            if score is not None:
                if (
                    not Grade.objects.filter(submission=submission).exists()
                    and score > assignment.max_score
                ):
                    raise CommandError(
                        f"Демооценка {score} для задания «{assignment.title}» превышает "
                        f"максимальный балл {assignment.max_score}."
                    )
                Grade.objects.get_or_create(
                    submission=submission,
                    defaults={
                        "score": score,
                        "feedback": feedback,
                        "graded_by": assignment.course.teacher,
                    },
                )
            submissions[key] = submission

        return {
            "users": {
                username: {"id": user.pk, "role": user.role} for username, user in users.items()
            },
            "courses": {
                key: {"id": course.pk, "title": course.title, "teacher": course.teacher.username}
                for key, course in courses.items()
            },
            "assignments": {
                key: {
                    "id": assignment.pk,
                    "title": assignment.title,
                    "course_id": assignment.course_id,
                }
                for key, assignment in assignments.items()
            },
            "submissions": {
                key: {
                    "id": submission.pk,
                    "assignment_id": submission.assignment_id,
                    "student": submission.student.username,
                }
                for key, submission in submissions.items()
            },
        }, created_users

    def get_named_object(self, queryset, **lookup):
        try:
            return queryset.get_or_create(**lookup)
        except queryset.model.MultipleObjectsReturned as error:
            raise CommandError(
                f"Неоднозначные демоданные: несколько объектов «{lookup['title']}» "
                f"({queryset.model._meta.verbose_name})."
            ) from error

    def seed_users(self, group, password):
        users, created_users = {}, []
        for username, role, first_name, last_name in DEMO_USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": f"{username}@studyflow.example",
                    "first_name": first_name,
                    "last_name": last_name,
                    "role": role,
                    "password": lambda: make_password(password),
                    "is_active": True,
                    "is_staff": False,
                    "is_superuser": False,
                },
            )
            if created:
                user.groups.add(group)
                created_users.append(username)
            elif not user.groups.filter(pk=group.pk).exists():
                raise CommandError(
                    f"Аккаунт {username} уже существует и не принадлежит демоданным."
                )
            elif user.role != role or not user.is_active:
                raise CommandError(f"У демоаккаунта {username} изменена роль или отключён доступ.")
            users[username] = user
        return users, created_users
