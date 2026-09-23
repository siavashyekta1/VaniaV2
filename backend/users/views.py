import logging
from django.db import transaction, IntegrityError
from django.core import signing
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from rest_framework.exceptions import Throttled
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import CustomUser, UserProfile, UserRole, ExpertProfession
from billing.models import UserWallet
from .serializers import (
    PhoneSerializer, VerifyOTPSerializer, UserSerializer, PasswordLoginSerializer,
    UserProfileSerializer, ChangePasswordSerializer, UserWalletSerializer
)
from .serializers import CompleteSignupSerializer
from .phone_utils import normalize_and_validate_phone_number
from .otp_service import otp_service
from .throttles import RequestOTPThrottle, VerifyOTPThrottle, PasswordLoginThrottle
from .roles import (
    normalize_role_slug,
    CANONICAL_EXPERT_SLUG,
    CANONICAL_VISITOR_SLUG,
)
from .expert_validation import validate_profession_credential
from vania_core.profile_sync import sync_visitor_base_profile_identity
from billing.services import activate_default_expert_plan_for_transferred_credits
from vania_core.models import RoleVerificationRequest

logger = logging.getLogger(__name__)
SIGNUP_TOKEN_SALT = "users.signup"
SIGNUP_TOKEN_MAX_AGE_SECONDS = 15 * 60


def _normalize_national_code(value: str) -> str:
    translation = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    return (value or "").translate(translation).strip()


def _is_valid_iranian_national_code(value: str) -> bool:
    code = _normalize_national_code(value)
    if len(code) != 10 or not code.isdigit():
        return False
    if code == code[0] * 10:
        return False
    check = int(code[9])
    total = sum(int(code[i]) * (10 - i) for i in range(9))
    remainder = total % 11
    if remainder < 2:
        return check == remainder
    return check == (11 - remainder)

# --- AUTH FLOW STEP 1: CHECK EXISTENCE ---
class CheckUserExistenceView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        phone = request.data.get('phone_number')
        if not phone:
            return Response({"error": "شماره موبایل الزامی است."}, status=400)
        try:
            phone = normalize_and_validate_phone_number(phone)
        except DjangoValidationError as exc:
            return Response({"error": exc.messages[0]}, status=400)
        exists = CustomUser.objects.filter(phone_number=phone).exists()
        return Response({"exists": exists})

# --- AUTH FLOW STEP 2 (OPTIONAL): VERIFY DOCTOR ---
class VerifyDoctorView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        full_name = (request.data.get('full_name') or "").strip()
        national_code = _normalize_national_code(
            request.data.get("national_code")
            or request.data.get("meli_code")
            or ""
        )
        credential_code = (
            request.data.get('credential_code')
            or request.data.get('license_code')
            or ""
        )
        profession_slug = (request.data.get('profession_slug') or "psychologist").strip()

        profession = ExpertProfession.objects.filter(slug=profession_slug, is_active=True).first()
        if not profession:
            return Response(
                {"verified": False, "message": "حوزه تخصصی نامعتبر است", "found_name": None},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = validate_profession_credential(
            profession=profession,
            full_name=full_name,
            credential_code=str(credential_code).strip(),
        )

        return Response({
            "verified": result.verified,
            "message": result.message,
            "found_name": result.normalized_name,
            "profession_slug": profession.slug,
            "profession_label": profession.name,
            "meta": result.meta,
            "national_code": national_code or None,
        })


class ExpertProfessionListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        professions = ExpertProfession.objects.filter(is_active=True).order_by("name")
        def get_ui_text(p, key, fallback=""):
            cfg = p.validation_config or {}
            value = cfg.get(key)
            return value if isinstance(value, str) else fallback

        return Response([
            {
                "slug": p.slug,
                "name": p.name,
                "description": p.description,
                "validation_kind": p.validation_kind,
                "credential_label": get_ui_text(p, "credential_label", "کد اعتبارسنجی تخصص"),
                "credential_placeholder": get_ui_text(p, "credential_placeholder", "کد اعتبارسنجی تخصص را وارد کنید"),
                "credential_help": get_ui_text(p, "credential_help", ""),
                "sample_code": get_ui_text(p, "sample_code", ""),
                "university_required": bool((p.validation_config or {}).get("university_required")),
                "university_label": get_ui_text(p, "university_label", "نام دانشگاه"),
                "university_placeholder": get_ui_text(
                    p,
                    "university_placeholder",
                    "نام دانشگاه محل تحصیل را وارد کنید",
                ),
            }
            for p in professions
        ])

# --- AUTH FLOW STEP 3: REQUEST OTP ---
class RequestOTPView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [RequestOTPThrottle]

    def post(self, request):
        serializer = PhoneSerializer(data=request.data)
        if serializer.is_valid():
            phone_number = serializer.validated_data['phone_number']
            send_otp = serializer.validated_data.get("send_otp", True)
            user = CustomUser.objects.filter(phone_number=phone_number).first()
            has_password = bool(user and user.password and user.has_usable_password())
            try:
                if send_otp:
                    otp_service.send_otp(phone_number)
            except ValueError as exc:
                raise Throttled(detail=str(exc))

            payload = {
                "message": "کد تایید ارسال شد." if send_otp else "وضعیت شماره بررسی شد.",
                "otp_sent": bool(send_otp),
            }
            if not send_otp:
                payload.update({
                    "user_exists": bool(user),
                    "has_password": has_password,
                    "requires_otp": (not user) or (not has_password),
                })
            return Response(payload, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

# --- AUTH FLOW STEP 4: VERIFY OTP & CREATE USER ---
class VerifyOTPView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [VerifyOTPThrottle]

    def post(self, request):
        verification_serializer = VerifyOTPSerializer(data=request.data)
        if not verification_serializer.is_valid():
            return Response(verification_serializer.errors, status=400)

        phone_number = verification_serializer.validated_data.get('phone_number')
        otp_code = verification_serializer.validated_data.get('otp_code')

        if not otp_service.verify_otp(phone_number, otp_code):
            return Response({'detail': 'کد وارد شده نامعتبر یا منقضی شده است.'}, status=400)

        user = CustomUser.objects.filter(phone_number=phone_number).first()
        if user:
            refresh = RefreshToken.for_user(user)
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'user_created': False,
                'requires_signup': False,
                'role': normalize_role_slug(user.role.slug) if user.role else CANONICAL_VISITOR_SLUG,
            })

        signup_token = signing.dumps({"phone_number": phone_number}, salt=SIGNUP_TOKEN_SALT)
        return Response({
            'requires_signup': True,
            'signup_token': signup_token,
            'signup_token_expires_in': SIGNUP_TOKEN_MAX_AGE_SECONDS,
            'phone_number': phone_number,
        })


