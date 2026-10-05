from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, Grade, Submission
from courses.models import Course, Enrollment
from users.models import User

pytestmark = pytest.mark.django_db

COLLECTIONS = ("courses", "students", "assignments", "submissions")
IGNORED_FILTERS = (
    "?status=bad&search=missing&student=missing&due_before=bad&overdue=bad&ordering=missing"
)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def teacher():
    return User.objects.create_user(username="teacher", role=User.Role.TEACHER)


@pytest.fixture
def course(teacher):
    return Course.objects.create(title="Main course", teacher=teacher)


@pytest.fixture
def student(course):
    user = User.objects.create_user(
        username="alice", first_name="Anastasia", last_name="Petrova", email="private@example.com"
    )
    Enrollment.objects.create(course=course, student=user)
    return user


@pytest.fixture
def assignment(course):
    return Assignment.objects.create(
        course=course,
        title="Main assignment",
        description="Solve the exercise",
        status=Assignment.Status.PUBLISHED,
        published_at=timezone.now(),
    )


def collection_url(kind, course, assignment):
    if kind == "courses":
        return reverse("courses:course-list")
    if kind == "students":
        return reverse("courses:course-students", kwargs={"pk": course.pk})
    kwargs = {"course_pk": course.pk}
    if kind == "assignments":
        return reverse("assignments:assignment-list", kwargs=kwargs)
    kwargs["assignment_pk"] = assignment.pk
    return reverse("assignments:submission-list", kwargs=kwargs)


def make_collection(kind, teacher, course, assignment, size):
    if kind == "courses":
        rows = [course] + Course.objects.bulk_create(
            [Course(title=f"Course {index}", teacher=teacher) for index in range(size - 1)]
        )
    elif kind == "assignments":
        rows = [assignment] + Assignment.objects.bulk_create(
            [
                Assignment(course=course, title=f"Assignment {index}", description="Exercise")
                for index in range(size - 1)
            ]
        )
    else:
        students = User.objects.bulk_create(
            [User(username=f"learner_{index}") for index in range(size)]
        )
        if kind == "students":
            rows = Enrollment.objects.bulk_create(
                [Enrollment(course=course, student=user) for user in students]
            )
        else:
            rows = Submission.objects.bulk_create(
                [
                    Submission(assignment=assignment, student=user, answer="Solution")
                    for user in students
                ]
            )
    ids = [row.pk for row in rows]
    return ids if kind == "students" else list(reversed(ids))


def result_ids(response):
    assert response.status_code == 200, response.content
    return [item["id"] for item in response.json()["results"]]


@pytest.mark.parametrize("kind", COLLECTIONS)
def test_collections_paginate_with_default_size_custom_size_and_cap(
    api_client, teacher, course, assignment, kind
):
    expected = make_collection(kind, teacher, course, assignment, size=105)
    api_client.force_authenticate(teacher)
    url = collection_url(kind, course, assignment)

    first = api_client.get(url)

    assert result_ids(first) == expected[:20]
    assert set(first.json()) == {"count", "next", "previous", "results"}
    assert first.json()["count"] == 105
    assert first.json()["previous"] is None
    assert parse_qs(urlsplit(first.json()["next"]).query)["page"] == ["2"]
    second = api_client.get(first.json()["next"])
    assert result_ids(second) == expected[20:40]
    assert second.json()["previous"] is not None

    custom = api_client.get(url, {"page_size": 7, "page": 3})
    assert result_ids(custom) == expected[14:21]
    assert custom.json()["count"] == 105
    assert parse_qs(urlsplit(custom.json()["next"]).query)["page_size"] == ["7"]

    capped = api_client.get(url, {"page_size": 1000})
    assert result_ids(capped) == expected[:100]
    last = api_client.get(capped.json()["next"])
    assert result_ids(last) == expected[100:]
    assert last.json()["count"] == 105
    assert last.json()["next"] is None
    assert last.json()["previous"] is not None


@pytest.mark.parametrize("kind", COLLECTIONS)
@pytest.mark.parametrize("page", ["0", "-1", "not-a-page", "999"])
def test_invalid_or_nonexistent_pages_return_404(
    api_client, teacher, course, assignment, kind, page
):
    api_client.force_authenticate(teacher)

    response = api_client.get(collection_url(kind, course, assignment), {"page": page})

    assert response.status_code == 404


