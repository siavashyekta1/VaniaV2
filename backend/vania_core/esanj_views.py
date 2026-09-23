import re
import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .esanj_client import EsanjAPIError, EsanjClient, EsanjConfigurationError
from .esanj_serializers import (
    EsanjAttemptSerializer,
    EsanjSaveAnswersSerializer,
    EsanjStartAttemptSerializer,
    EsanjSubmitAttemptSerializer,
    EsanjTestRuleSerializer,
    user_can_access_esanj_rule,
)
from .models import EsanjTestAccessRule, EsanjTestAttempt, EsanjUserProfile
from .tests_service import ClinicalTestsService
from billing.models import BillingConfig, Invoice


VAT_RATE = Decimal("10.00")


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _esanj_error_response(exc: Exception):
    if isinstance(exc, EsanjConfigurationError):
        return Response(
            {"error": "اتصال سرویس تست تعاملی هنوز در تنظیمات سرور کامل نشده است."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    if isinstance(exc, EsanjAPIError):
        http_status = status.HTTP_502_BAD_GATEWAY
        if exc.status_code == 403:
            http_status = status.HTTP_403_FORBIDDEN
        elif exc.status_code == 404:
            http_status = status.HTTP_404_NOT_FOUND
        return Response({"error": str(exc), "upstream_status": exc.status_code}, status=http_status)
    raise exc


def _extract_bank_item(item: dict) -> dict:
    test = item.get("test") if isinstance(item, dict) else {}
    test = test if isinstance(test, dict) else {}
    test_id = int(item.get("test_id") or test.get("id"))
    return {
        "esanj_test_id": test_id,
        "title": str(test.get("title") or item.get("title") or f"آزمون {test_id}"),
        "title_employee": str(test.get("title_employee") or ""),
        "base_price": test.get("base_price"),
        "upstream_payload": item,
        "last_synced_at": timezone.now(),
    }


def sync_esanj_test_bank(client: EsanjClient | None = None) -> tuple[int, int]:
    client = client or EsanjClient()
    had_enabled_rules = EsanjTestAccessRule.objects.filter(is_active=True).exists()
    created = 0
    updated = 0
    for item in client.test_bank():
        data = _extract_bank_item(item)
        test_id = data.pop("esanj_test_id")
        rule, was_created = EsanjTestAccessRule.objects.get_or_create(
            esanj_test_id=test_id,
            defaults={
                **data,
                "is_active": True,
                "allow_visitors": True,
                "allow_experts": True,
            },
        )
        if was_created:
            created += 1
        else:
            for field, value in data.items():
                setattr(rule, field, value)
            rule.save(update_fields=[*data.keys(), "updated_at"])
            updated += 1
    if not had_enabled_rules:
        EsanjTestAccessRule.objects.update(is_active=True, allow_visitors=True, allow_experts=True)
    return created, updated


def _allowed_rules_for_user(user):
    queryset = (
        EsanjTestAccessRule.objects.filter(is_active=True)
        .prefetch_related("eligible_expert_professions")
        .order_by("esanj_test_id")
    )
    return [rule for rule in queryset if user_can_access_esanj_rule(user, rule)]


def _get_allowed_rule_or_response(user, test_id: int):
    rule = (
        EsanjTestAccessRule.objects.prefetch_related("eligible_expert_professions")
        .filter(esanj_test_id=test_id)
        .first()
    )
    if not rule or not user_can_access_esanj_rule(user, rule):
        return None, Response({"error": "این آزمون برای حساب شما فعال نیست."}, status=status.HTTP_403_FORBIDDEN)
    return rule, None


def _get_active_rule_or_response(test_id: int):
    rule = EsanjTestAccessRule.objects.filter(esanj_test_id=test_id, is_active=True).first()
    if not rule:
        return None, Response({"error": "این آزمون فعال نیست."}, status=status.HTTP_403_FORBIDDEN)
    return rule, None


def _interactive_test_subtotal(rule: EsanjTestAccessRule) -> tuple[Decimal, Decimal, Decimal]:
    base_price = Decimal(str(rule.base_price or 0))
    markup_percent = Decimal(str(BillingConfig.load().esanj_test_markup_percent or 0))
    subtotal = _money(base_price * (Decimal("1") + (markup_percent / Decimal("100"))))
    return base_price, markup_percent, subtotal


def _invoice_for_interactive_test(user, rule: EsanjTestAccessRule):
    base_price, markup_percent, subtotal = _interactive_test_subtotal(rule)
    if subtotal <= 0:
        return None, {
            "base_price": base_price,
            "markup_percent": markup_percent,
            "subtotal": subtotal,
            "tax_amount": Decimal("0.00"),
            "total_amount": Decimal("0.00"),
        }

    content_type = ContentType.objects.get_for_model(rule)
    paid_invoice = Invoice.objects.filter(
        user=user,
        content_type=content_type,
        object_id=rule.id,
        status=Invoice.Status.PAID,
    ).order_by("-payment_date", "-created_at").first()
    if paid_invoice:
        return paid_invoice, {
            "base_price": base_price,
            "markup_percent": markup_percent,
            "subtotal": paid_invoice.subtotal_amount,
            "tax_amount": paid_invoice.tax_amount,
            "total_amount": paid_invoice.total_amount,
            "paid": True,
        }

    tax_amount = _money(subtotal * (VAT_RATE / Decimal("100")))
    pending_invoice = Invoice.objects.filter(
        user=user,
        content_type=content_type,
        object_id=rule.id,
        status__in=[Invoice.Status.PENDING, Invoice.Status.WAITING_APPROVAL],
    ).order_by("-created_at").first()
    if pending_invoice:
        if pending_invoice.status == Invoice.Status.PENDING:
            pending_invoice.subtotal_amount = subtotal
            pending_invoice.tax_rate = VAT_RATE
            pending_invoice.tax_amount = tax_amount
            pending_invoice.total_amount = subtotal + tax_amount
            pending_invoice.save(update_fields=["subtotal_amount", "tax_rate", "tax_amount", "total_amount"])
        return pending_invoice, {
            "base_price": base_price,
            "markup_percent": markup_percent,
            "subtotal": pending_invoice.subtotal_amount,
            "tax_amount": pending_invoice.tax_amount,
            "total_amount": pending_invoice.total_amount,
            "paid": False,
        }

    invoice = Invoice.objects.create(
        user=user,
        status=Invoice.Status.PENDING,
        subtotal_amount=subtotal,
        tax_rate=VAT_RATE,
        tax_amount=tax_amount,
        total_amount=subtotal + tax_amount,
        content_object=rule,
    )
    return invoice, {
        "base_price": base_price,
        "markup_percent": markup_percent,
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "total_amount": invoice.total_amount,
        "paid": False,
    }


def _payment_required_response(rule: EsanjTestAccessRule, invoice: Invoice, pricing: dict):
    return Response(
        {
            "error": "برای شروع این آزمون ابتدا پرداخت را تکمیل کنید.",
            "payment_required": True,
            "invoice_id": str(invoice.id),
            "redirect_url": f"/dashboard/invoices/{invoice.id}",
            "test": {
                "id": rule.id,
                "esanj_test_id": rule.esanj_test_id,
                "title": rule.title,
            },
            "pricing": {
                "base_price": str(pricing["base_price"]),
                "markup_percent": str(pricing["markup_percent"]),
                "subtotal_amount": str(pricing["subtotal"]),
                "tax_amount": str(pricing["tax_amount"]),
                "total_amount": str(pricing["total_amount"]),
            },
        },
        status=status.HTTP_402_PAYMENT_REQUIRED,
    )


def _user_has_paid_interactive_test(user, rule: EsanjTestAccessRule) -> bool:
    _, _, subtotal = _interactive_test_subtotal(rule)
    if subtotal <= 0:
        return True
    content_type = ContentType.objects.get_for_model(rule)
    return Invoice.objects.filter(
        user=user,
        content_type=content_type,
        object_id=rule.id,
        status=Invoice.Status.PAID,
    ).exists()


def _request_scope_value(request, data: dict, key: str, *headers: str):
    value = data.get(key)
    if value not in (None, ""):
        return value
    for header in headers:
        value = request.headers.get(header)
        if value not in (None, ""):
            return value
    value = request.query_params.get(key)
    return value if value not in (None, "") else None


def _safe_phone_for_esanj(user) -> str:
    digits = re.sub(r"\D", "", getattr(user, "phone_number", "") or "")
    if digits.startswith("98") and len(digits) == 12:
        digits = "0" + digits[2:]
    return digits if 10 <= len(digits) <= 11 else ""


def _normalize_esanj_phone(value: str | None) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("98") and len(digits) == 12:
        digits = "0" + digits[2:]
    return digits


def _jalali_birth_year_from_age(age: int | None) -> int | None:
    if not age:
        return None
    current_jalali_year = timezone.localdate().year - 621
    return current_jalali_year - int(age)


def _find_employee_by_phone(client: EsanjClient, phone_number: str) -> dict | None:
    if not phone_number:
        return None
    for employee in client.list_employees():
        if _normalize_esanj_phone(employee.get("phone_number")) == phone_number:
            return employee
    return None


def ensure_esanj_employee(user, client: EsanjClient, *, age: int | None = None, sex: str = "") -> EsanjUserProfile:
    profile, _ = EsanjUserProfile.objects.get_or_create(user=user)
    if profile.employee_id:
        return profile

    username = profile.employee_username or f"vania-{user.id}"
    phone_number = _safe_phone_for_esanj(user)
    employee = client.find_employee(username=username)
    if not employee:
        employee = _find_employee_by_phone(client, phone_number)
    if not employee:
        employee = client.create_employee(
            username=username,
            name=getattr(user, "full_name", "") or getattr(user, "phone_number", "") or username,
            phone_number=phone_number,
            sex=sex,
            birth_year=_jalali_birth_year_from_age(age),
        )
    if employee:
        profile.employee_id = employee.get("id") or profile.employee_id
        profile.employee_username = employee.get("username") or username
        profile.upstream_payload = employee
        profile.last_synced_at = timezone.now()
        profile.save(update_fields=["employee_id", "employee_username", "upstream_payload", "last_synced_at", "updated_at"])
    return profile


def _question_rows(questionnaire: dict) -> list[int]:
    questions = questionnaire.get("questions", []) if isinstance(questionnaire, dict) else []
    rows: list[int] = []
    if isinstance(questions, list):
        for question in questions:
            try:
                rows.append(int(question.get("row")))
            except (TypeError, ValueError, AttributeError):
                continue
    return rows


def _question_answer_rows(questionnaire: dict) -> dict[int, set[str]]:
    questions = questionnaire.get("questions", []) if isinstance(questionnaire, dict) else []
    rows_by_question: dict[int, set[str]] = {}
    if not isinstance(questions, list):
        return rows_by_question
    for question in questions:
        try:
            row = int(question.get("row"))
        except (TypeError, ValueError, AttributeError):
            continue
        answers = question.get("answers", []) if isinstance(question, dict) else []
        if isinstance(answers, list):
            rows_by_question[row] = {
                str(answer.get("row"))
                for answer in answers
                if isinstance(answer, dict)
                and answer.get("row") is not None
            }
    return rows_by_question


def _question_answer_row_by_value(questionnaire: dict) -> dict[int, dict[str, str]]:
    questions = questionnaire.get("questions", []) if isinstance(questionnaire, dict) else []
    rows_by_value: dict[int, dict[str, str]] = {}
    if not isinstance(questions, list):
        return rows_by_value
    for question in questions:
        try:
            row = int(question.get("row"))
        except (TypeError, ValueError, AttributeError):
            continue
        answers = question.get("answers", []) if isinstance(question, dict) else []
        if isinstance(answers, list):
            rows_by_value[row] = {
                str(answer.get("value")): str(answer.get("row"))
                for answer in answers
                if isinstance(answer, dict)
                and answer.get("row") is not None
                and answer.get("value") is not None
            }
    return rows_by_value


def _answers_payload(attempt: EsanjTestAttempt, answers: dict[str, str]) -> dict:
    payload = {"sex": attempt.sex, "age": attempt.age}
    questionnaire = attempt.questionnaire if isinstance(attempt.questionnaire, dict) else {}
    answer_storage = questionnaire.get("answer_storage", "value")
    answer_rows = _question_answer_rows(questionnaire)
    answer_row_by_value = _question_answer_row_by_value(questionnaire)
    for row in _question_rows(attempt.questionnaire):
        value = answers.get(str(row))
        if value is None:
            raise ValueError("همه سوال‌ها باید پاسخ داده شوند.")
        if answer_storage == "row":
            selected_row = str(value)
        else:
            selected_row = answer_row_by_value.get(row, {}).get(str(value))
        if selected_row is None or (answer_rows.get(row) and selected_row not in answer_rows[row]):
            raise ValueError("یکی از پاسخ‌ها با گزینه‌های آزمون سازگار نیست.")
        try:
            payload[f"q{row}"] = int(selected_row)
        except (TypeError, ValueError):
            payload[f"q{row}"] = selected_row
    return payload


def _is_html_attempt(attempt: EsanjTestAttempt) -> bool:
    questionnaire = attempt.questionnaire if isinstance(attempt.questionnaire, dict) else {}
    return questionnaire.get("delivery_mode") == EsanjStartAttemptSerializer.DeliveryMode.HTML


def _remote_esanj_attempt_is_done(client: EsanjClient, attempt: EsanjTestAttempt) -> bool:
    if not attempt.employee_id:
        return False
    for item in client.status_do(test_id=attempt.esanj_test_id, employee_id=attempt.employee_id):
        if str(item.get("uuid") or "") != str(attempt.id):
            continue
        return str(item.get("is_done")) in {"1", "true", "True"}
    return False


class EsanjTestCatalogView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        search = (request.query_params.get("search") or "").strip()
        rules = _allowed_rules_for_user(request.user)
        if search:
            normalized = search.casefold()
            rules = [
                rule
                for rule in rules
                if normalized in rule.title.casefold()
                or normalized in str(rule.esanj_test_id)
                or normalized in (rule.title_employee or "").casefold()
            ]
        content_type = ContentType.objects.get_for_model(EsanjTestAccessRule)
        paid_rule_ids = set(
            Invoice.objects.filter(
                user=request.user,
                content_type=content_type,
                status=Invoice.Status.PAID,
            ).values_list("object_id", flat=True)
        )
        serializer = EsanjTestRuleSerializer(
            rules,
            many=True,
            context={"request": request, "paid_rule_ids": paid_rule_ids},
        )
        return Response({"tests": serializer.data})


class EsanjTestQuestionnaireView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, test_id: int):
        _, error_response = _get_allowed_rule_or_response(request.user, test_id)
        if error_response:
            return error_response
        try:
            return Response(EsanjClient().questionnaire(test_id))
        except (EsanjConfigurationError, EsanjAPIError) as exc:
            return _esanj_error_response(exc)


class EsanjAttemptListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        attempts = EsanjTestAttempt.objects.filter(user=request.user).order_by("-started_at")
        serializer = EsanjAttemptSerializer(attempts, many=True)
        return Response({"attempts": serializer.data})

    @transaction.atomic
    def post(self, request):
        serializer = EsanjStartAttemptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        clinical_test_id = (data.get("clinical_test_id") or "").strip()
        doctor_id = _request_scope_value(request, data, "doctor_id", "X-Target-Expert-ID", "X-Target-Doctor-ID")
        case_id = _request_scope_value(request, data, "case_id", "X-Target-Case-ID")
        assignment = None

        if clinical_test_id:
            try:
                doctor_id = int(doctor_id) if doctor_id else None
            except (TypeError, ValueError):
                doctor_id = None
            assignment = ClinicalTestsService.get_test(
                request.user,
                clinical_test_id,
                doctor_id=doctor_id,
                case_id=case_id,
            )
            if not assignment or assignment.get("source") != "interactive" or not assignment.get("interactive_test_id"):
                return Response({"error": "تست تعاملی اختصاص داده‌شده پیدا نشد."}, status=status.HTTP_404_NOT_FOUND)
            data["test_id"] = int(assignment["interactive_test_id"])
            rule, error_response = _get_active_rule_or_response(data["test_id"])
        else:
            rule, error_response = _get_allowed_rule_or_response(request.user, data["test_id"])
        if error_response:
            return error_response

        if clinical_test_id:
            existing = (
                EsanjTestAttempt.objects.filter(user=request.user, clinical_test_id=clinical_test_id)
                .order_by("-started_at")
                .first()
            )
            # A failed local status can still represent a consumed/completed Esanj
            # UUID. Always resume the existing clinical attempt so starting again
            # cannot silently consume a second upstream credit.
            if existing:
                return Response(EsanjAttemptSerializer(existing).data)

        invoice, pricing = _invoice_for_interactive_test(request.user, rule)
        if invoice and invoice.status != Invoice.Status.PAID:
            return _payment_required_response(rule, invoice, pricing)

        client = EsanjClient()
        attempt_id = uuid.uuid4()
        try:
            # Direct purchases use the package/API account inventory. Sending an
            # Esanj employee id scopes the request to that employee and package
            # accounts reject the otherwise valid reservation with HTTP 403.
            # Keep employee scoping only for tests assigned through a clinical case.
            employee_id = None
            if clinical_test_id:
                profile = ensure_esanj_employee(request.user, client, age=data["age"], sex=data["sex"])
                employee_id = profile.employee_id
            if data["delivery_mode"] == EsanjStartAttemptSerializer.DeliveryMode.HTML:
                html = client.questionnaire_html(
                    test_id=rule.esanj_test_id,
                    sex=data["sex"],
                    age=data["age"],
                    uuid=str(attempt_id),
                    employee_id=employee_id,
                )
                questionnaire = {
                    "delivery_mode": EsanjStartAttemptSerializer.DeliveryMode.HTML,
                    "html": html,
                    "questions": [],
                }
            else:
                # Internal tests use Esanj's JSON flow from start to finish. Mixing
                # an HTML reservation with a JSON submission for the same UUID can
                # leave Esanj reporting a completed attempt without a JSON result.
                questionnaire = client.questionnaire(rule.esanj_test_id)
                if isinstance(questionnaire, dict):
                    questionnaire = {
                        **questionnaire,
                        "delivery_mode": EsanjStartAttemptSerializer.DeliveryMode.JSON,
                        "answer_storage": "row",
                    }
        except (EsanjConfigurationError, EsanjAPIError) as exc:
            return _esanj_error_response(exc)

        attempt = EsanjTestAttempt.objects.create(
            id=attempt_id,
            user=request.user,
            access_rule=rule,
            esanj_test_id=rule.esanj_test_id,
            test_title=rule.title,
            age=data["age"],
            sex=data["sex"],
            employee_id=employee_id,
            questionnaire=questionnaire,
            clinical_test_id=clinical_test_id,
            assigned_by_id=(assignment.get("assigned_by_user_id") or None) if assignment else None,
            doctor_id=doctor_id if clinical_test_id else None,
            case_id=case_id or "",
        )
        if clinical_test_id:
            ClinicalTestsService.update_interactive_assignment_from_attempt(request.user, attempt, creator=request.user)
        return Response(EsanjAttemptSerializer(attempt).data, status=status.HTTP_201_CREATED)


class EsanjAttemptDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_attempt(self, request, attempt_id):
        return EsanjTestAttempt.objects.filter(id=attempt_id, user=request.user).first()

    def get(self, request, attempt_id):
        attempt = self.get_attempt(request, attempt_id)
        if not attempt:
            return Response({"error": "سابقه آزمون پیدا نشد."}, status=status.HTTP_404_NOT_FOUND)
        return Response(EsanjAttemptSerializer(attempt).data)

    def patch(self, request, attempt_id):
        attempt = self.get_attempt(request, attempt_id)
        if not attempt:
            return Response({"error": "سابقه آزمون پیدا نشد."}, status=status.HTTP_404_NOT_FOUND)
        if attempt.status in {EsanjTestAttempt.Status.SUBMITTED, EsanjTestAttempt.Status.COMPLETED}:
            return Response({"error": "این آزمون قبلا ثبت شده است."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = EsanjSaveAnswersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        attempt.answers = {**(attempt.answers or {}), **serializer.validated_data["answers"]}
        attempt.status = EsanjTestAttempt.Status.IN_PROGRESS
        attempt.save(update_fields=["answers", "status", "updated_at"])
        return Response(EsanjAttemptSerializer(attempt).data)


class EsanjAttemptSubmitView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, attempt_id):
        attempt = EsanjTestAttempt.objects.select_for_update().filter(id=attempt_id, user=request.user).first()
        if not attempt:
            return Response({"error": "سابقه آزمون پیدا نشد."}, status=status.HTTP_404_NOT_FOUND)
        if attempt.status == EsanjTestAttempt.Status.COMPLETED:
            return Response(EsanjAttemptSerializer(attempt).data)

        serializer = EsanjSubmitAttemptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        answers = {**(attempt.answers or {}), **serializer.validated_data.get("answers", {})}

        client = EsanjClient()
        try:
            if _is_html_attempt(attempt):
                result = client.get_interpretation(str(attempt.id))
            else:
                if _remote_esanj_attempt_is_done(client, attempt):
                    try:
                        result = client.get_interpretation(str(attempt.id))
                    except EsanjAPIError as exc:
                        if exc.status_code != 404:
                            raise
                        return Response(
                            {
                                "error": (
                                    "سرویس Esanj این آزمون را تکمیل‌شده اعلام می‌کند، اما نتیجه آن قابل دریافت نیست. "
                                    "برای جلوگیری از مصرف دوباره اعتبار آزمون، ارسال مجدد انجام نشد؛ لطفاً با پشتیبانی بررسی شود."
                                )
                            },
                            status=status.HTTP_409_CONFLICT,
                        )
                else:
                    try:
                        payload = _answers_payload(attempt, answers)
                    except ValueError as exc:
                        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
                    result = client.submit_interpretation(
                        test_id=attempt.esanj_test_id,
                        uuid=str(attempt.id),
                        answers_payload=payload,
                        employee_id=attempt.employee_id,
                    )
            try:
                grading = client.get_grading(str(attempt.id))
            except EsanjAPIError:
                grading = {}
        except EsanjConfigurationError as exc:
            return _esanj_error_response(exc)
        except EsanjAPIError as exc:
            attempt.answers = answers
            attempt.status = EsanjTestAttempt.Status.FAILED
            attempt.error_message = str(exc)
            attempt.submitted_at = timezone.now()
            attempt.save(update_fields=["answers", "status", "error_message", "submitted_at", "updated_at"])
            return _esanj_error_response(exc)

        attempt.answers = answers
        attempt.result_json = result
        attempt.grading_json = grading
        attempt.status = EsanjTestAttempt.Status.COMPLETED
        attempt.error_message = ""
        now = timezone.now()
        attempt.submitted_at = now
        attempt.completed_at = now
        attempt.save(
            update_fields=[
                "answers",
                "result_json",
                "grading_json",
                "status",
                "error_message",
                "submitted_at",
                "completed_at",
                "updated_at",
            ]
        )
        ClinicalTestsService.update_interactive_assignment_from_attempt(request.user, attempt, creator=request.user)
        return Response(EsanjAttemptSerializer(attempt).data)


class EsanjSyncTestsView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request):
        try:
            created, updated = sync_esanj_test_bank()
        except (EsanjConfigurationError, EsanjAPIError) as exc:
            return _esanj_error_response(exc)
        return Response({"created": created, "updated": updated})
