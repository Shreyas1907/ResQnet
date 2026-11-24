from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    ROLE_CHOICES = (
        ('citizen', 'Citizen'),
        ('ngo', 'NGO'),
        ('government', 'Government'),
    )

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        blank=False,
        null=False,
        help_text="Role required during registration"
    )
    reliability_score = models.FloatField(default=100)

    class Meta:
        indexes = [
            models.Index(fields=['role']),
        ]

    def __str__(self):
        return f"{self.username} ({self.role})"

    def is_citizen(self):
        return self.role == 'citizen'

    def is_ngo(self):
        return self.role == 'ngo'

    def is_government(self):
        return self.role == 'government'

class UserPreferences(models.Model):
    THEME_CHOICES = (('system', 'System'), ('light', 'Light'), ('dark', 'Dark'))
    DISASTER_CHOICES = (
        ('flood', 'Flood'),
        ('earthquake', 'Earthquake'),
        ('tsunami', 'Tsunami'),
        ('drought', 'Drought'),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferences')
    theme = models.CharField(max_length=10, choices=THEME_CHOICES, default='system')
    language = models.CharField(max_length=10, default='en')
    default_disaster_type = models.CharField(max_length=20, choices=DISASTER_CHOICES, default='flood')
    notify_email = models.BooleanField(default=True)
    notify_push = models.BooleanField(default=True)
    alert_radius_km = models.PositiveIntegerField(default=50)
    home_latitude = models.FloatField(null=True, blank=True)
    home_longitude = models.FloatField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Preferences for {self.user.username}"
