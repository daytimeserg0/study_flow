from django.contrib import admin

from assignments.models import Assignment


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "status", "due_at", "max_score")
    list_filter = ("status",)
    list_select_related = ("course",)
    search_fields = ("title", "course__title")
    autocomplete_fields = ("course",)
    readonly_fields = ("status", "published_at", "created_at", "updated_at")
