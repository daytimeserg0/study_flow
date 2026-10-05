from rest_framework.routers import SimpleRouter

from assignments.views import AssignmentViewSet

app_name = "assignments"

router = SimpleRouter()
router.register(
    r"courses/(?P<course_pk>[0-9]+)/assignments", AssignmentViewSet, basename="assignment"
)

urlpatterns = router.urls
