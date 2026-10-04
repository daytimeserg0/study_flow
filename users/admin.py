from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from users.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = (*DjangoUserAdmin.list_display, "role")
    list_filter = (*DjangoUserAdmin.list_filter, "role")
    fieldsets = (*DjangoUserAdmin.fieldsets, ("Обучение", {"fields": ("role",)}))
    add_fieldsets = (*DjangoUserAdmin.add_fieldsets, ("Обучение", {"fields": ("role",)}))

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return fields if request.user.is_superuser else (*fields, "role")