class CompleteSignupView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = CompleteSignupSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        signup_token = serializer.validated_data['signup_token']
        try:
            payload = signing.loads(
                signup_token,
                salt=SIGNUP_TOKEN_SALT,
                max_age=SIGNUP_TOKEN_MAX_AGE_SECONDS,
            )
        except signing.SignatureExpired:
            return Response(
                {"error": "زمان ثبت نام به پایان رسیده است.", "code": "signup_token_expired"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except signing.BadSignature:
            return Response(
                {"error": "توکن ثبت نام نامعتبر است.", "code": "invalid_signup_token"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        phone_number = payload.get("phone_number")
        if not phone_number:
            return Response(
                {"error": "توکن ثبت نام نامعتبر است.", "code": "invalid_signup_token"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = CompleteSignupSerializer(data=request.data, context={"phone_number": phone_number})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        if CustomUser.objects.filter(phone_number=phone_number).exists():
            return Response(
                {"error": "برای این شماره قبلا حساب ایجاد شده است.", "code": "account_exists"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = serializer.validated_data.get("email")
        if email and CustomUser.objects.filter(email=email).exists():
            return Response(
                {"email": ["کاربری با این ایمیل از قبل وجود دارد."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                user = CustomUser.objects.create_user(
                    phone_number=phone_number,
                    password=serializer.validated_data['password'],
                    full_name=serializer.validated_data.get('full_name'),
                    email=email,
                )
                role_obj, _ = UserRole.objects.get_or_create(
                    slug=CANONICAL_VISITOR_SLUG,
                    defaults={'name': 'مراجعه‌کننده'}
                )
                user.role = role_obj
                user.save(update_fields=["role"])
                sync_visitor_base_profile_identity(
                    user,
                    full_name=user.full_name or "",
                    email=user.email or "",
                )
        except DjangoValidationError as exc:
            return Response({"password": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError as exc:
            if 'email' in str(exc):
                return Response({"email": ["کاربری با این ایمیل از قبل وجود دارد."]}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"error": "خطا در ساخت کاربر."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        refresh = RefreshToken.for_user(user)
        return Response({
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user_created': True,
            'role': normalize_role_slug(user.role.slug) if user.role else CANONICAL_VISITOR_SLUG,
        }, status=status.HTTP_201_CREATED)

class PasswordLoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PasswordLoginThrottle]

    def post(self, request):
        serializer = PasswordLoginSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            user = serializer.validated_data['user']
            refresh = RefreshToken.for_user(user)
            return Response({'refresh': str(refresh), 'access': str(refresh.access_token)}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

# --- PROFILE VIEWS ---
class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)
    
    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        serializer.save()
        return Response(serializer.data)


class UpgradeExpertView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        full_name = (request.data.get("full_name") or request.user.full_name or "").strip()
        profession_slug = (request.data.get("profession_slug") or "").strip()
        national_code = _normalize_national_code(
            request.data.get("national_code")
            or request.data.get("meli_code")
            or ""
        )
        credential_code = (
            request.data.get("credential_code")
            or request.data.get("license_code")
            or ""
        )
        university_name = (request.data.get("university_name") or "").strip()

        if not profession_slug:
            return Response({"detail": "profession_slug is required."}, status=400)
        if not national_code:
            return Response({"detail": "national_code is required."}, status=400)
        if not _is_valid_iranian_national_code(national_code):
            return Response({"detail": "national_code is invalid."}, status=400)

        profession = ExpertProfession.objects.filter(slug=profession_slug, is_active=True).first()
        if not profession:
            return Response({"detail": "Invalid profession_slug."}, status=400)
        if (profession.validation_config or {}).get("university_required") and len(university_name) < 2:
            return Response({"detail": "university_name is required."}, status=400)

        result = validate_profession_credential(
            profession=profession,
            full_name=full_name,
            credential_code=str(credential_code).strip(),
        )
        if not result.verified:
            return Response(
                {
                    "verified": False,
                    "message": result.message,
                    "found_name": result.normalized_name,
                },
                status=400,
            )

        with transaction.atomic():
            expert_role, _ = UserRole.objects.get_or_create(
                slug=CANONICAL_EXPERT_SLUG,
                defaults={"name": "متخصص"},
            )
            user = request.user
            requires_manual_review = bool((result.meta or {}).get("manual_review"))
            request_snapshot = {
                **(result.meta or {}),
                "profession_slug": profession.slug,
                "profession_label": profession.name,
                "credential_code": str(credential_code).strip(),
                "national_code": national_code,
                "full_name": full_name,
                "validation_kind": profession.validation_kind,
                "university_name": university_name,
            }

            latest_request = None
            if requires_manual_review:
                RoleVerificationRequest.objects.filter(
                    user=user,
                    target_role=expert_role,
                    status=RoleVerificationRequest.Status.PENDING,
                ).update(
                    status=RoleVerificationRequest.Status.REJECTED,
                    admin_notes="Superseded by a newer verification submission.",
                )
                latest_request = RoleVerificationRequest.objects.create(
                    user=user,
                    target_role=expert_role,
                    data=request_snapshot,
                    status=RoleVerificationRequest.Status.PENDING,
                )

            if not requires_manual_review:
                user.role = expert_role
            user.expert_profession = profession
            user.national_code = national_code
            user.is_expert_verified = not requires_manual_review
            user.expert_verified_at = timezone.now() if not requires_manual_review else None
            user.expert_verification_meta = {
                **(result.meta or {}),
                "submitted_credential_code": str(credential_code).strip(),
                "submitted_national_code": national_code,
                "submitted_profession_slug": profession.slug,
                "submitted_university_name": university_name,
                "submitted_at": timezone.now().isoformat(),
                "validation_kind": profession.validation_kind,
                "admin_review_recommended": requires_manual_review,
                "status": "pending" if requires_manual_review else "approved",
                "latest_message": result.message,
                "role_verification_request_id": latest_request.id if latest_request else None,
            }
            # keep legacy fields in sync for temporary compatibility
            user.is_verified_doctor = not requires_manual_review
            user.medical_license = str(credential_code).strip() or user.medical_license
            if result.normalized_name and not user.full_name:
                user.full_name = result.normalized_name
            user.save()
            if not requires_manual_review:
                activate_default_expert_plan_for_transferred_credits(user)

        user.refresh_from_db()
        return Response(
            {
                "verified": True,
                "message": result.message,
                "profession_slug": profession.slug,
                "profession_label": profession.name,
                "user": UserSerializer(user).data,
            },
            status=200,
        )


class AdminExpertProfessionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not (request.user.is_staff or request.user.is_superuser):
            return Response({"detail": "Only admin users can select an expert profession this way."}, status=403)

        profession_slug = (request.data.get("profession_slug") or "").strip()
        if not profession_slug:
            return Response({"detail": "profession_slug is required."}, status=400)

        profession = ExpertProfession.objects.filter(slug=profession_slug, is_active=True).first()
        if not profession:
            return Response({"detail": "Invalid profession_slug."}, status=400)

        with transaction.atomic():
            user = request.user
            user.expert_profession = profession
            user.is_expert_verified = True
            user.is_verified_doctor = True
            user.expert_verified_at = timezone.now()
            user.expert_verification_meta = {
                **(user.expert_verification_meta or {}),
                "submitted_profession_slug": profession.slug,
                "status": "approved",
                "admin_bypass": True,
                "latest_message": "حوزه تخصصی ادمین بدون نیاز به اعتبارسنجی تنظیم شد.",
                "updated_at": timezone.now().isoformat(),
            }
            user.save(update_fields=[
                "expert_profession",
                "is_expert_verified",
                "is_verified_doctor",
                "expert_verified_at",
                "expert_verification_meta",
            ])

        user.refresh_from_db()
        return Response(
            {
                "message": "حوزه تخصصی ادمین بروزرسانی شد.",
                "profession_slug": profession.slug,
                "profession_label": profession.name,
                "user": UserSerializer(user).data,
            },
            status=200,
        )

class UserProfileDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer
    def get_object(self):
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        return profile

class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={'user': request.user})
        if serializer.is_valid():
            user = request.user
            user.set_password(serializer.validated_data['new_password'])
            user.save()
            return Response({"message": "رمز عبور با موفقیت به‌روزرسانی شد."}, status=200)
        return Response(serializer.errors, status=400)

class UserWalletDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserWalletSerializer
    def get_object(self):
        wallet, _ = UserWallet.objects.get_or_create(user=self.request.user)
        return wallet
