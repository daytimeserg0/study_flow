from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.generics import CreateAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from users.serializers import RegistrationSerializer, UserSerializer


@extend_schema_view(post=extend_schema(tags=["Auth"], summary="Регистрация студента"))
class RegistrationView(CreateAPIView):
    serializer_class = RegistrationSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


@extend_schema_view(post=extend_schema(tags=["Auth"], summary="Получение пары JWT"))
class LoginView(TokenObtainPairView):
    pass


@extend_schema_view(post=extend_schema(tags=["Auth"], summary="Обновление access-токена"))
class RefreshView(TokenRefreshView):
    pass


@extend_schema_view(get=extend_schema(tags=["Users"], summary="Текущий пользователь"))
class CurrentUserView(RetrieveAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user