@pytest.mark.parametrize("kind", ["courses", "assignments"])
def test_search_matches_title_and_description_case_insensitively(
    api_client, teacher, course, assignment, kind
):
    model = Course if kind == "courses" else Assignment
    owner = {"teacher": teacher} if kind == "courses" else {"course": course}
    title_match = model.objects.create(title="PyThOn basics", description="Start here", **owner)
    description_match = model.objects.create(title="Exercises", description="Learn python", **owner)
    model.objects.create(title="Unrelated", description="No matching keyword", **owner)
    api_client.force_authenticate(teacher)

    response = api_client.get(collection_url(kind, course, assignment), {"search": "PYTHON"})

    assert set(result_ids(response)) == {title_match.pk, description_match.pk}
    assert response.json()["count"] == 2


@pytest.mark.parametrize(
    ("kind", "field"),
    [
        ("courses", "title"),
        ("courses", "created_at"),
        ("assignments", "title"),
        ("assignments", "created_at"),
        ("assignments", "due_at"),
        ("assignments", "max_score"),
        ("submissions", "submitted_at"),
        ("submissions", "updated_at"),
    ],
)
@pytest.mark.parametrize("descending", [False, True])
def test_allowed_orderings_use_stable_id_tiebreak_across_pages(
    api_client, teacher, course, assignment, kind, field, descending
):
    earlier = timezone.now()
    later = earlier + timedelta(hours=1)
    rows = []
    for index in range(3):
        if kind == "courses":
            row = Course.objects.create(
                title="Ordering beta" if index == 0 else "Ordering alpha", teacher=teacher
            )
        elif kind == "assignments":
            row = Assignment.objects.create(
                course=course,
                title="Ordering beta" if index == 0 else "Ordering alpha",
                description="Ordering exercise",
                due_at=later if index == 0 else earlier,
                max_score=100 if index == 0 else 50,
            )
        else:
            user = User.objects.create_user(username=f"ordering_{index}")
            row = Submission.objects.create(assignment=assignment, student=user, answer="Solution")
        if field.endswith("_at"):
            type(row).objects.filter(pk=row.pk).update(**{field: later if index == 0 else earlier})
        rows.append(row)
    expected = [rows[0], rows[1], rows[2]] if descending else [rows[1], rows[2], rows[0]]
    api_client.force_authenticate(teacher)
    url = collection_url(kind, course, assignment)
    params = {
        "search": "ordering",
        "ordering": f"{'-' if descending else ''}{field}",
        "page_size": 1,
    }

    response = api_client.get(url, params)
    actual = result_ids(response)
    while response.json()["next"]:
        response = api_client.get(response.json()["next"])
        actual.extend(result_ids(response))

    assert actual == [row.pk for row in expected]


@pytest.mark.parametrize("kind", ["courses", "assignments", "submissions"])
def test_unknown_ordering_falls_back_to_default_with_deterministic_ties(
    api_client, teacher, course, assignment, kind
):
    expected = make_collection(kind, teacher, course, assignment, size=3)
    model = {"courses": Course, "assignments": Assignment, "submissions": Submission}[kind]
    field = "submitted_at" if kind == "submissions" else "created_at"
    model.objects.filter(pk__in=expected).update(**{field: timezone.now()})
    api_client.force_authenticate(teacher)

    response = api_client.get(
        collection_url(kind, course, assignment), {"ordering": "not_allowed", "page_size": 2}
    )

    assert result_ids(response) == expected[:2]
    assert result_ids(api_client.get(response.json()["next"])) == expected[2:]


@pytest.mark.parametrize("actor", ["teacher", "student"])
@pytest.mark.parametrize("status", ["draft", "published"])
def test_assignment_status_filter_respects_student_publication_scope(
    api_client, teacher, student, course, assignment, actor, status
):
    draft = Assignment.objects.create(course=course, title="Draft", description="Hidden exercise")
    api_client.force_authenticate(teacher if actor == "teacher" else student)

    response = api_client.get(collection_url("assignments", course, assignment), {"status": status})

    expected = (
        [assignment.pk] if status == "published" else ([draft.pk] if actor == "teacher" else [])
    )
    assert result_ids(response) == expected
    assert response.json()["count"] == len(expected)


