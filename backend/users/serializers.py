from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from .models import User, UserPreferences
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError
from django.contrib.auth.hashers import make_password
import math


class RegisterSerializer(serializers.ModelSerializer):
    """
    Serializer for user registration with role-based access.
    
    Required fields:
    - username: Must be unique and not empty
    - email: Valid email, required
    - password: Will be hashed using create_user()
    - role: Required choice (citizen, ngo, government)
    
    Does NOT return password in response.
    """
    password = serializers.CharField(
        write_only=True,
        required=True,
        style={'input_type': 'password'},
        help_text='Password will be hashed'
    )
    password_confirm = serializers.CharField(
        write_only=True,
        required=True,
        style={'input_type': 'password'}
    )

    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'email', 'password', 'password_confirm', 'role']
        extra_kwargs = {
            'username': {
                'required': False,
                'help_text': 'Defaults to the email address'
            },
            'first_name': {'required': False},
            'email': {
                'required': True,
                'help_text': 'Valid email is required'
            },
            'role': {
                'required': True,
                'help_text': 'Choose one: citizen, ngo, or government'
            }
        }

    def validate(self, attrs):
        """Validate password match and role selection."""
        password = attrs.get('password')
        password_confirm = attrs.get('password_confirm')

        # Check passwords match
        if password != password_confirm:
            raise serializers.ValidationError({
                'password': 'Passwords do not match.'
            })

        # Validate password strength
        try:
            validate_password(password)
        except serializers.ValidationError as e:
            raise serializers.ValidationError({
                'password': list(e.messages)
            })

        # Validate role selection
        if attrs.get('role') != 'citizen':
            raise serializers.ValidationError({
                'role': 'Self-registration is limited to citizen accounts. NGO and government accounts are issued by an administrator.'
            })

        attrs['email'] = attrs['email'].strip().lower()
        attrs['username'] = (attrs.get('username') or attrs['email']).strip()

        # Check for duplicate username
        if User.objects.filter(username__iexact=attrs['username']).exists():
            raise serializers.ValidationError({
                'username': 'This username is already taken.'
            })

        # Check for duplicate email
        if User.objects.filter(email__iexact=attrs['email']).exists():
            raise serializers.ValidationError({
                'email': 'This email is already registered.'
            })

        attrs.pop('password_confirm', None)
        return attrs

    def create(self, validated_data):
        """Create user using create_user() to ensure proper password hashing."""
        try:
            user = User.objects.create_user(
                username=validated_data['username'],
                email=validated_data['email'],
                password=validated_data['password'],
                role=validated_data['role'],
                first_name=validated_data.get('first_name', ''),
            )
            UserPreferences.objects.get_or_create(user=user)
            return user
        except IntegrityError as e:
            raise serializers.ValidationError({
                'detail': 'Registration failed. Please check your information.'
            })


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Extended JWT serializer that includes user role in token claims
    and response data.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields[self.username_field].required = False
        self.fields['email'] = serializers.EmailField(required=False, write_only=True)
        self.fields['role'] = serializers.ChoiceField(
            choices=User.ROLE_CHOICES, required=False, write_only=True
        )

    @classmethod
    def get_token(cls, user):
        """Add custom claims to JWT token."""
        token = super().get_token(user)

        # Add custom claims
        token['role'] = user.role
        token['username'] = user.username

        return token

    def validate(self, attrs):
        """
        Validate login credentials and ensure user is active.
        Only registered users can login.
        """
        invalid = serializers.ValidationError({
            'detail': 'Invalid username or password. Please check your credentials.'
        })
        email = attrs.pop('email', None)
        expected_role = attrs.pop('role', None)
        if email and not attrs.get(self.username_field):
            match = User.objects.filter(email__iexact=email).first()
            if match is None:
                make_password(attrs.get('password') or '')
                raise invalid
            attrs[self.username_field] = match.get_username()
        if not attrs.get(self.username_field):
            raise invalid

        try:
            data = super().validate(attrs)
        except (serializers.ValidationError, AuthenticationFailed):
            raise invalid

        # Check if user is active
        user = self.user
        if not user.is_active:
            raise serializers.ValidationError({
                'detail': 'This account has been deactivated.'
            })

        if expected_role and expected_role != user.role:
            raise invalid

        # Add user role to response
        data['role'] = user.role
        data['username'] = user.username
        data['user_id'] = user.id

        return data


class UserSerializer(serializers.ModelSerializer):
    """
    General user information serializer.
    Does NOT include password.
    """

    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'email', 'role', 'is_active', 'reliability_score']
        read_only_fields = ['id', 'username', 'email', 'role', 'is_active', 'reliability_score']


class PreferencesSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreferences
        fields = [
            'theme', 'language', 'default_disaster_type', 'notify_email', 'notify_push',
            'alert_radius_km', 'home_latitude', 'home_longitude', 'updated_at',
        ]
        read_only_fields = ['updated_at']
        extra_kwargs = {
            'alert_radius_km': {'min_value': 1, 'max_value': 500},
            'home_latitude': {'min_value': -90, 'max_value': 90},
            'home_longitude': {'min_value': -180, 'max_value': 180},
        }

    def validate(self, attrs):
        if self.instance is not None:
            merged = {
                'home_latitude': self.instance.home_latitude,
                'home_longitude': self.instance.home_longitude,
                **attrs,
            }
        else:
            merged = attrs
        for key in ('home_latitude', 'home_longitude'):
            value = merged.get(key)
            if value is not None and not math.isfinite(value):
                raise serializers.ValidationError({key: 'Must be a finite number.'})
        if (merged.get('home_latitude') is None) != (merged.get('home_longitude') is None):
            raise serializers.ValidationError('home_latitude and home_longitude must be set together.')
        return attrs