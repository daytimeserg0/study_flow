from html.parser import HTMLParser

import pytest
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.urls import Resolver404, resolve, reverse

from courses.management.commands.seed_demo import DEFAULT_PASSWORD
from courses.models import Course
from users.models import User


class PageElements(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.elements = []
        self.feed(content.decode("utf-8"))

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def matching(self, tag, **attributes):
        return [
            attrs
            for element_tag, attrs in self.elements
            if element_tag == tag
            and all(attrs.get(key) == value for key, value in attributes.items())
        ]


def test_public_shell_renders_russian_login_and_empty_application_without_database(client):
    url = reverse("web-app")

    response = client.get(url)

    assert url == "/"
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert b"StudyFlow" in response.content
    page = PageElements(response.content)
    assert page.matching("html", lang="ru")
    assert page.matching("form", id="auth-form")
    assert page.matching("input", id="auth-username", name="username")
    assert page.matching("input", id="auth-password", name="password", type="password")
    assert "hidden" not in page.matching("section", id="auth-screen")[0]
    assert "hidden" in page.matching("div", id="app-shell")[0]
    assert page.matching("div", id="page")


@pytest.mark.parametrize("asset", ["app.css", "app.js", "api.js"])
def test_frontend_assets_are_discoverable_for_collectstatic(asset):
    assert finders.find(f"studyflow/{asset}")


def test_shell_links_stylesheet_and_javascript_module(client):
    response = client.get(reverse("web-app"))

    assert response.status_code == 200
    page = PageElements(response.content)
    assert page.matching("link", rel="stylesheet", href=f"{static('studyflow/app.css')}?v=1")
    assert page.matching("script", type="module", src=static("studyflow/app.js"))


@pytest.mark.parametrize("debug", [False, True])
def test_demo_access_is_rendered_only_in_debug_and_login_remains_available(client, settings, debug):
    settings.DEBUG = debug
    settings.INTERNAL_IPS = []

    response = client.get(reverse("web-app"), REMOTE_ADDR="203.0.113.10")

    assert response.status_code == 200
    assert response.context["debug"] is debug
    page = PageElements(response.content)
    assert page.matching("form", id="auth-form")
    for role in ("student", "teacher"):
        buttons = page.matching("button", **{"data-demo": role})
        assert bool(buttons) is debug
        assert (f"demo_{role}".encode() in response.content) is debug
    assert (DEFAULT_PASSWORD.encode() in response.content) is debug


@pytest.mark.django_db
def test_django_session_does_not_embed_private_data_or_replace_jwt_login(client, settings):
    settings.DEBUG = False
    teacher = User.objects.create_user(
        username="private_shell_teacher",
        email="private-shell-teacher@example.com",
        first_name="PrivateShellName",
        role=User.Role.TEACHER,
        password="Private-shell-password-2026!",
    )
    course = Course.objects.create(title="Private shell course payload", teacher=teacher)
    client.force_login(teacher)

    response = client.get(reverse("web-app"))

    assert response.status_code == 200
    for private_value in (
        teacher.username,
        teacher.email,
        teacher.first_name,
        teacher.password,
        course.title,
    ):
        assert private_value.encode() not in response.content
    page = PageElements(response.content)
    assert page.matching("form", id="auth-form")
    assert "hidden" in page.matching("div", id="app-shell")[0]


@pytest.mark.parametrize(
    "route",
    [
        "health-check",
        "schema",
        "swagger-ui",
        "admin:login",
        "users:register",
        "users:token-obtain-pair",
        "users:token-refresh",
        "users:me",
        "courses:course-list",
    ],
)
def test_frontend_does_not_replace_existing_backend_routes(route):
    assert resolve(reverse(route)).view_name == route


def test_protected_api_keeps_json_authentication_error_instead_of_frontend_html(client):
    response = client.get(reverse("courses:course-list"))

    assert response.status_code == 401
    assert response["Content-Type"].startswith("application/json")
    assert "detail" in response.json()


@pytest.mark.parametrize("path", ["/unknown-page/", "/api/unknown-endpoint/", "/courses/123/"])
def test_unknown_paths_do_not_fall_back_to_the_frontend(path):
    with pytest.raises(Resolver404):
        resolve(path)