@pytest.mark.parametrize(
    ("filters", "expected_offsets"),
    [
        ({"due_before": "2030-01-01T15:00:00+03:00"}, [-1, 0]),
        ({"due_after": "2030-01-01T12:00:00Z"}, [0, 1]),
        (
            {"due_before": "2030-01-01T12:00:00Z", "due_after": "2030-01-01T12:00:00Z"},
            [0],
        ),
    ],
)
def test_assignment_due_bounds_are_inclusive_and_exclude_missing_dates(
    api_client, teacher, course, assignment, filters, expected_offsets
):
    boundary = datetime(2030, 1, 1, 12, tzinfo=UTC)
    dated = {
        offset: Assignment.objects.create(
            course=course,
            title=f"Deadline {offset}",
            description="Exercise",
            due_at=boundary + timedelta(seconds=offset),
        )
        for offset in (-1, 0, 1)
    }
    api_client.force_authenticate(teacher)

    response = api_client.get(collection_url("assignments", course, assignment), filters)

    assert set(result_ids(response)) == {dated[offset].pk for offset in expected_offsets}
    assert assignment.pk not in result_ids(response)


@pytest.mark.parametrize("overdue", ["true", "false"])
def test_overdue_uses_publication_and_inclusive_deadline_and_false_is_complement(
    api_client, teacher, course, assignment, overdue
):
    now = timezone.now()
    expired = []
    for offset in (-1, 0):
        expired.append(
            Assignment.objects.create(
                course=course,
                title=f"Expired {offset}",
                description="Exercise",
                due_at=now + timedelta(seconds=offset),
                status=Assignment.Status.PUBLISHED,
                published_at=now - timedelta(days=1),
            )
        )
    future = Assignment.objects.create(
        course=course,
        title="Future",
        description="Exercise",
        due_at=now + timedelta(days=1),
        status=Assignment.Status.PUBLISHED,
        published_at=now,
    )
    draft = Assignment.objects.create(
        course=course, title="Expired draft", description="Exercise", due_at=now - timedelta(days=1)
    )
    undated_draft = Assignment.objects.create(
        course=course, title="Undated draft", description="Text"
    )
    api_client.force_authenticate(teacher)

    with patch("django.utils.timezone.now", return_value=now):
        unfiltered = api_client.get(collection_url("assignments", course, assignment))
        response = api_client.get(
            collection_url("assignments", course, assignment), {"overdue": overdue}
        )

    all_rows = [*expired, assignment, future, draft, undated_draft]
    assert set(result_ids(unfiltered)) == {row.pk for row in all_rows}
    expected = expired if overdue == "true" else [assignment, future, draft, undated_draft]
    assert set(result_ids(response)) == {row.pk for row in expected}


@pytest.mark.parametrize(
    "filters",
    [
        {"status": "unknown"},
        {"status": ""},
        {"due_before": "not-a-date"},
        {"due_after": "2026-13-01T12:00:00Z"},
        {"due_after": "2030-01-02T12:00:00Z", "due_before": "2030-01-01T12:00:00Z"},
        {"overdue": "sometimes"},
        {"overdue": ""},
    ],
)
def test_assignment_filters_reject_invalid_values(api_client, teacher, course, assignment, filters):
    api_client.force_authenticate(teacher)

    response = api_client.get(collection_url("assignments", course, assignment), filters)

    assert response.status_code == 400


@pytest.mark.parametrize("search", ["ALICE", "Anastasia", "Petrova", "Anastasia Petrova"])
def test_submission_search_matches_student_names_and_username(
    api_client, teacher, student, course, assignment, search
):
    match = Submission.objects.create(assignment=assignment, student=student, answer="Solution")
    peer = User.objects.create_user(username="bob", first_name="Boris", last_name="Ivanov")
    Submission.objects.create(assignment=assignment, student=peer, answer="Alice Anastasia Petrova")
    api_client.force_authenticate(teacher)

    response = api_client.get(collection_url("submissions", course, assignment), {"search": search})

    assert result_ids(response) == [match.pk]
    assert student.email.encode() not in response.content


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"status": "submitted"}, "submitted"),
        ({"status": "graded"}, "graded"),
        ({"student": "alice"}, "submitted"),
        ({"student": "ali"}, None),
        ({"student": "alice", "status": "graded"}, None),
        ({"student": "alice_extra", "status": "graded"}, "graded"),
    ],
)
def test_submission_status_and_exact_student_filters_combine(
    api_client, teacher, student, course, assignment, filters, expected
):
    submitted = Submission.objects.create(
        assignment=assignment, student=student, answer="Unreviewed"
    )
    peer = User.objects.create_user(username="alice_extra")
    graded = Submission.objects.create(assignment=assignment, student=peer, answer="Reviewed")
    Grade.objects.create(submission=graded, score=75, graded_by=teacher)
    api_client.force_authenticate(teacher)

    response = api_client.get(collection_url("submissions", course, assignment), filters)

    expected_ids = (
        [] if expected is None else [{"submitted": submitted.pk, "graded": graded.pk}[expected]]
    )
    assert result_ids(response) == expected_ids


