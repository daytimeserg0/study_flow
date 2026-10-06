from django.conf import settings
from django.db import models

from users.models import User


class Course(models.Model):
    title = models.CharField("название", max_length=200)
    description = models.TextField("описание", blank=True)
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="taught_courses",
        limit_choices_to={"role": User.Role.TEACHER},
        verbose_name="преподаватель",
    )
    created_at = models.DateTimeField("создан", auto_now_add=True)
    updated_at = models.DateTimeField("обновлён", auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "курс"
        verbose_name_plural = "курсы"

    def __str__(self):
        return self.title


class Enrollment(models.Model):
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name="курс",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="enrollments",
        limit_choices_to={"role": User.Role.STUDENT, "is_active": True},
        verbose_name="студент",
    )
    created_at = models.DateTimeField("зачислен", auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")
        verbose_name = "зачисление"
        verbose_name_plural = "зачисления"
        constraints = [
            models.UniqueConstraint(fields=("course", "student"), name="unique_course_student"),
        ]

    def __str__(self):
        return f"{self.student} - {self.course}"
