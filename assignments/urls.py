from rest_framework.routers import SimpleRouter

from assignments.submission_views import SubmissionViewSet
from assignments.views import AssignmentViewSet

app_name = "assignments"

router = SimpleRouter()
router.register(
    r"courses/(?P<course_pk>[0-9]+)/assignments", AssignmentViewSet, basename="assignment"
)
router.register(
    r"courses/(?P<course_pk>[0-9]+)/assignments/(?P<assignment_pk>[0-9]+)/submissions",
    SubmissionViewSet,
    basename="submission",
)

urlpatterns = router.urls
