from django.contrib import admin

from assignments.models import Assignment, Submission


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "status", "due_at", "max_score")
    list_filter = ("status",)
    list_select_related = ("course",)
    search_fields = ("title", "course__title")
    autocomplete_fields = ("course",)
    readonly_fields = ("status", "published_at", "created_at", "updated_at")


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("student", "assignment", "submitted_at", "updated_at")
    list_select_related = ("student", "assignment")
    search_fields = ("student__username", "assignment__title", "assignment__course__title")
    readonly_fields = (
        "assignment",
        "student",
        "answer",
        "solution_url",
        "submitted_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
