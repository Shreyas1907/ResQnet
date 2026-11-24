from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from .serializers import (
    RegisterSerializer,
    CustomTokenObtainPairSerializer,
    PreferencesSerializer,
    UserSerializer
)
from .models import User, UserPreferences
from .permissions import IsAnonymous


class RegisterView(generics.CreateAPIView):
    """
    Public endpoint for user registration.
    
    Required fields:
    - username (unique)
    - email (unique)
    - password
    - password_confirm
    - role (citizen, ngo, or government)
    
    Response:
    - User details (NO password returned)
    - Success message
    
    Errors:
    - 400: Validation errors (duplicate username, invalid role, etc.)
    """
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'register'

    def create(self, request, *args, **kwargs):
        """Customize response to include success message."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        return Response(
            {
                'success': True,
                'message': 'User registered successfully. You can now login.',
                'user': {
                    'id': serializer.data['id'],
                    'username': serializer.data['username'],
                    'email': serializer.data['email'],
                    'role': serializer.data['role']
                }
            },
            status=status.HTTP_201_CREATED
        )


class LoginView(TokenObtainPairView):
    """
    JWT login endpoint (replacing default TokenObtainPairView).
    
    Only registered users can login. Anonymous login is rejected.
    
    Required fields:
    - username
    - password
    
    Response on success:
    - access: JWT access token (valid 60 minutes)
    - refresh: JWT refresh token (valid 24 hours)
    - role: User's role (citizen, ngo, or government)
    - username: Username
    - user_id: User ID
    
    Errors:
    - 401: Invalid credentials or non-existent user
    - 400: Invalid role or inactive user
    """
    serializer_class = CustomTokenObtainPairSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request, *args, **kwargs):
        """Post request to obtain JWT tokens."""
        serializer = self.get_serializer(data=request.data)

        try:
            serializer.is_valid(raise_exception=True)
        except Exception as e:
            detail = getattr(e, 'detail', None)
            if isinstance(detail, dict):
                detail = next(iter(detail.values()), '')
            if isinstance(detail, (list, tuple)):
                detail = detail[0] if detail else ''
            return Response(
                {
                    'success': False,
                    'detail': str(detail if detail is not None else e)
                },
                status=status.HTTP_401_UNAUTHORIZED
            )

        return Response(
            {
                'success': True,
                'message': 'Login successful',
                'access': serializer.validated_data['access'],
                'refresh': serializer.validated_data['refresh'],
                'role': serializer.validated_data['role'],
                'username': serializer.validated_data['username'],
                'user_id': serializer.validated_data['user_id']
            },
            status=status.HTTP_200_OK
        )


class CurrentUserView(generics.RetrieveUpdateAPIView):
    """Authenticated user's profile; only first_name is editable."""
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_object(self):
        return self.request.user


class PreferencesView(generics.RetrieveUpdateAPIView):
    """Per-user preferences, created lazily with defaults."""
    serializer_class = PreferencesSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_object(self):
        prefs, _ = UserPreferences.objects.get_or_create(user=self.request.user)
        return prefs


class ThrottledTokenRefreshView(TokenRefreshView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'refresh'


class LogoutView(APIView):
    """Blacklists the supplied refresh token; possession of it is the credential, so no access token is needed."""
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'refresh'

    def post(self, request):
        refresh = request.data.get('refresh')
        if not refresh or not isinstance(refresh, str):
            return Response({'detail': 'refresh token is required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            RefreshToken(refresh).blacklist()
        except TokenError:
            return Response({'detail': 'Invalid or expired token.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_205_RESET_CONTENT)
