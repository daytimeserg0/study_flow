from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, URLValidator
from django.db import models

from courses.models import Course
from users.models import User


class Assignment(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        PUBLISHED = "published", "Опубликовано"

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name="курс",
    )
    title = models.CharField("название", max_length=200)
    description = models.TextField("условие задания")
    due_at = models.DateTimeField("срок сдачи", null=True, blank=True)
    max_score = models.PositiveSmallIntegerField(
        "максимальный балл",
        default=100,
        validators=(MinValueValidator(1), MaxValueValidator(1000)),
    )
    status = models.CharField("статус", max_length=9, choices=Status.choices, default=Status.DRAFT)
    published_at = models.DateTimeField("опубликовано", null=True, blank=True, editable=False)
    created_at = models.DateTimeField("создано", auto_now_add=True)
    updated_at = models.DateTimeField("обновлено", auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "задание"
        verbose_name_plural = "задания"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(max_score__gte=1, max_score__lte=1000),
                name="assignment_valid_max_score",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="draft", published_at__isnull=True)
                    | models.Q(status="published", published_at__isnull=False)
                ),
                name="assignment_valid_publication",
            ),
        ]

    def __str__(self):
        return self.title


class Submission(models.Model):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Ожидает проверки"
        GRADED = "graded", "Проверено"

    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.CASCADE,
        related_name="submissions",
        verbose_name="задание",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="submissions",
        limit_choices_to={"role": User.Role.STUDENT},
        verbose_name="студент",
    )
    answer = models.TextField("текст решения", max_length=20000, blank=True)
    solution_url = models.URLField(
        "ссылка на решение",
        max_length=500,
        blank=True,
        validators=(URLValidator(schemes=("http", "https")),),
    )
    submitted_at = models.DateTimeField("отправлено", auto_now_add=True)
    updated_at = models.DateTimeField("обновлено", auto_now=True)

    class Meta:
        ordering = ("-submitted_at", "-id")
        verbose_name = "решение"
        verbose_name_plural = "решения"
        constraints = [
            models.UniqueConstraint(
                fields=("assignment", "student"), name="unique_assignment_student"
            ),
            models.CheckConstraint(
                condition=~models.Q(answer="", solution_url=""), name="submission_has_content"
            ),
        ]

    def __str__(self):
        return f"{self.student} — {self.assignment}"

    @property
    def status(self):
        return self.Status.GRADED if hasattr(self, "grade") else self.Status.SUBMITTED


class Grade(models.Model):
    submission = models.OneToOneField(
        Submission,
        on_delete=models.CASCADE,
        related_name="grade",
        verbose_name="решение",
    )
    score = models.PositiveSmallIntegerField("балл", validators=(MaxValueValidator(1000),))
    feedback = models.TextField("обратная связь", max_length=5000, blank=True)
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="given_grades",
        limit_choices_to={"role": User.Role.TEACHER},
        verbose_name="проверил",
    )
    graded_at = models.DateTimeField("проверено", auto_now=True)

    class Meta:
        ordering = ("-graded_at", "-id")
        verbose_name = "оценка"
        verbose_name_plural = "оценки"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(score__gte=0, score__lte=1000), name="grade_valid_score"
            ),
        ]

    def __str__(self):
        return f"{self.submission} — {self.score}"