@pytest.mark.parametrize("status", ["unknown", "draft", ""])
def test_submission_filters_reject_invalid_status(api_client, teacher, course, assignment, status):
    api_client.force_authenticate(teacher)

    response = api_client.get(collection_url("submissions", course, assignment), {"status": status})

    assert response.status_code == 400


@pytest.mark.parametrize("actor", ["teacher", "student"])
def test_search_and_filters_never_expand_course_assignment_or_submission_scope(
    api_client, teacher, student, course, assignment, actor
):
    course.title = "Shared keyword"
    course.save(update_fields=["title"])
    assignment.title = "Shared keyword"
    assignment.save(update_fields=["title"])
    outsider = User.objects.create_user(username="outsider_teacher", role=User.Role.TEACHER)
    private_course = Course.objects.create(title="Shared keyword", teacher=outsider)
    private_assignment = Assignment.objects.create(
        course=private_course,
        title="Shared keyword",
        description="Secret",
        status=Assignment.Status.PUBLISHED,
        published_at=timezone.now(),
    )
    own = Submission.objects.create(assignment=assignment, student=student, answer="Own answer")
    peer = User.objects.create_user(username="alice_peer", first_name="Alice")
    other = Submission.objects.create(assignment=assignment, student=peer, answer="Peer answer")
    Submission.objects.create(
        assignment=private_assignment, student=student, answer="Private answer"
    )
    api_client.force_authenticate(teacher if actor == "teacher" else student)

    courses = api_client.get(collection_url("courses", course, assignment), {"search": "shared"})
    assignments = api_client.get(
        collection_url("assignments", course, assignment),
        {"search": "shared", "status": "published"},
    )
    submissions = api_client.get(
        collection_url("submissions", course, assignment), {"search": "alice"}
    )
    peer_only = api_client.get(
        collection_url("submissions", course, assignment),
        {"student": peer.username, "status": "submitted", "search": "alice"},
    )

    assert result_ids(courses) == [course.pk]
    assert result_ids(assignments) == [assignment.pk]
    assert set(result_ids(submissions)) == ({own.pk, other.pk} if actor == "teacher" else {own.pk})
    assert result_ids(peer_only) == ([other.pk] if actor == "teacher" else [])
    for kind in ("assignments", "submissions"):
        response = api_client.get(
            collection_url(kind, private_course, private_assignment), {"search": "shared"}
        )
        assert response.status_code == 404
        assert b"Private answer" not in response.content


@pytest.mark.parametrize("kind", ["courses", "assignments", "submissions"])
@pytest.mark.parametrize("actor", ["teacher", "student"])
def test_detail_lookup_ignores_collection_filters(
    api_client, teacher, student, course, assignment, kind, actor
):
    submission = Submission.objects.create(
        assignment=assignment, student=student, answer="Solution"
    )
    if kind == "courses":
        url = reverse("courses:course-detail", kwargs={"pk": course.pk})
        expected_id = course.pk
    elif kind == "assignments":
        url = reverse(
            "assignments:assignment-detail", kwargs={"course_pk": course.pk, "pk": assignment.pk}
        )
        expected_id = assignment.pk
    else:
        url = reverse(
            "assignments:submission-detail",
            kwargs={"course_pk": course.pk, "assignment_pk": assignment.pk, "pk": submission.pk},
        )
        expected_id = submission.pk
    api_client.force_authenticate(teacher if actor == "teacher" else student)

    response = api_client.get(url + IGNORED_FILTERS + "&page=not-a-page")

    assert response.status_code == 200
    assert response.json()["id"] == expected_id


