from django.urls import path

from users.views import CurrentUserView, LoginView, RefreshView, RegistrationView

app_name = "users"

urlpatterns = [
    path("auth/register/", RegistrationView.as_view(), name="register"),
    path("auth/token/", LoginView.as_view(), name="token-obtain-pair"),
    path("auth/token/refresh/", RefreshView.as_view(), name="token-refresh"),
    path("users/me/", CurrentUserView.as_view(), name="me"),
]
