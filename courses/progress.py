from django.db.models import Count, IntegerField, Q, Sum, Value
from django.utils import timezone

from assignments.models import Assignment
from users.models import User


def get_course_progress(course, user):
    now = timezone.now()
    totals = course.assignments.filter(status=Assignment.Status.PUBLISHED).aggregate(
        assignments_count=Count("id"),
        max_score=Sum("max_score", default=0),
        overdue_count=Count("id", filter=Q(due_at__lte=now)),
    )
    students = User.objects.filter(enrollments__course=course)
    if user.role == User.Role.STUDENT:
        students = students.filter(pk=user.pk)
    elif user.role != User.Role.TEACHER or user.pk != course.teacher_id:
        students = students.none()

    published = Q(
        submissions__assignment__course=course,
        submissions__assignment__status=Assignment.Status.PUBLISHED,
    )
    return students.annotate(
        assignments_count=Value(totals["assignments_count"], output_field=IntegerField()),
        submitted_count=Count("submissions", filter=published),
        graded_count=Count("submissions__grade", filter=published),
        overdue_count=Value(totals["overdue_count"])
        - Count("submissions", filter=published & Q(submissions__assignment__due_at__lte=now)),
        earned_score=Sum("submissions__grade__score", filter=published, default=0),
        max_score=Value(totals["max_score"], output_field=IntegerField()),
    ).order_by("id")
