from django.contrib import admin

from assignments.models import Assignment, Grade, Submission


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "status", "due_at", "max_score")
    list_filter = ("status",)
    list_select_related = ("course",)
    search_fields = ("title", "course__title")
    autocomplete_fields = ("course",)
    readonly_fields = ("status", "published_at", "created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return (*fields, "max_score") if obj else fields

    def save_model(self, request, obj, form, change):
        if change:
            obj.save(update_fields=(*form.changed_data, "updated_at"))
        else:
            super().save_model(request, obj, form, change)


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("student", "assignment", "status", "submitted_at", "updated_at")
    list_select_related = ("student", "assignment", "grade")
    search_fields = ("student__username", "assignment__title", "assignment__course__title")
    readonly_fields = (
        "assignment",
        "student",
        "answer",
        "solution_url",
        "status",
        "submitted_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Grade)
class GradeAdmin(admin.ModelAdmin):
    list_display = ("submission", "score", "graded_by", "graded_at")
    list_select_related = ("submission__student", "submission__assignment", "graded_by")
    search_fields = ("submission__student__username", "submission__assignment__title")
    readonly_fields = ("submission", "score", "feedback", "graded_by", "graded_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
