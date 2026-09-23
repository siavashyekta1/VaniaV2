from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from .models import CustomUser, UserProfile
from .password_policy import validate_password_policy
from .phone_utils import normalize_and_validate_phone_number, normalize_digits
from billing.models import UserWallet
from vania_core.profile_sync import sync_visitor_base_profile_identity
from .roles import (
    normalize_role_slug,
    CANONICAL_EXPERT_SLUG,
    CANONICAL_VISITOR_SLUG,
)

class PhoneSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)
    send_otp = serializers.BooleanField(required=False, default=True)

    def validate_phone_number(self, value):
        try:
            return normalize_and_validate_phone_number(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages[0])

class VerifyOTPSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)
    otp_code = serializers.CharField(max_length=6, min_length=6)

    def validate_phone_number(self, value):
        try:
            return normalize_and_validate_phone_number(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages[0])

    def validate_otp_code(self, value):
        normalized = normalize_digits(value)
        if not normalized.isdigit() or len(normalized) != 6:
            raise serializers.ValidationError("کد تایید باید ۶ رقم باشد.")
        return normalized

class PasswordLoginSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20)
    password = serializers.CharField(style={'input_type': 'password'}, trim_whitespace=False)

    def validate(self, attrs):
        phone = attrs.get('phone_number')
        pwd = attrs.get('password')
        if not phone or not pwd:
            raise serializers.ValidationError("شماره موبایل و رمز عبور الزامی است.")
        try:
            phone = normalize_and_validate_phone_number(phone)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"phone_number": exc.messages[0]})
        user = authenticate(request=self.context.get('request'), phone_number=phone, password=pwd)
        if not user:
            raise serializers.ValidationError("شماره موبایل یا رمز عبور نادرست است.")
        attrs['phone_number'] = phone
        attrs['user'] = user
        return attrs


