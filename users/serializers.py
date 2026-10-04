from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from users.models import User


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "email", "first_name", "last_name", "role")
        read_only_fields = fields


class RegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    email = serializers.EmailField(required=True, max_length=254)

    class Meta:
        model = User
        fields = ("id", "username", "email", "password", "first_name", "last_name", "role")
        read_only_fields = ("id", "role")

    def validate(self, attrs):
        writable_fields = {name for name, field in self.fields.items() if not field.read_only}
        unexpected_fields = set(self.initial_data) - writable_fields
        if unexpected_fields:
            raise serializers.ValidationError(
                {name: "Это поле нельзя передавать при регистрации." for name in unexpected_fields}
            )

        user = User(**{key: value for key, value in attrs.items() if key != "password"})
        try:
            validate_password(attrs["password"], user=user)
        except DjangoValidationError as error:
            raise serializers.ValidationError({"password": error.messages}) from error
        return attrs

    def create(self, validated_data):
        return User.objects.create_user(**validated_data)
