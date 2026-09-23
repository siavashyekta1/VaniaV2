import jsonschema
import random
import string
from django.db import models
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.utils import timezone
from django.core.exceptions import ValidationError
from .password_policy import validate_password_policy
from .phone_utils import normalize_and_validate_phone_number

# --- 1. Minimal Role Model (Identity) ---
class UserRole(models.Model):
    """
    A simple tag to define user capability groups (e.g. 'expert', 'visitor').
    Used by the Frontend to toggle between 'Clinical View' and 'Personal View'.
    """
    name = models.CharField(max_length=50)
    slug = models.SlugField(unique=True) # e.g. 'expert', 'visitor'
    
    def __str__(self): return self.name


class ExpertProfession(models.Model):
    """
    Canonical expert profession definitions used for verification and access control.
    """
    slug = models.SlugField(unique=True)  # e.g. psychologist, psychiatrist, lawyer
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    validation_kind = models.CharField(max_length=100, default="mock")
    validation_config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

# --- 2. Custom User Model ---
class CustomUserManager(BaseUserManager):
    def create_user(self, phone_number, password=None, **extra_fields):
        if not phone_number:
            raise ValueError('The Phone Number must be set')

        normalized_phone_number = normalize_and_validate_phone_number(phone_number)
        user = self.model(phone_number=normalized_phone_number, **extra_fields)
        if password is not None:
            validate_password_policy(password, user=user)
            user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone_number, password, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
            
        return self.create_user(phone_number, password, **extra_fields)

class CustomUser(AbstractBaseUser, PermissionsMixin):
    """
    Custom user model where phone_number is the unique identifier.
    Includes Role context for Vania integration.
    """
    phone_number = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        help_text='The primary login identifier.'
    )
    email = models.EmailField(max_length=255, blank=True, null=True, unique=True)
    full_name = models.CharField(max_length=255, blank=True, null=True)
    national_code = models.CharField(max_length=10, blank=True, null=True, db_index=True)

    # The Active Context Role
    role = models.ForeignKey(
        UserRole, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='users',
        help_text="Designates the user's primary persona (e.g. Expert or Visitor)."
    )

    # --- [NEW] Doctor Verification Fields ---
    medical_license = models.CharField(max_length=50, blank=True, null=True)
    is_verified_doctor = models.BooleanField(default=False)
    expert_profession = models.ForeignKey(
        ExpertProfession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
    )
    is_expert_verified = models.BooleanField(default=False)
    expert_verified_at = models.DateTimeField(null=True, blank=True)
    expert_verification_meta = models.JSONField(default=dict, blank=True)
    has_all_expert_access = models.BooleanField(
        default=False,
        help_text="Grant access to every active expert assistant regardless of profession or plan inclusion.",
    )

    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = CustomUserManager()

    USERNAME_FIELD = 'phone_number'
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.phone_number

# --- 3. OTP Request Model (NEW - Required for Auth) ---
class OTPRequest(models.Model):
    phone_number = models.CharField(max_length=15)
    otp_code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        # Auto-generate code if missing
        if not self.otp_code:
            self.otp_code = ''.join(random.choices(string.digits, k=6))
        # Default expiry: 2 minutes
        if not self.expires_at:
            self.expires_at = timezone.now() + timezone.timedelta(minutes=2)
        super().save(*args, **kwargs)

    def is_valid(self, code):
        """Checks if code matches and is not expired."""
        now = timezone.now()
        return (
            self.otp_code == code and 
            now < self.expires_at and 
            not self.is_used
        )

    def __str__(self):
        return f"{self.phone_number} - {self.otp_code}"

# --- 4. User Profile (Legacy/AgentIQ Standard) ---
class UserProfile(models.Model):
    """
    Stores generic user preferences.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    skin_type = models.CharField(max_length=255, blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile for {self.user.phone_number}"

# --- 5. Structured Memory (Vania Requirements) ---

class ContextDefinition(models.Model):
    """
    Defines the schema for stored data types (e.g., 'clinical_session_log', 'patient_tasks').
    """
    key = models.SlugField(max_length=50, unique=True, help_text="Unique identifier for this data type")
    description = models.TextField(blank=True)
    is_public = models.BooleanField(default=False, help_text="Is this data visible to other users by default?")
    
    # Optional JSON Schema for validation
    validation_schema = models.JSONField(default=dict, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self): return self.key

class UserContextEntry(models.Model):
    """
    The persistent structured memory store.
    Vania tools write Clinical Notes, Tasks, and History Logs here.
    This acts as a NoSQL-like store attached to the User.
    """
    class SourceType(models.TextChoices):
        USER = 'USER', 'User Input'
        AGENT = 'AGENT', 'AI Agent'
        SYSTEM = 'SYSTEM', 'System Process'
        ADMIN = 'ADMIN', 'Admin Override'

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='context_entries')
    definition = models.ForeignKey(ContextDefinition, on_delete=models.PROTECT, related_name='entries')
    
    # The actual payload (e.g., {"score": 15, "notes": "Patient is anxious"})
    data = models.JSONField(default=dict)
    
    source = models.CharField(max_length=20, choices=SourceType.choices, default=SourceType.AGENT)
    created_by = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='authored_entries')
    
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_active = models.BooleanField(default=True, help_text="Set to False to soft-delete this specific entry")

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'definition', '-created_at']),
        ]
        verbose_name_plural = "User Context Entries"

    def clean(self):
        """Validate data against the definition schema if present."""
        if self.definition.validation_schema:
            try:
                jsonschema.validate(instance=self.data, schema=self.definition.validation_schema)
            except jsonschema.ValidationError as e:
                raise ValidationError(f"Invalid data format: {e.message}")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} -> {self.definition.key} ({self.created_at.strftime('%Y-%m-%d')})"
