from django.contrib import admin

from courses.models import Course, Enrollment


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("title", "teacher", "created_at")
    list_select_related = ("teacher",)
    search_fields = ("title", "teacher__username")
    autocomplete_fields = ("teacher",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "course", "created_at")
    list_select_related = ("student", "course")
    search_fields = ("student__username", "course__title")
    autocomplete_fields = ("student", "course")
    readonly_fields = ("created_at",)
