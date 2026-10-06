from rest_framework.permissions import SAFE_METHODS, BasePermission

from users.models import User


class AssignmentPermission(BasePermission):
    message = "Изменять задания может только преподаватель - владелец курса."

    def has_permission(self, request, view):
        if view.action == "create":
            course = view.get_course()
            return request.user.role == User.Role.TEACHER and course.teacher_id == request.user.pk
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return request.user.role == User.Role.TEACHER and obj.course.teacher_id == request.user.pk


class SubmissionPermission(BasePermission):
    message = "У вас нет прав для этого действия с решением."

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if view.action == "grade":
            return (
                request.user.role == User.Role.TEACHER
                and view.get_assignment().course.teacher_id == request.user.pk
            )
        return request.user.role == User.Role.STUDENT and obj.student_id == request.user.pk
