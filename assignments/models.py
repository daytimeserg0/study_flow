from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from courses.models import Course


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