class CompleteSignupSerializer(serializers.Serializer):
    signup_token = serializers.CharField()
    full_name = serializers.CharField(max_length=255, trim_whitespace=True)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    password = serializers.CharField(required=True, trim_whitespace=False, style={'input_type': 'password'})

    def validate_email(self, value):
        if value in ("", None):
            return None
        return value.strip().lower()

    def validate(self, attrs):
        phone_number = self.context.get("phone_number") or ""
        user = CustomUser(
            phone_number=phone_number,
            full_name=attrs.get("full_name") or "",
            email=attrs.get("email") or None,
        )

        try:
            validate_password_policy(attrs["password"], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": exc.messages})

        return attrs

class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    new_password = serializers.CharField(required=True, trim_whitespace=False)
    confirm_password = serializers.CharField(required=True, trim_whitespace=False)

    def validate(self, attrs):
        user = self.context.get('user')
        has_existing_password = bool(getattr(user, 'password', '')) and user.has_usable_password() if user else False

        if has_existing_password and not attrs.get('old_password'):
            raise serializers.ValidationError({"old_password": "رمز عبور فعلی الزامی است."})

        if has_existing_password and user and not user.check_password(attrs.get('old_password', '')):
            raise serializers.ValidationError({"old_password": "رمز عبور فعلی نادرست است."})

        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError({"confirm_password": "رمزهای عبور مطابقت ندارند."})

        try:
            validate_password_policy(attrs['new_password'], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": exc.messages})

        return attrs


class SignupDataSerializer(serializers.Serializer):
    fullName = serializers.CharField(max_length=255, trim_whitespace=True)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    password = serializers.CharField(required=True, trim_whitespace=False, style={'input_type': 'password'})

    def validate_email(self, value):
        if value in ("", None):
            return None
        return value.strip().lower()

    def validate(self, attrs):
        phone_number = self.context.get("phone_number") or ""
        user = CustomUser(
            phone_number=phone_number,
            full_name=attrs.get("fullName") or "",
            email=attrs.get("email") or None,
        )

        try:
            validate_password_policy(attrs["password"], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": exc.messages})

        return attrs

class UserWalletSerializer(serializers.ModelSerializer):
    balance_plan = serializers.CharField()
    balance_paid = serializers.CharField()
    daily_free_used = serializers.CharField()
    active_plan_name = serializers.CharField(source='active_plan.name', read_only=True, allow_null=True)
    
    class Meta:
        model = UserWallet
        fields = ('id', 'balance_plan', 'balance_paid', 'daily_free_used', 'updated_at', 'active_plan_name')

class UserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, style={'input_type': 'password'}, trim_whitespace=False)
    wallet = UserWalletSerializer(read_only=True)
    role_label = serializers.SerializerMethodField()
    role_slug = serializers.SerializerMethodField()
    has_password = serializers.SerializerMethodField()
    expert_profession_slug = serializers.SerializerMethodField()
    expert_profession_label = serializers.SerializerMethodField()
    expert_verification_status = serializers.SerializerMethodField()
    expert_verification_message = serializers.SerializerMethodField()
    expert_verification_requested_at = serializers.SerializerMethodField()
    expert_verification_can_retry = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = (
            'id', 'phone_number', 'email', 'full_name', 
            'date_joined', 'password', 'wallet',
            'is_staff', 'is_superuser',
            'role_label', 'role_slug', 'national_code', 'medical_license', 'is_verified_doctor',
            'is_expert_verified', 'expert_profession_slug', 'expert_profession_label',
            'has_all_expert_access',
            'expert_verification_status', 'expert_verification_message',
            'expert_verification_requested_at', 'expert_verification_can_retry',
            'has_password'
        )
        read_only_fields = (
            'phone_number', 'date_joined', 'id', 'role_label', 'role_slug',
            'is_staff', 'is_superuser',
            'national_code', 'is_verified_doctor', 'is_expert_verified', 'expert_profession_slug', 'expert_profession_label',
            'has_all_expert_access',
            'expert_verification_status', 'expert_verification_message', 'expert_verification_requested_at',
            'expert_verification_can_retry'
        )

    def validate_email(self, value):
        if value in ("", None):
            return None
        return value.strip().lower()

    def validate(self, attrs):
        password = attrs.get("password")
        if password:
            candidate_user = CustomUser(
                phone_number=getattr(self.instance, "phone_number", ""),
                full_name=attrs.get("full_name", getattr(self.instance, "full_name", "")) if self.instance else attrs.get("full_name", ""),
                email=attrs.get("email", getattr(self.instance, "email", None)) if self.instance else attrs.get("email", None),
            )

            try:
                validate_password_policy(password, user=candidate_user)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"password": exc.messages})

        return attrs

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        should_sync_base_profile = any(field in validated_data for field in ("full_name", "email"))
        instance = super().update(instance, validated_data)
        if password:
            instance.set_password(password)
            instance.save()
        if should_sync_base_profile:
            sync_visitor_base_profile_identity(
                instance,
                full_name=instance.full_name if "full_name" in validated_data else None,
                email=instance.email if "email" in validated_data else None,
            )
        return instance

    def get_has_password(self, obj):
        return bool(getattr(obj, 'password', '')) and obj.has_usable_password()

    def get_role_slug(self, obj):
        role = getattr(obj, "role", None)
        return normalize_role_slug(getattr(role, "slug", None))

    def get_role_label(self, obj):
        normalized = self.get_role_slug(obj)
        if normalized == CANONICAL_EXPERT_SLUG:
            return "متخصص"
        if normalized == CANONICAL_VISITOR_SLUG:
            return "مراجعه‌کننده"
        role = getattr(obj, "role", None)
        return getattr(role, "name", None)

    def get_expert_profession_slug(self, obj):
        profession = getattr(obj, "expert_profession", None)
        return getattr(profession, "slug", None)

    def get_expert_profession_label(self, obj):
        profession = getattr(obj, "expert_profession", None)
        return getattr(profession, "name", None)

    def get_expert_verification_status(self, obj):
        meta = getattr(obj, "expert_verification_meta", None) or {}
        if getattr(obj, "is_expert_verified", False):
            return "approved"
        status = meta.get("status")
        if status in {"pending", "rejected"}:
            return status
        if meta.get("manual_review") or meta.get("submitted_credential_code"):
            return "pending"
        return "none"

    def get_expert_verification_message(self, obj):
        meta = getattr(obj, "expert_verification_meta", None) or {}
        status = self.get_expert_verification_status(obj)
        if meta.get("latest_message"):
            return meta["latest_message"]
        if status == "approved":
            return "حساب متخصص شما تایید شده است."
        if status == "pending":
            return "درخواست شما ثبت شده و در انتظار بررسی تیم وانیا است."
        if status == "rejected":
            return "درخواست قبلی نیاز به اصلاح دارد. می‌توانید اطلاعات جدید ارسال کنید."
        return ""

    def get_expert_verification_requested_at(self, obj):
        meta = getattr(obj, "expert_verification_meta", None) or {}
        return meta.get("submitted_at")

    def get_expert_verification_can_retry(self, obj):
        status = self.get_expert_verification_status(obj)
        return status in {"pending", "rejected", "none"}

class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ('skin_type',)