@pytest.mark.parametrize(
    ("route", "method", "expected_status"),
    [
        ("courses:course-list", "post", 201),
        ("courses:course-detail", "put", 200),
        ("courses:course-detail", "patch", 200),
        ("courses:course-students", "post", 201),
        ("assignments:assignment-list", "post", 201),
        ("assignments:assignment-detail", "put", 200),
        ("assignments:assignment-detail", "patch", 200),
        ("assignments:assignment-detail", "delete", 204),
        ("assignments:assignment-publish", "post", 200),
        ("assignments:submission-list", "post", 201),
        ("assignments:submission-detail", "put", 200),
        ("assignments:submission-detail", "patch", 200),
        ("assignments:submission-grade", "post", 200),
    ],
)
def test_writes_and_custom_actions_ignore_collection_filters(
    api_client, teacher, student, course, assignment, route, method, expected_status
):
    kwargs = {}
    payload = {"title": "Updated title", "description": "Updated description"}
    actor = teacher
    if route.startswith("courses:"):
        if route != "courses:course-list":
            kwargs["pk"] = course.pk
        if route == "courses:course-students":
            newcomer = User.objects.create_user(username="newcomer")
            payload = {"username": newcomer.username}
    else:
        kwargs["course_pk"] = course.pk
        if "submission" in route:
            kwargs["assignment_pk"] = assignment.pk
            payload = {"answer": "Updated answer"}
            actor = student
            if route != "assignments:submission-list":
                submission = Submission.objects.create(
                    assignment=assignment, student=student, answer="Original answer"
                )
                kwargs["pk"] = submission.pk
            if route == "assignments:submission-grade":
                actor = teacher
                payload = {"score": 75}
        elif route != "assignments:assignment-list":
            kwargs["pk"] = assignment.pk
            if method == "delete" or route == "assignments:assignment-publish":
                assignment.status = Assignment.Status.DRAFT
                assignment.published_at = None
                assignment.save(update_fields=["status", "published_at"])
            if route == "assignments:assignment-publish":
                payload = {}
    api_client.force_authenticate(actor)

    response = getattr(api_client, method)(
        reverse(route, kwargs=kwargs) + IGNORED_FILTERS + "&page=not-a-page", payload, format="json"
    )

    assert response.status_code == expected_status, response.content
    if method == "delete":
        assert not Assignment.objects.filter(pk=assignment.pk).exists()
    elif route == "courses:course-students":
        assert Enrollment.objects.filter(course=course, student=newcomer).exists()
    elif route == "assignments:assignment-publish":
        assignment.refresh_from_db()
        assert assignment.status == Assignment.Status.PUBLISHED
    elif route == "assignments:submission-grade":
        assert Grade.objects.get(submission=submission).score == 75
    else:
        field = "answer" if "submission" in route else "title"
        assert response.json()[field] == payload[field]


def test_roster_ignores_course_search_and_collection_filters(
    api_client, teacher, student, course, assignment
):
    api_client.force_authenticate(teacher)

    response = api_client.get(collection_url("students", course, assignment) + IGNORED_FILTERS)

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["results"][0]["student"]["id"] == student.pk


def test_submission_list_queries_do_not_grow_with_students_grades_or_graders(
    api_client, teacher, student, course, assignment
):
    first = Submission.objects.create(assignment=assignment, student=student, answer="Solution")
    Grade.objects.create(submission=first, score=75, graded_by=teacher)
    api_client.force_authenticate(teacher)
    url = collection_url("submissions", course, assignment)

    with CaptureQueriesContext(connection) as small_queries:
        small = api_client.get(url)
    assert len(result_ids(small)) == 1
    assert small.json()["results"][0]["grade"]["graded_by"]["id"] == teacher.pk

    for index in range(7):
        user = User.objects.create_user(username=f"more_students_{index}")
        submission = Submission.objects.create(
            assignment=assignment, student=user, answer="Solution"
        )
        if index < 5:
            Grade.objects.create(submission=submission, score=50, graded_by=teacher)

    with CaptureQueriesContext(connection) as larger_queries:
        larger = api_client.get(url)

    assert len(result_ids(larger)) == 8
    assert sum(item["status"] == "graded" for item in larger.json()["results"]) == 6
    assert len(larger_queries) == len(small_queries)
