from rest_framework.filters import OrderingFilter


class CollectionFilteringMixin:
    def filter_queryset(self, queryset):
        if self.action == "list":
            return super().filter_queryset(queryset)
        return queryset


class StableOrderingFilter(OrderingFilter):
    def get_ordering(self, request, queryset, view):
        ordering = super().get_ordering(request, queryset, view)
        if ordering and not any(field.lstrip("-") in ("id", "pk") for field in ordering):
            return [*ordering, "id"]
        return ordering
