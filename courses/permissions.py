from rest_framework.permissions import SAFE_METHODS, BasePermission

from users.models import User


class CoursePermission(BasePermission):
    message = "Это действие доступно только преподавателю — владельцу курса."

    def has_permission(self, request, view):
        return view.action != "create" or request.user.role == User.Role.TEACHER

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS and view.action != "students":
            return True
        return request.user.role == User.Role.TEACHER and obj.teacher_id == request.user.pk
