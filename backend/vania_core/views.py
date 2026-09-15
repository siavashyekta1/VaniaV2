# backend/vania_core/views.py
import logging
import mimetypes
from botocore.exceptions import BotoCoreError, ClientError
from django.db.models import Q
from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.http import FileResponse, HttpResponse
from django.core.files.storage import default_storage
from django.utils.dateparse import parse_datetime
from django.shortcuts import redirect
from django.urls import reverse
from rest_framework import generics, status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser
import json
from typing import Optional
from django.utils import timezone
# --- Vania Core Imports ---
from .services import (
    PatientManagementService, 
    RoadmapService, 
    AppendixService, 
    TaskService, 
    SessionService,
    ProfileService,
)
from vania_core.patient_service import PatientDataService

from .medication_service import MedicationService
from .tests_service import ClinicalTestsService
from .case_files_service import CaseFilesService
from .flashcards import normalize_flashcards
from .models import (
    TreatmentConnection, 
    PatientInvite, 
    DoctorProfile, 
    Notification, 
    SecureMessage, 
    RoleVerificationRequest, 
    Location, 
    GoogleCalendarConnection,
    ExpertMeetingLink,
    PageTutorial,
    EsanjTestAccessRule,
    normalize_tutorial_path,
)
from .esanj_serializers import user_can_access_esanj_rule
from .permissions import IsDoctorUser, VaniaAccessControl
from .google_calendar import calendar_service, SCOPES
from .serializers import (
    # Existing Serializers
    InvitePatientSerializer, ConnectionListSerializer, RespondConnectionSerializer,
    PublicDoctorSerializer, AppointmentRequestSerializer, NotificationSerializer,
    SecureMessageSerializer, ConversationSerializer, RoleVerificationRequestSerializer,
    DoctorProfileUpdateSerializer, LocationSerializer,
    PatientLookupSerializer, UpdateConnectionStatusSerializer,
    
    # New VCOS Serializers
    TherapyRoadmapSerializer, CulturalResourceSerializer, AddSessionSerializer
)

# --- User Imports ---
from users.models import CustomUser, UserContextEntry
from users.roles import CANONICAL_EXPERT_SLUG, has_visitor_features, is_expert, normalize_role_slug
from capabilities.vania_visitor.forms import FORM_BASE_PROFILE
from .case_service import CaseService

# Configure logger for this module
logger = logging.getLogger(__name__)


def _resolve_expert_case_scope(request, patient, case_id: Optional[str]):
    """
    Returns (case_item, doctor_scope, can_edit) for expert requests.
    For read-only shared cases, doctor_scope is the owner expert id.
    """
    if not is_expert(request.user):
        return None, None, True
    if not case_id:
        return None, int(request.user.id), True
    case_item = CaseService.get_accessible_case_for_expert(patient, request.user, case_id)
    if not case_item:
        return None, None, False
    return case_item, int(case_item.get("doctor_id") or 0), bool(case_item.get("can_edit"))


def _build_google_redirect_uri(request):
    redirect_uri = request.build_absolute_uri(reverse("vania_core:google-calendar-callback"))
    if not settings.DEBUG:
        redirect_uri = redirect_uri.replace("http://", "https://")
    return redirect_uri


@staff_member_required
def google_calendar_login(request):
    from google_auth_oauthlib.flow import Flow

    config = GoogleCalendarConnection.get_solo()
    if not config.client_id or not config.client_secret:
        return HttpResponse("Please provide Client ID and Client Secret in admin first.", status=400)

    client_config = {
        "web": {
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=_build_google_redirect_uri(request),
    )
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    return redirect(auth_url)


@staff_member_required
def google_calendar_callback(request):
    from google_auth_oauthlib.flow import Flow

    config = GoogleCalendarConnection.get_solo()
    if "error" in request.GET:
        return HttpResponse(f"Google Auth Error: {request.GET.get('error')}", status=400)

    client_config = {
        "web": {
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=_build_google_redirect_uri(request),
    )

    try:
        authorization_response = request.build_absolute_uri()
        if not settings.DEBUG:
            authorization_response = authorization_response.replace("http://", "https://")

        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials
        config.token_json = json.loads(credentials.to_json())
        config.is_connected = True
        config.save(update_fields=["token_json", "is_connected", "updated_at"])
        return HttpResponse("Successfully connected to Google Calendar. You can close this tab.")
    except Exception as exc:
        logger.exception("Google OAuth callback failed.")
        return HttpResponse(f"Authentication failed: {exc}", status=500)

# ==============================================================================
# == 1. PATIENT & DOCTOR MANAGEMENT VIEWS
# ==============================================================================

class DoctorInvitePatientView(APIView):
    """
    Handles a doctor's request to add a new patient to their roster.
    This is the entry point for the "Add Patient" modal.
    """
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]
    
    def post(self, request):
        serializer = InvitePatientSerializer(data=request.data)
        if serializer.is_valid():
            data = serializer.validated_data
            
            success, message, patient, conn_status, activation_locked = PatientManagementService.invite_patient_by_phone(
                doctor_user=request.user, 
                phone_number=data['phone_number'], 
                full_name=data.get('full_name', '')
            )
            
            if success and patient:
                # Initialize the therapy roadmap for the new patient
                RoadmapService.get_or_create_roadmap(patient, doctor_id=request.user.id)
                
                return Response({
                    "message": message, 
                    "patient_id": patient.id,
                    "name": patient.full_name,
                    "status": conn_status,
                    "activation_locked": activation_locked
                }, status=status.HTTP_201_CREATED)
                
            else: 
                return Response({"error": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class DoctorDashboardPatientsView(APIView):
    """
    Provides a consolidated list of a doctor's active patients, pending requests,
    and sent invitations for their main dashboard and the patient selector dropdown.
    """
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]
    
    def get(self, request):
        user = request.user
        connections = TreatmentConnection.objects.filter(
            doctor=user,
            status__in=[
                TreatmentConnection.Status.ACTIVE,
                TreatmentConnection.Status.PENDING_PATIENT_APPROVAL,
                TreatmentConnection.Status.ARCHIVED
            ]
        ).select_related('patient')
        requests = TreatmentConnection.objects.filter(doctor=user, status=TreatmentConnection.Status.PENDING_DOCTOR_APPROVAL).select_related('patient')
        invites = PatientInvite.objects.filter(doctor=user, status=PatientInvite.InviteStatus.SENT)
        
        results = []
        for conn in connections:
            results.append({"id": f"conn_{conn.id}", "db_id": conn.id, "type": "CONNECTION", "patient_id": conn.patient.id, "name": conn.patient.full_name or "کاربر بدون نام", "phone": conn.patient.phone_number, "status": conn.status, "date": conn.created_at})
        for req in requests:
            results.append({"id": f"req_{req.id}", "db_id": req.id, "type": "REQUEST", "patient_id": req.patient.id, "name": req.patient.full_name or "کاربر بدون نام", "phone": req.patient.phone_number, "status": req.status, "date": req.created_at, "request_data": req.request_data})
        for inv in invites:
            results.append({"id": f"inv_{inv.id}", "db_id": inv.id, "type": "INVITE", "patient_id": None, "name": "کاربر دعوت شده", "phone": inv.phone_number, "status": "INVITED", "date": inv.created_at})
        
        results.sort(key=lambda x: x['date'], reverse=True)
        return Response(results)


class DoctorPatientLookupView(APIView):
    """
    Allows doctors to check whether a patient already exists by phone and if activation is currently locked.
    """
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]

    def post(self, request):
        serializer = PatientLookupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone = serializer.validated_data["phone_number"]
        patient = CustomUser.objects.filter(phone_number=phone).first()
        if not patient:
            return Response({"exists": False})

        existing_conn = TreatmentConnection.objects.filter(
            doctor=request.user,
            patient=patient
        ).first()

        return Response({
            "exists": True,
            "patient": {
                "id": patient.id,
                "full_name": patient.full_name or "کاربر بدون نام",
                "phone_number": patient.phone_number,
            },
            "existing_connection_status": existing_conn.status if existing_conn else None,
            "activation_locked": False,
        })

class PublicDoctorListView(generics.ListAPIView):
    """Provides a list of public doctor profiles for the 'Find a Doctor' directory."""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = PublicDoctorSerializer

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        complete_profile_ids = [
            profile.pk for profile in queryset
            if profile.is_public_profile_complete
        ]
        return queryset.filter(pk__in=complete_profile_ids)
    
    def get_queryset(self):
        queryset = DoctorProfile.objects.filter(is_public=True).select_related('user', 'location')
        
        specialty = self.request.query_params.get('specialty')
        profession = self.request.query_params.get('profession') or self.request.query_params.get('expert_profession')
        search = self.request.query_params.get('search')
        locations = self.request.query_params.getlist('locations')

        if locations and len(locations) == 1 and ',' in locations[0]:
             locations = locations[0].split(',')

        if locations:
            queryset = queryset.filter(location__id__in=locations)
        if specialty and specialty != 'ALL': 
            queryset = queryset.filter(specialty__icontains=specialty)
        if profession and profession != 'ALL':
            queryset = queryset.filter(user__expert_profession__slug=profession)
        if search: 
            queryset = queryset.filter(
                Q(user__full_name__icontains=search)
                | Q(specialty__icontains=search)
                | Q(user__expert_profession__name__icontains=search)
                | Q(location__name__icontains=search)
            ).distinct()
            
        return queryset

class DoctorProfileView(APIView):
    """Allows a doctor to view and update their own professional profile."""
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]
    parser_classes = [MultiPartParser, FormParser]
    serializer_class = DoctorProfileUpdateSerializer

    def get(self, request):
        profile, _ = DoctorProfile.objects.get_or_create(user=request.user)
        serializer = self.serializer_class(profile, context={'request': request})
        return Response(serializer.data)

    def patch(self, request):
        profile, _ = DoctorProfile.objects.get_or_create(user=request.user)
        serializer = self.serializer_class(profile, data=request.data, partial=True, context={'request': request})
        
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class MyBaseProfileView(APIView):
    """Allows a visitor to manage their shared base profile outside the canvas."""
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    def get(self, request):
        if not has_visitor_features(request.user):
            return Response({"error": "این بخش فقط برای مراجعان فعال است."}, status=status.HTTP_403_FORBIDDEN)

        entry = CaseService.get_latest_base_profile_entry(request.user)
        data = entry.data if entry and isinstance(entry.data, dict) else {}
        payload = {
            **data,
            "full_name": data.get("full_name") or request.user.full_name or "",
            "mobile_phone": data.get("mobile_phone") or request.user.phone_number or "",
            "email": data.get("email") or request.user.email or "",
            "form_key": FORM_BASE_PROFILE["key"],
            "form_title": FORM_BASE_PROFILE["title"],
        }
        return Response({
            "form": FORM_BASE_PROFILE,
            "data": payload,
        })

    def patch(self, request):
        if not has_visitor_features(request.user):
            return Response({"error": "این بخش فقط برای مراجعان فعال است."}, status=status.HTTP_403_FORBIDDEN)

        incoming = request.data if isinstance(request.data, dict) else {}
        existing_entry = CaseService.get_latest_base_profile_entry(request.user)
        existing_data = existing_entry.data if existing_entry and isinstance(existing_entry.data, dict) else {}
        payload = {
            **existing_data,
            **incoming,
            "form_key": FORM_BASE_PROFILE["key"],
            "form_title": FORM_BASE_PROFILE["title"],
            "visibility_scope": "SHARED_BASE",
            "case_id": None,
        }

        entry = CaseService.save_base_profile(request.user, payload, creator=request.user, source=UserContextEntry.SourceType.USER)

        updated_fields = []
        next_full_name = (payload.get("full_name") or "").strip()
        next_email = (payload.get("email") or "").strip()

        if next_full_name and next_full_name != (request.user.full_name or ""):
            request.user.full_name = next_full_name
            updated_fields.append("full_name")

        if next_email != (request.user.email or ""):
            if next_email:
                try:
                    validate_email(next_email)
                except ValidationError:
                    return Response({"error": "ایمیل وارد شده معتبر نیست."}, status=status.HTTP_400_BAD_REQUEST)
            request.user.email = next_email
            updated_fields.append("email")

        if updated_fields:
            request.user.save(update_fields=updated_fields)

        return Response({
            "status": "success",
            "data": entry.data,
        })


class CaseShareOptionsView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, case_id):
        if not has_visitor_features(request.user):
            return Response({"error": "Only visitors can manage case shares."}, status=status.HTTP_403_FORBIDDEN)
        payload = CaseService.get_case_share_options_for_patient(request.user, case_id)
        if not payload:
            return Response({"error": "Case not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(payload)


class CaseShareGrantView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, case_id):
        if not has_visitor_features(request.user):
            return Response({"error": "Only visitors can manage case shares."}, status=status.HTTP_403_FORBIDDEN)
        expert_id = request.data.get("expert_id") or request.data.get("doctor_id")
        if not expert_id:
            return Response({"error": "expert_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        grantee = get_object_or_404(CustomUser, pk=expert_id)
        try:
            payload = CaseService.grant_read_only_access(
                patient=request.user,
                case_id=case_id,
                grantee_doctor=grantee,
                granted_by=request.user,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload, status=status.HTTP_201_CREATED)

    def delete(self, request, case_id, expert_id=None):
        if not has_visitor_features(request.user):
            return Response({"error": "Only visitors can manage case shares."}, status=status.HTTP_403_FORBIDDEN)
        target_expert_id = expert_id or request.data.get("expert_id") or request.query_params.get("expert_id")
        if not target_expert_id:
            return Response({"error": "expert_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        revoked = CaseService.revoke_read_only_access(request.user, case_id, int(target_expert_id))
        if not revoked:
            return Response({"error": "Share not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"status": "revoked"})

# ==============================================================================
# == 2. CONNECTION & REQUEST MANAGEMENT VIEWS
# ==============================================================================

class RequestAppointmentView(APIView):
    """Endpoint for a patient to request an appointment with a doctor."""
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request, doctor_id):
        serializer = AppointmentRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        doctor_profile = get_object_or_404(DoctorProfile, pk=doctor_id)
        doctor_user = doctor_profile.user

        existing_conn = TreatmentConnection.objects.filter(doctor=doctor_user, patient=request.user).first()
        if existing_conn and existing_conn.status in [TreatmentConnection.Status.ACTIVE, TreatmentConnection.Status.PENDING_DOCTOR_APPROVAL]:
            return Response({"error": "You already have an active or pending connection with this doctor."}, status=400)

        form_data = serializer.validated_data
        TreatmentConnection.objects.create(
            doctor=doctor_user,
            patient=request.user,
            status=TreatmentConnection.Status.PENDING_DOCTOR_APPROVAL,
            request_data=form_data
        )

        Notification.objects.create(
            recipient=doctor_user, sender=request.user, type=Notification.Type.CONNECTION_REQUEST,
            title="درخواست نوبت جدید", message=f"مراجع {request.user.full_name} درخواست مشاوره ارسال کرده است.",
            payload={"url": "/dashboard/patients?tab=REQUESTS", "data": form_data}
        )

        return Response({"message": "درخواست شما برای متخصص ارسال شد."})

class PatientConnectionRequestsView(APIView):
    """Endpoint for a patient to view their pending connection requests from doctors."""
    permission_classes = [permissions.IsAuthenticated]
    
    def get(self, request):
        requests = TreatmentConnection.objects.filter(patient=request.user, status=TreatmentConnection.Status.PENDING_PATIENT_APPROVAL).select_related('doctor', 'doctor__doctor_profile')
        serializer = ConnectionListSerializer(requests, many=True, context={'request': request})
        return Response(serializer.data)

class RespondToConnectionView(APIView):
    """Endpoint for a patient to ACCEPT or REJECT a connection request."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, connection_id):
        connection = get_object_or_404(TreatmentConnection, id=connection_id, patient=request.user)
        serializer = RespondConnectionSerializer(data=request.data)
        if serializer.is_valid():
            action = serializer.validated_data['action']
            if action == 'ACCEPT':
                PatientManagementService.activate_connection_or_lock(connection)
            else:
                connection.status = TreatmentConnection.Status.REJECTED
                connection.save(update_fields=['status', 'updated_at'])
            return Response({"message": f"Connection {action.lower()}ed."})
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class DoctorRespondToRequestView(APIView):
    """Endpoint for a doctor to ACCEPT or REJECT a connection request from a patient."""
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]

    def post(self, request, connection_id):
        connection = get_object_or_404(TreatmentConnection, id=connection_id, doctor=request.user, status=TreatmentConnection.Status.PENDING_DOCTOR_APPROVAL)
        serializer = RespondConnectionSerializer(data=request.data)
        if serializer.is_valid():
            action = serializer.validated_data['action']
            if action == 'ACCEPT':
                PatientManagementService.activate_connection_or_lock(connection)
            else:
                connection.status = TreatmentConnection.Status.REJECTED
                connection.save(update_fields=['status', 'updated_at'])
            return Response({"message": f"Request {action.lower()}ed."})
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class DoctorUpdatePatientStatusView(APIView):
    """
    Allows doctors to activate/deactivate their patient connection.
    """
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]

    def post(self, request, connection_id):
        connection = get_object_or_404(TreatmentConnection, id=connection_id, doctor=request.user)
        serializer = UpdateConnectionStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        action = serializer.validated_data["action"]
        if action == "DEACTIVATE":
            connection.status = TreatmentConnection.Status.ARCHIVED
            connection.save(update_fields=["status", "updated_at"])
            return Response({
                "status": connection.status,
                "activation_locked": False,
                "message": "وضعیت مراجع غیرفعال شد."
            })

        _, locked = PatientManagementService.activate_connection_or_lock(connection)

        return Response({
            "status": connection.status,
            "activation_locked": locked,
            "message": "وضعیت مراجع فعال شد."
        })

# ==============================================================================
# == 3. MESSAGING & NOTIFICATION VIEWS
# ==============================================================================

class NotificationListView(generics.ListAPIView):
    """Lists notifications for the currently authenticated user."""
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = NotificationSerializer
    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user).order_by('-created_at')

class MarkNotificationReadView(APIView):
    """Marks a single notification as read."""
    permission_classes = [permissions.IsAuthenticated]
    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
        if not notification.is_read:
            notification.is_read = True
            notification.save()
        return Response({"status": "read"})

class MarkAllNotificationsReadView(APIView):
    """Marks all of the user's unread notifications as read."""
    permission_classes = [permissions.IsAuthenticated]
    def post(self, request):
        count = Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
        return Response({"status": "success", "count": count})

class ConversationListView(APIView):
    """Provides a summary of all active messaging threads for the user's inbox."""
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request):
        user = request.user
        counterpart_role = normalize_role_slug(request.query_params.get("counterpart_role"))
        connections = TreatmentConnection.objects.filter(
            Q(doctor=user) | Q(patient=user),
            status=TreatmentConnection.Status.ACTIVE
        ).select_related(
            'doctor',
            'doctor__role',
            'doctor__expert_profession',
            'patient',
            'patient__role',
            'patient__expert_profession',
            'doctor__doctor_profile',
        )
        results = []
        for conn in connections:
            other = conn.patient if conn.doctor == user else conn.doctor
            other_role_slug = normalize_role_slug(getattr(getattr(other, "role", None), "slug", None))
            other_is_verified_expert = bool(
                other_role_slug == CANONICAL_EXPERT_SLUG
                and getattr(other, "is_expert_verified", False)
            )

            if counterpart_role == CANONICAL_EXPERT_SLUG and not other_is_verified_expert:
                continue

            role_label = "متخصص" if other_is_verified_expert else "مراجعه‌کننده"

            try:
                profile = other.doctor_profile
            except DoctorProfile.DoesNotExist:
                profile = None

            specialty = profile.specialty if profile else ""
            expert_profession = getattr(other, "expert_profession", None)
            location_name = profile.location.name if profile and profile.location else ""
            clinic_address = profile.clinic_address if profile else ""
            meeting_price = profile.meeting_price if profile else 0
            accepting_new_patients = bool(profile.accepting_new_patients) if profile else False
            avatar = None
            if profile and profile.avatar:
                avatar = request.build_absolute_uri(profile.avatar.url)

            last_msg = SecureMessage.objects.filter(Q(sender=user, recipient=other) | Q(sender=other, recipient=user)).last()
            unread = SecureMessage.objects.filter(sender=other, recipient=user, is_read=False).count()
            results.append({
                "user_id": other.id,
                "name": other.full_name or other.phone_number,
                "phone_number": other.phone_number,
                "email": other.email,
                "avatar": avatar,
                "role_label": role_label,
                "role_slug": other_role_slug,
                "is_expert_verified": bool(getattr(other, "is_expert_verified", False)),
                "specialty": specialty,
                "expert_profession_slug": getattr(expert_profession, "slug", None),
                "expert_profession_label": getattr(expert_profession, "name", None),
                "location_name": location_name,
                "clinic_address": clinic_address,
                "meeting_price": meeting_price,
                "accepting_new_patients": accepting_new_patients,
                "last_message": last_msg.content if last_msg else "گفتگو را شروع کنید...",
                "last_message_date": last_msg.created_at if last_msg else conn.updated_at,
                "unread_count": unread,
            })
        results.sort(key=lambda x: x['last_message_date'], reverse=True)
        serializer = ConversationSerializer(results, many=True)
        return Response(serializer.data)

class MessageThreadView(APIView):
    """Handles retrieving message history and sending new messages within a thread."""
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request, other_user_id):
        messages = SecureMessage.objects.filter(Q(sender=request.user, recipient_id=other_user_id) | Q(sender_id=other_user_id, recipient=request.user))
        messages.filter(sender_id=other_user_id, is_read=False).update(is_read=True)
        serializer = SecureMessageSerializer(messages, many=True, context={'request': request})
        return Response(serializer.data)

    def post(self, request, other_user_id):
        serializer = SecureMessageSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save(sender=request.user, recipient_id=other_user_id)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CreateMeetLinkView(APIView):
    """
    Allows experts to generate a Google Meet link for an active visitor conversation.
    The response includes a Persian prefill message for the existing composer.
    """
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]
    parser_classes = [JSONParser]

    def post(self, request, other_user_id):
        visitor = get_object_or_404(CustomUser, pk=other_user_id)

        has_connection = TreatmentConnection.objects.filter(
            doctor=request.user,
            patient=visitor,
            status=TreatmentConnection.Status.ACTIVE,
        ).exists()
        if not has_connection:
            return Response(
                {"error": "You do not have an active connection with this visitor."},
                status=status.HTTP_403_FORBIDDEN,
            )

        config = GoogleCalendarConnection.get_solo()
        if not config.is_connected or not config.token_json:
            return Response(
                {"error": "Google Calendar is not connected in admin yet."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        raw_scheduled_at = request.data.get("scheduled_at")
        parsed_scheduled_at = parse_datetime(raw_scheduled_at) if raw_scheduled_at else None
        if raw_scheduled_at and parsed_scheduled_at is None:
            return Response({"error": "Scheduled time is invalid."}, status=status.HTTP_400_BAD_REQUEST)

        if parsed_scheduled_at and timezone.is_naive(parsed_scheduled_at):
            parsed_scheduled_at = timezone.make_aware(parsed_scheduled_at, timezone.get_current_timezone())

        started_at = parsed_scheduled_at or timezone.now()
        ends_at = started_at + timezone.timedelta(minutes=60)
        default_emails = [email for email in [request.user.email, visitor.email] if email]
        has_requested_emails = "attendee_emails" in request.data
        requested_emails = request.data.get("attendee_emails") if has_requested_emails else default_emails
        if not isinstance(requested_emails, list):
            return Response({"error": "Attendee emails must be a list."}, status=status.HTTP_400_BAD_REQUEST)

        attendee_emails: list[str] = []
        for email in requested_emails:
            if not email:
                continue
            normalized_email = str(email).strip().lower()
            if not normalized_email or normalized_email in attendee_emails:
                continue
            try:
                validate_email(normalized_email)
            except ValidationError:
                return Response(
                    {"error": f"Invalid attendee email: {normalized_email}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            attendee_emails.append(normalized_email)

        summary_name = visitor.full_name or visitor.phone_number or f"Visitor {visitor.id}"
        description = (
            f"Expert: {request.user.full_name or request.user.phone_number}\n"
            f"Visitor: {summary_name}\n"
            f"Created in Vania messaging."
        )

        try:
            meet_result = calendar_service.create_meet_event(
                summary=f"جلسه آنلاین | {summary_name}",
                description=description,
                started_at=started_at,
                ends_at=ends_at,
                attendee_emails=attendee_emails,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Meet creation failed for expert=%s visitor=%s", request.user.id, visitor.id)
            return Response(
                {"error": "Creating the Meet link failed. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        ExpertMeetingLink.objects.create(
            creator=request.user,
            visitor=visitor,
            google_event_id=meet_result.event_id,
            meet_link=meet_result.meet_link,
            attendee_emails=meet_result.attendee_emails,
            started_at=started_at,
            ends_at=ends_at,
        )

        prefill_message = (
            "سلام وقتتون بخیر\n"
            "اتاق جلسه آنلاین آماده شده و می‌تونید از طریق لینک زیر وارد شوید:\n"
            f"{meet_result.meet_link}\n"
            "لطفا در زمان هماهنگ‌شده وارد جلسه شوید."
        )

        return Response(
            {
                "meet_link": meet_result.meet_link,
                "google_event_id": meet_result.event_id,
                "attendee_emails": meet_result.attendee_emails,
                "prefill_message": prefill_message,
            }
        )

# ==============================================================================
# == 4. VCOS 6-PHASE PROTOCOL API VIEWS
# ==============================================================================

class RoadmapView(APIView):
    """API endpoint for managing the Therapy Roadmap."""
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]

    def get(self, request):
        patient_id = request.query_params.get('visitor_id') or request.query_params.get('patient_id')
        case_id = request.query_params.get("case_id") or request.headers.get("X-Target-Case-ID")
        if not patient_id:
            return Response({"error": "'patient_id' query parameter is required."}, status=status.HTTP_400_BAD_REQUEST)
        
        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return Response({"error": "Access denied to this patient's records."}, status=status.HTTP_403_FORBIDDEN)

        _, doctor_scope, _ = _resolve_expert_case_scope(request, patient, case_id)
        if case_id and not doctor_scope:
            return Response({"error": "Access denied for this case."}, status=status.HTTP_403_FORBIDDEN)
        roadmap = RoadmapService.get_or_create_roadmap(patient, doctor_id=doctor_scope or request.user.id, case_id=case_id)
        serializer = TherapyRoadmapSerializer(roadmap)
        return Response(serializer.data)

    def post(self, request):
        patient_id = request.data.get('visitor_id') or request.data.get('patient_id')
        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return Response({"error": "Access denied to this patient's records."}, status=status.HTTP_403_FORBIDDEN)
        if case_id and not CaseService.expert_can_edit_case(patient, request.user, case_id):
            return Response({"error": "This case is read-only for you."}, status=status.HTTP_403_FORBIDDEN)
        
        serializer = AddSessionSerializer(data=request.data)
        if serializer.is_valid():
            new_session = RoadmapService.add_session(
                patient=patient, 
                title=serializer.validated_data['title'],
                instructions=serializer.validated_data.get('instructions', ""),
                scheduled_date=serializer.validated_data.get('scheduled_date'),
                doctor_id=request.user.id,
                case_id=case_id,
            )
            return Response(new_session.model_dump(), status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def put(self, request):
        patient_id = request.data.get('visitor_id') or request.data.get('patient_id')
        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return Response({"error": "Access denied to this patient's records."}, status=status.HTTP_403_FORBIDDEN)
        if case_id and not CaseService.expert_can_edit_case(patient, request.user, case_id):
            return Response({"error": "This case is read-only for you."}, status=status.HTTP_403_FORBIDDEN)

        roadmap = RoadmapService.get_or_create_roadmap(patient, doctor_id=request.user.id, case_id=case_id)

        if "treatment_approaches" in request.data:
            raw_items = request.data.get("treatment_approaches") or []
            if not isinstance(raw_items, list):
                return Response({"error": "'treatment_approaches' must be a list."}, status=status.HTTP_400_BAD_REQUEST)
            roadmap.treatment_approaches = [str(item).strip() for item in raw_items if str(item).strip()]

        RoadmapService.save_roadmap(patient, roadmap, doctor_id=request.user.id, case_id=case_id)
        return Response(TherapyRoadmapSerializer(roadmap).data)

    def delete(self, request):
        patient_id = request.query_params.get('visitor_id') or request.query_params.get('patient_id')
        case_id = request.query_params.get("case_id") or request.headers.get("X-Target-Case-ID")
        session_number = request.query_params.get("session_number")
        if not patient_id or not session_number:
            return Response({"error": "'patient_id' and 'session_number' are required."}, status=status.HTTP_400_BAD_REQUEST)

        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return Response({"error": "Access denied to this patient's records."}, status=status.HTTP_403_FORBIDDEN)
        if case_id and not CaseService.expert_can_edit_case(patient, request.user, case_id):
            return Response({"error": "This case is read-only for you."}, status=status.HTTP_403_FORBIDDEN)

        roadmap = RoadmapService.get_or_create_roadmap(patient, doctor_id=request.user.id, case_id=case_id)
        target_session = next((item for item in roadmap.sessions if item.session_number == int(session_number)), None)
        if not target_session:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

        if target_session.doc_id:
            try:
                SessionService.delete_session(int(target_session.doc_id), request.user)
            except (TypeError, ValueError):
                logger.warning("Failed to soft-delete linked session log for roadmap session %s", session_number)

        deleted = RoadmapService.delete_session(
            patient,
            int(session_number),
            doctor_id=request.user.id,
            case_id=case_id,
        )
        if not deleted:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

        roadmap = RoadmapService.get_or_create_roadmap(patient, doctor_id=request.user.id, case_id=case_id)
        SessionReportView._refresh_visitor_dashboard_canvas(patient, request.user.id, case_id)
        return Response(TherapyRoadmapSerializer(roadmap).data)


class ClinicalTestsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def _is_doctor(self, user):
        return is_expert(user)

    def _require_doctor(self, request):
        if self._is_doctor(request.user):
            return None
        return Response({"error": "Only experts can modify tests."}, status=403)

    def _resolve_patient_doctor_scope(self, request) -> int | None:
        raw = (
            request.headers.get("X-Target-Expert-ID")
            or request.headers.get("X-Target-Doctor-ID")
            or request.query_params.get("expert_id")
            or request.query_params.get("doctor_id")
            or request.data.get("expert_id")
            or request.data.get("doctor_id")
        )
        if raw:
            try:
                candidate = int(raw)
            except (TypeError, ValueError):
                candidate = None
            if candidate and TreatmentConnection.objects.filter(
                patient=request.user,
                doctor_id=candidate,
                status=TreatmentConnection.Status.ACTIVE,
            ).exists():
                return candidate

        conn = TreatmentConnection.objects.filter(
            patient=request.user,
            status=TreatmentConnection.Status.ACTIVE,
        ).order_by("-updated_at").first()
        return conn.doctor_id if conn else None

    def get_patient(self, request):
        if not self._is_doctor(request.user):
            # Patients can only access their own test list.
            return request.user

        patient_id = (
            request.query_params.get("visitor_id")
            or request.query_params.get("patient_id")
            or request.data.get("visitor_id")
            or request.data.get("patient_id")
        )
        if not patient_id:
            return None

        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return None
        return patient

    def get(self, request):
        patient = self.get_patient(request)
        if patient is None:
            return Response({"error": "Access denied or missing patient_id."}, status=403)
        case_id = request.query_params.get("case_id") or request.headers.get("X-Target-Case-ID")
        if self._is_doctor(request.user):
            _, doctor_scope, _ = _resolve_expert_case_scope(request, patient, case_id)
            if case_id and not doctor_scope:
                return Response({"error": "Access denied for this case."}, status=403)
            doctor_scope = doctor_scope or request.user.id
        else:
            doctor_scope = self._resolve_patient_doctor_scope(request)
        return Response({
            "catalog": ClinicalTestsService.list_catalog(),
            "tests": ClinicalTestsService.get_tests(patient, doctor_id=doctor_scope, case_id=case_id),
        })

    def post(self, request):
        doctor_guard = self._require_doctor(request)
        if doctor_guard is not None:
            return doctor_guard

        patient = self.get_patient(request)
        if patient is None:
            return Response({"error": "Access denied or missing patient_id."}, status=403)

        catalog_id = request.data.get("catalog_id")
        title = request.data.get("title")
        url = request.data.get("url")
        result_summary = request.data.get("result_summary") or request.data.get("result_text")
        source = request.data.get("source")
        interactive_test_id = request.data.get("interactive_test_id")
        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
        if case_id and not CaseService.expert_can_edit_case(patient, request.user, case_id):
            return Response({"error": "This case is read-only for you."}, status=403)
        if source == "interactive" or interactive_test_id:
            try:
                interactive_test_id = int(interactive_test_id)
            except (TypeError, ValueError):
                return Response({"error": "interactive_test_id is required."}, status=400)
            rule = (
                EsanjTestAccessRule.objects.prefetch_related("eligible_expert_professions")
                .filter(esanj_test_id=interactive_test_id)
                .first()
            )
            if not rule or not user_can_access_esanj_rule(request.user, rule):
                return Response({"error": "This interactive test is not available for this expert."}, status=403)
            source = "interactive"
            title = rule.title
            url = ""
            result_summary = ""
        new_test = ClinicalTestsService.add_test(
            patient=patient,
            created_by=request.user,
            catalog_id=int(catalog_id) if catalog_id else None,
            title=title,
            url=url,
            result_summary=result_summary,
            doctor_id=request.user.id,
            case_id=case_id,
            source=source,
            interactive_test_id=interactive_test_id,
        )
        return Response(new_test, status=201)

    def put(self, request, test_id):
        patient = self.get_patient(request)
        if patient is None:
            return Response({"error": "Access denied or missing patient_id."}, status=403)

        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
        payload = dict(request.data)
        if self._is_doctor(request.user):
            _, doctor_scope, can_edit = _resolve_expert_case_scope(request, patient, case_id)
            if case_id and not doctor_scope:
                return Response({"error": "Access denied for this case."}, status=403)
            if case_id and not can_edit:
                return Response({"error": "This case is read-only for you."}, status=403)
            doctor_scope = doctor_scope or request.user.id
        else:
            doctor_scope = self._resolve_patient_doctor_scope(request)
        if not self._is_doctor(request.user):
            payload = {
                "result_text": request.data.get("result_text", request.data.get("result_summary", "")),
                "result_summary": request.data.get("result_text", request.data.get("result_summary", "")),
            }
        updated = ClinicalTestsService.update_test(
            patient=patient,
            created_by=request.user,
            test_id=test_id,
            payload=payload,
            doctor_id=doctor_scope,
            case_id=case_id,
        )
        if not updated:
            return Response({"error": "Test not found."}, status=404)
        return Response(updated)

    def delete(self, request, test_id):
        doctor_guard = self._require_doctor(request)
        if doctor_guard is not None:
            return doctor_guard

        patient = self.get_patient(request)
        if patient is None:
            return Response({"error": "Access denied or missing patient_id."}, status=403)

        case_id = request.query_params.get("case_id") or request.headers.get("X-Target-Case-ID")
        if case_id and not CaseService.expert_can_edit_case(patient, request.user, case_id):
            return Response({"error": "This case is read-only for you."}, status=403)
        deleted = ClinicalTestsService.delete_test(
            patient=patient,
            created_by=request.user,
            test_id=test_id,
            doctor_id=request.user.id,
            case_id=case_id,
        )
        if not deleted:
            return Response({"error": "Test not found."}, status=404)
        return Response(status=204)


class ClinicalTestFileUploadView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def _resolve_patient(self, request):
        is_doctor = is_expert(request.user)
        if not is_doctor:
            return request.user

        patient_id = (
            request.data.get("visitor_id")
            or request.data.get("patient_id")
            or request.query_params.get("visitor_id")
            or request.query_params.get("patient_id")
            or request.headers.get("X-Target-Resource-ID")
        )
        if not patient_id:
            return None
        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return None
        return patient

    def _resolve_doctor_scope(self, request, patient) -> int | None:
        is_doctor = is_expert(request.user)
        if is_doctor:
            return request.user.id

        raw = (
            request.headers.get("X-Target-Expert-ID")
            or request.headers.get("X-Target-Doctor-ID")
            or request.data.get("expert_id")
            or request.data.get("doctor_id")
        )
        if raw:
            try:
                candidate = int(raw)
            except (TypeError, ValueError):
                candidate = None
            if candidate and TreatmentConnection.objects.filter(
                patient=patient,
                doctor_id=candidate,
                status=TreatmentConnection.Status.ACTIVE,
            ).exists():
                return candidate

        conn = TreatmentConnection.objects.filter(
            patient=patient,
            status=TreatmentConnection.Status.ACTIVE,
        ).order_by("-updated_at").first()
        return conn.doctor_id if conn else None

    def post(self, request, test_id):
        patient = self._resolve_patient(request)
        if patient is None:
            return Response({"error": "Access denied or missing patient_id."}, status=403)
        is_doctor = is_expert(request.user)

        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response({"error": "File is required."}, status=400)

        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")

        try:
            if is_doctor:
                _, doctor_scope, can_edit = _resolve_expert_case_scope(request, patient, case_id)
                if case_id and not doctor_scope:
                    return Response({"error": "Access denied for this case."}, status=403)
                if case_id and not can_edit:
                    return Response({"error": "This case is read-only for you."}, status=403)
                doctor_scope = doctor_scope or request.user.id
            else:
                doctor_scope = self._resolve_doctor_scope(request, patient)
            updated = ClinicalTestsService.attach_test_file(
                patient=patient,
                created_by=request.user,
                test_id=test_id,
                uploaded_file=uploaded_file,
                doctor_id=doctor_scope,
                case_id=case_id,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)
        except (ClientError, BotoCoreError):
            logger.exception("Clinical test PDF upload failed due to object storage error.")
            return Response(
                {"error": "File storage is unavailable right now. Please try again shortly."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not updated:
            return Response({"error": "Test not found."}, status=404)

        return Response(updated)


class ClinicalTestFileDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, test_id):
        is_doctor = is_expert(request.user)
        if is_doctor:
            patient_id = request.query_params.get("visitor_id") or request.query_params.get("patient_id")
            patient = get_object_or_404(CustomUser, pk=patient_id)
            if not VaniaAccessControl.verify_doctor_access(request.user, patient):
                return Response({"error": "Access denied."}, status=403)
            case_id = request.query_params.get("case_id") or request.headers.get("X-Target-Case-ID")
            _, doctor_scope, can_edit = _resolve_expert_case_scope(request, patient, case_id)
            if case_id and not doctor_scope:
                return Response({"error": "Access denied for this case."}, status=403)
            if case_id and not can_edit:
                return Response({"error": "This case is read-only for you."}, status=403)
            doctor_scope = doctor_scope or request.user.id
        else:
            patient = request.user
            raw = (
                request.headers.get("X-Target-Expert-ID")
                or request.headers.get("X-Target-Doctor-ID")
                or request.query_params.get("expert_id")
                or request.query_params.get("doctor_id")
            )
            doctor_scope = int(raw) if raw and str(raw).isdigit() else None

        case_id = request.query_params.get("case_id") or request.headers.get("X-Target-Case-ID")
        attachment_id = request.query_params.get("attachment_id")
        removed = ClinicalTestsService.remove_test_file(
            patient=patient,
            created_by=request.user,
            test_id=test_id,
            attachment_id=attachment_id,
            doctor_id=doctor_scope,
            case_id=case_id,
        )
        if not removed:
            return Response({"error": "Test not found."}, status=404)

        return Response({"status": "deleted"})


class ClinicalTestFileDownloadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _resolve_patient(self, request):
        is_doctor = is_expert(request.user)
        if not is_doctor:
            return request.user

        patient_id = request.query_params.get("visitor_id") or request.query_params.get("patient_id")
        if not patient_id:
            return None
        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return None
        return patient

    def get(self, request, test_id):
        patient = self._resolve_patient(request)
        if patient is None:
            return Response({"error": "Access denied or missing patient_id."}, status=403)

        case_id = request.query_params.get("case_id") or request.headers.get("X-Target-Case-ID")
        is_doctor = is_expert(request.user)
        if is_doctor:
            _, doctor_scope, _ = _resolve_expert_case_scope(request, patient, case_id)
            if case_id and not doctor_scope:
                return Response({"error": "Access denied for this case."}, status=403)
            doctor_scope = doctor_scope or request.user.id
        else:
            doctor_scope = None
            raw = (
                request.headers.get("X-Target-Expert-ID")
                or request.headers.get("X-Target-Doctor-ID")
                or request.query_params.get("expert_id")
                or request.query_params.get("doctor_id")
            )
            if raw:
                try:
                    candidate = int(raw)
                except (TypeError, ValueError):
                    candidate = None
                if candidate and TreatmentConnection.objects.filter(
                    patient=patient,
                    doctor_id=candidate,
                    status=TreatmentConnection.Status.ACTIVE,
                ).exists():
                    doctor_scope = candidate
            if not doctor_scope:
                conn = TreatmentConnection.objects.filter(
                    patient=patient,
                    status=TreatmentConnection.Status.ACTIVE,
                ).order_by("-updated_at").first()
                doctor_scope = conn.doctor_id if conn else None

        attachment_id = request.query_params.get("attachment_id")
        attachment = ClinicalTestsService.get_test_attachment(
            patient,
            test_id,
            attachment_id=attachment_id,
            doctor_id=doctor_scope,
            case_id=case_id,
        )
        if not attachment or not attachment.get("file_path"):
            return Response({"error": "File not found."}, status=404)

        storage_path = attachment["file_path"]
        if not default_storage.exists(storage_path):
            return Response({"error": "Stored file missing."}, status=404)

        file_name = attachment.get("file_name") or "test-result"
        file_obj = default_storage.open(storage_path, "rb")
        content_type = attachment.get("content_type") or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        response = FileResponse(file_obj, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename=\"{file_name}\"'
        return response


class CaseFilesView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def _resolve_patient(self, request):
        if not is_expert(request.user):
            return request.user

        patient_id = (
            request.query_params.get("visitor_id")
            or request.query_params.get("patient_id")
            or request.data.get("visitor_id")
            or request.data.get("patient_id")
        )
        if not patient_id:
            return None
        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return None
        return patient

    def _resolve_doctor_scope(self, request, patient) -> Optional[int]:
        if is_expert(request.user):
            case_id = (
                request.query_params.get("case_id")
                or request.headers.get("X-Target-Case-ID")
                or request.data.get("case_id")
            )
            _, doctor_scope, _ = _resolve_expert_case_scope(request, patient, case_id)
            return doctor_scope or int(request.user.id)

        raw = (
            request.headers.get("X-Target-Expert-ID")
            or request.headers.get("X-Target-Doctor-ID")
            or request.query_params.get("expert_id")
            or request.query_params.get("doctor_id")
            or request.data.get("expert_id")
            or request.data.get("doctor_id")
        )
        if raw:
            try:
                candidate = int(raw)
            except (TypeError, ValueError):
                candidate = None
            if candidate and TreatmentConnection.objects.filter(
                patient=patient,
                doctor_id=candidate,
                status=TreatmentConnection.Status.ACTIVE,
            ).exists():
                return candidate

        conn = TreatmentConnection.objects.filter(
            patient=patient,
            status=TreatmentConnection.Status.ACTIVE,
        ).order_by("-updated_at").first()
        return int(conn.doctor_id) if conn else None

    def get(self, request, file_id=None):
        patient = self._resolve_patient(request)
        if patient is None:
            return Response({"error": "Access denied or missing patient_id."}, status=403)

        doctor_scope = self._resolve_doctor_scope(request, patient)
        case_id = request.query_params.get("case_id") or request.headers.get("X-Target-Case-ID")
        if is_expert(request.user) and case_id and not CaseService.expert_can_view_case(patient, request.user, case_id):
            return Response({"error": "Access denied for this case."}, status=403)
        if not doctor_scope or not case_id:
            return Response({"error": "doctor_id and case_id are required."}, status=400)

        if file_id:
            payload = CaseFilesService.get_file_details(patient, doctor_scope, case_id, file_id)
            if not payload:
                return Response({"error": "File not found."}, status=404)
            return Response(payload)

        page = int(request.query_params.get("page", "1") or 1)
        page_size = int(request.query_params.get("page_size", "10") or 10)
        query = request.query_params.get("query")
        file_type = request.query_params.get("file_type")
        readable_only = str(request.query_params.get("readable_only", "")).lower() in {"1", "true", "yes"}
        sort = request.query_params.get("sort", "recent")
        payload = CaseFilesService.list_files(
            patient=patient,
            doctor_id=doctor_scope,
            case_id=case_id,
            page=page,
            page_size=page_size,
            query=query,
            file_type=file_type,
            readable_only=readable_only,
            sort=sort,
        )
        return Response(payload)

    def post(self, request, file_id=None):
        patient = self._resolve_patient(request)
        if patient is None:
            return Response({"error": "Access denied or missing patient_id."}, status=403)

        doctor_scope = self._resolve_doctor_scope(request, patient)
        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
        if is_expert(request.user) and case_id and not CaseService.expert_can_edit_case(patient, request.user, case_id):
            return Response({"error": "This case is read-only for you."}, status=403)
        if not doctor_scope or not case_id:
            return Response({"error": "doctor_id and case_id are required."}, status=400)

        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response({"error": "File is required."}, status=400)

        try:
            record = CaseFilesService.create_file(
                patient=patient,
                created_by=request.user,
                doctor_id=doctor_scope,
                case_id=case_id,
                uploaded_file=uploaded_file,
                name=request.data.get("name", ""),
                description=request.data.get("description", ""),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)
        except (ClientError, BotoCoreError):
            logger.exception("Case file upload failed due to object storage error.")
            return Response(
                {"error": "File storage is unavailable right now. Please try again shortly."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(record, status=201)

    def delete(self, request, file_id):
        patient = self._resolve_patient(request)
        if patient is None:
            return Response({"error": "Access denied or missing patient_id."}, status=403)

        doctor_scope = self._resolve_doctor_scope(request, patient)
        case_id = request.query_params.get("case_id") or request.headers.get("X-Target-Case-ID")
        if is_expert(request.user) and case_id and not CaseService.expert_can_edit_case(patient, request.user, case_id):
            return Response({"error": "This case is read-only for you."}, status=403)
        if not doctor_scope or not case_id:
            return Response({"error": "doctor_id and case_id are required."}, status=400)

        deleted = CaseFilesService.delete_file(
            patient=patient,
            created_by=request.user,
            doctor_id=doctor_scope,
            case_id=case_id,
            file_id=file_id,
        )
        if not deleted:
            return Response({"error": "File not found."}, status=404)
        return Response({"status": "deleted"})


class CaseFileDownloadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _resolve_patient(self, request):
        if not is_expert(request.user):
            return request.user

        patient_id = request.query_params.get("visitor_id") or request.query_params.get("patient_id")
        if not patient_id:
            return None
        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return None
        return patient

    def get(self, request, file_id):
        patient = self._resolve_patient(request)
        if patient is None:
            return Response({"error": "Access denied or missing patient_id."}, status=403)

        case_id = request.query_params.get("case_id") or request.headers.get("X-Target-Case-ID")
        if is_expert(request.user):
            _, doctor_scope, _ = _resolve_expert_case_scope(request, patient, case_id)
            if case_id and not doctor_scope:
                return Response({"error": "Access denied for this case."}, status=403)
            doctor_scope = doctor_scope or int(request.user.id)
        else:
            raw = (
                request.headers.get("X-Target-Expert-ID")
                or request.headers.get("X-Target-Doctor-ID")
                or request.query_params.get("expert_id")
                or request.query_params.get("doctor_id")
            )
            try:
                doctor_scope = int(raw) if raw else None
            except (TypeError, ValueError):
                doctor_scope = None
        if not doctor_scope or not case_id:
            return Response({"error": "doctor_id and case_id are required."}, status=400)

        record = CaseFilesService.get_file(patient, doctor_scope, case_id, file_id)
        if not record or not record.get("storage_path"):
            return Response({"error": "File not found."}, status=404)
        if not default_storage.exists(record["storage_path"]):
            return Response({"error": "Stored file missing."}, status=404)

        file_name = record.get("original_file_name") or record.get("name") or "case-file"
        content_type = record.get("content_type") or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        file_obj = default_storage.open(record["storage_path"], "rb")
        response = FileResponse(file_obj, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{file_name}"'
        return response


class AppendixView(APIView):
    """API endpoint for managing the Thought Appendix."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not is_expert(request.user):
            return Response({"error": "Only experts can browse appendix data through this endpoint."}, status=status.HTTP_403_FORBIDDEN)
        patient_id = request.query_params.get('visitor_id') or request.query_params.get('patient_id')
        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return Response({"error": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
            
        case_id = request.query_params.get("case_id") or request.headers.get("X-Target-Case-ID")
        _, doctor_scope, _ = _resolve_expert_case_scope(request, patient, case_id)
        if case_id and not doctor_scope:
            return Response({"error": "Access denied for this case."}, status=status.HTTP_403_FORBIDDEN)
        library = AppendixService.get_library(patient, doctor_id=doctor_scope or request.user.id, case_id=case_id)
        return Response(library.model_dump())

    def post(self, request):
        if not is_expert(request.user):
            return Response({"error": "Only experts can add appendix resources."}, status=status.HTTP_403_FORBIDDEN)
        patient_id = request.data.get('visitor_id') or request.data.get('patient_id')
        patient = get_object_or_404(CustomUser, pk=patient_id)
        
        serializer = CulturalResourceSerializer(data=request.data)
        if serializer.is_valid():
            case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
            if case_id and not CaseService.expert_can_edit_case(patient, request.user, case_id):
                return Response({"error": "This case is read-only for you."}, status=status.HTTP_403_FORBIDDEN)
            new_resource = AppendixService.add_resource(patient, request.user, serializer.validated_data, doctor_id=request.user.id, case_id=case_id)
            return Response(new_resource.model_dump(), status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def patch(self, request):
        resource_id = request.data.get("resource_id")
        next_status = request.data.get("status")
        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
        if not resource_id or not next_status:
            return Response({"error": "resource_id and status are required."}, status=status.HTTP_400_BAD_REQUEST)

        if is_expert(request.user):
            patient_id = request.data.get("visitor_id") or request.data.get("patient_id")
            patient = get_object_or_404(CustomUser, pk=patient_id)
            if not VaniaAccessControl.verify_doctor_access(request.user, patient):
                return Response({"error": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
            _, doctor_scope, can_edit = _resolve_expert_case_scope(request, patient, case_id)
            if case_id and not doctor_scope:
                return Response({"error": "Access denied for this case."}, status=status.HTTP_403_FORBIDDEN)
            if case_id and not can_edit:
                return Response({"error": "This case is read-only for you."}, status=status.HTTP_403_FORBIDDEN)
            doctor_scope = doctor_scope or request.user.id
        else:
            patient = request.user
            raw_doctor = (
                request.data.get("expert_id")
                or request.data.get("doctor_id")
                or request.headers.get("X-Target-Expert-ID")
                or request.headers.get("X-Target-Doctor-ID")
            )
            try:
                doctor_scope = int(raw_doctor) if raw_doctor else None
            except (TypeError, ValueError):
                doctor_scope = None
            if not doctor_scope or not case_id:
                return Response({"error": "doctor_id and case_id are required."}, status=status.HTTP_400_BAD_REQUEST)
            if not any(
                item.get("id") == case_id and int(item.get("doctor_id") or 0) == doctor_scope
                for item in CaseService.get_accessible_cases_for_patient(patient)
            ):
                return Response({"error": "Access denied for this case."}, status=status.HTTP_403_FORBIDDEN)

        updated = AppendixService.update_resource_status(
            patient,
            resource_id,
            next_status,
            doctor_id=doctor_scope,
            case_id=case_id,
        )
        if not updated:
            return Response({"error": "Resource not found."}, status=status.HTTP_404_NOT_FOUND)
        library = AppendixService.get_library(patient, doctor_id=doctor_scope, case_id=case_id)
        return Response(library.model_dump())


class ActiveSessionView(APIView):
    """API endpoint to set which session is currently 'active' for the agent's context."""
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]

    def post(self, request):
        patient_id = request.data.get('visitor_id') or request.data.get('patient_id')
        session_number = request.data.get('session_number')
        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
        
        if not patient_id or session_number is None:
            return Response({"error": "Both 'patient_id' and 'session_number' are required."}, status=status.HTTP_400_BAD_REQUEST)

        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return Response({"error": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        if case_id and not CaseService.expert_can_edit_case(patient, request.user, case_id):
            return Response({"error": "This case is read-only for you."}, status=status.HTTP_403_FORBIDDEN)
        
        try:
            RoadmapService.set_active_session(patient, int(session_number), doctor_id=request.user.id, case_id=case_id)
            return Response({"status": "updated", "active_session": session_number})
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class MedicationManagementView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]

    def post(self, request):
        patient_id = request.data.get("visitor_id") or request.data.get("patient_id")
        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
        if not patient_id or not case_id:
            return Response({"error": "patient_id and case_id are required."}, status=status.HTTP_400_BAD_REQUEST)

        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return Response({"error": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        _, doctor_scope, can_edit = _resolve_expert_case_scope(request, patient, case_id)
        if not doctor_scope:
            return Response({"error": "Access denied for this case."}, status=status.HTTP_403_FORBIDDEN)
        if not can_edit:
            return Response({"error": "This case is read-only for you."}, status=status.HTTP_403_FORBIDDEN)

        medication = MedicationService.add_medication(
            patient,
            request.user,
            {
                "drug_name": request.data.get("drug_name") or request.data.get("name") or "",
                "dosage": request.data.get("dosage", ""),
                "usage_instructions": request.data.get("usage_instructions", ""),
                "timing": request.data.get("timing", ""),
                "duration": request.data.get("duration", ""),
                "notes": request.data.get("notes", ""),
            },
            doctor_id=doctor_scope,
            case_id=case_id,
        )
        return Response(medication.model_dump(), status=status.HTTP_201_CREATED)

    def put(self, request, medication_id):
        patient_id = request.data.get("visitor_id") or request.data.get("patient_id")
        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
        if not patient_id or not case_id:
            return Response({"error": "patient_id and case_id are required."}, status=status.HTTP_400_BAD_REQUEST)

        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return Response({"error": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        _, doctor_scope, can_edit = _resolve_expert_case_scope(request, patient, case_id)
        if not doctor_scope:
            return Response({"error": "Access denied for this case."}, status=status.HTTP_403_FORBIDDEN)
        if not can_edit:
            return Response({"error": "This case is read-only for you."}, status=status.HTTP_403_FORBIDDEN)

        updated = MedicationService.update_medication(
            patient,
            medication_id,
            {
                key: value
                for key, value in {
                    "drug_name": request.data.get("drug_name", request.data.get("name")),
                    "dosage": request.data.get("dosage"),
                    "usage_instructions": request.data.get("usage_instructions"),
                    "timing": request.data.get("timing"),
                    "duration": request.data.get("duration"),
                    "notes": request.data.get("notes"),
                }.items()
                if value is not None
            },
            creator=request.user,
            doctor_id=doctor_scope,
            case_id=case_id,
        )
        if not updated:
            return Response({"error": "Medication not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(updated.model_dump())

    def delete(self, request, medication_id):
        patient_id = request.query_params.get("visitor_id") or request.query_params.get("patient_id")
        case_id = request.query_params.get("case_id") or request.headers.get("X-Target-Case-ID")
        if not patient_id or not case_id:
            return Response({"error": "patient_id and case_id are required."}, status=status.HTTP_400_BAD_REQUEST)

        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return Response({"error": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        _, doctor_scope, can_edit = _resolve_expert_case_scope(request, patient, case_id)
        if not doctor_scope:
            return Response({"error": "Access denied for this case."}, status=status.HTTP_403_FORBIDDEN)
        if not can_edit:
            return Response({"error": "This case is read-only for you."}, status=status.HTTP_403_FORBIDDEN)

        deleted = MedicationService.delete_medication(
            patient,
            medication_id,
            creator=request.user,
            doctor_id=doctor_scope,
            case_id=case_id,
        )
        if not deleted:
            return Response({"error": "Medication not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"status": "deleted"})

# ==============================================================================
# == 5. OTHER UTILITY/CRUD VIEWS
# ==============================================================================

class TaskManagementView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]
    
    def post(self, request):
        patient_id = request.data.get('visitor_id') or request.data.get('patient_id')
        text = request.data.get('text')
        due_date = request.data.get('due_date')
        dimension = request.data.get('dimension', 'PERSONAL')
        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
        patient = get_object_or_404(CustomUser, pk=patient_id)
        
        if not VaniaAccessControl.verify_doctor_access(request.user, patient): 
            return Response({"error": "Access denied"}, status=403)
        if case_id and not CaseService.expert_can_edit_case(patient, request.user, case_id):
            return Response({"error": "This case is read-only for you."}, status=403)
            
        new_task = TaskService.assign_task(patient, request.user, text, due_date, dimension, doctor_id=request.user.id, case_id=case_id)
        return Response(new_task, status=status.HTTP_201_CREATED)
    
    def put(self, request, task_id):
        patient_id = request.data.get('visitor_id') or request.data.get('patient_id')
        text = request.data.get('text')
        due_date = request.data.get('due_date')
        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
        patient = get_object_or_404(CustomUser, pk=patient_id)
        
        if not VaniaAccessControl.verify_doctor_access(request.user, patient): 
            return Response({"error": "Access denied"}, status=403)
        if case_id and not CaseService.expert_can_edit_case(patient, request.user, case_id):
            return Response({"error": "This case is read-only for you."}, status=403)
            
        success = TaskService.edit_task(patient, task_id, text, due_date, doctor_id=request.user.id, case_id=case_id)
        if success:
            return Response({"status": "updated"})
        return Response({"error": "Task not found"}, status=404)
    
    def delete(self, request, task_id):
        patient_id = request.query_params.get('visitor_id') or request.query_params.get('patient_id') 
        case_id = request.query_params.get("case_id") or request.headers.get("X-Target-Case-ID")
        patient = get_object_or_404(CustomUser, pk=patient_id)
        
        if not VaniaAccessControl.verify_doctor_access(request.user, patient): 
            return Response({"error": "Access denied"}, status=403)
        if case_id and not CaseService.expert_can_edit_case(patient, request.user, case_id):
            return Response({"error": "This case is read-only for you."}, status=403)
            
        success = TaskService.delete_task(patient, task_id, doctor_id=request.user.id, case_id=case_id)
        if success:
            return Response({"status": "deleted"})
        return Response({"error": "Task not found"}, status=404)
    
    
class SessionManagementView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]

    @staticmethod
    def _refresh_entry_timeline(entry):
        data = entry.data if isinstance(entry.data, dict) else {}
        doctor_id = data.get("doctor_id")
        case_id = data.get("case_id")
        if doctor_id and case_id:
            PatientDataService.refresh_patient_session_timeline_canvas(
                entry.user,
                int(doctor_id),
                case_id,
            )
    
    def post(self, request):
        patient_id = request.data.get('visitor_id') or request.data.get('patient_id')
        summary = request.data.get('summary')
        private_notes = request.data.get('private_notes', '')
        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
        # date_str = request.data.get('date') # Not used in current service signature
        patient = get_object_or_404(CustomUser, pk=patient_id)
        
        if not VaniaAccessControl.verify_doctor_access(request.user, patient): 
            return Response({"error": "Access denied"}, status=403)
        if case_id and not CaseService.expert_can_edit_case(patient, request.user, case_id):
            return Response({"error": "This case is read-only for you."}, status=403)
            
        entry = SessionService.log_session(
            patient,
            request.user,
            summary,
            private_notes,
            doctor_id=request.user.id,
            case_id=case_id,
        )
        self._refresh_entry_timeline(entry)
        return Response({"status": "created"}, status=status.HTTP_201_CREATED)
    
    def put(self, request, entry_id):
        summary = request.data.get('summary')
        private_notes = request.data.get('private_notes', '')
        date = request.data.get('date')
        entry = get_object_or_404(UserContextEntry, pk=entry_id, definition__key=SessionService.CONTEXT_KEY)
        success = SessionService.update_session(entry_id, request.user, summary, private_notes, date)
        if success:
            self._refresh_entry_timeline(entry)
            return Response({"status": "updated"})
        return Response({"error": "Update failed"}, status=400)
    
    def delete(self, request, entry_id):
        entry = get_object_or_404(UserContextEntry, pk=entry_id, definition__key=SessionService.CONTEXT_KEY)
        success = SessionService.delete_session(entry_id, request.user)
        if success:
            self._refresh_entry_timeline(entry)
            return Response({"status": "deleted"})
        return Response({"error": "Delete failed"}, status=400)


class CaseProfileNotesView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def _resolve_case_profile_context(self, request):
        patient_id = (
            request.data.get("visitor_id")
            or request.data.get("patient_id")
            or request.query_params.get("visitor_id")
            or request.query_params.get("patient_id")
        )
        case_id = (
            request.data.get("case_id")
            or request.query_params.get("case_id")
            or request.headers.get("X-Target-Case-ID")
        )

        if not patient_id or not case_id:
            return None, None, None, None, Response(
                {"error": "patient_id and case_id are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return None, None, None, None, Response({"error": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        _, doctor_scope, can_edit = _resolve_expert_case_scope(request, patient, case_id)
        if not doctor_scope:
            # Allow draft cases from canvas-local flows while still enforcing active doctor-patient relationship.
            if isinstance(case_id, str) and case_id.startswith("draft-"):
                doctor_scope = int(request.user.id)
                can_edit = True
            else:
                return None, None, None, None, Response({"error": "Access denied for this case."}, status=status.HTTP_403_FORBIDDEN)
        return patient, case_id, doctor_scope, can_edit, None

    def _build_case_profile_payload(self, patient, doctor_scope: int, case_id: str):
        return {
            "clinical_summary": ProfileService.get_summary(patient, doctor_id=doctor_scope, case_id=case_id),
            "forms_tests_analysis": ProfileService.get_forms_tests_analysis(patient, doctor_id=doctor_scope, case_id=case_id),
            "summary_voice_notes": ProfileService.get_summary_voice_notes(patient, doctor_id=doctor_scope, case_id=case_id),
        }

    def get(self, request):
        patient, case_id, doctor_scope, _, error = self._resolve_case_profile_context(request)
        if error:
            return error
        return Response(self._build_case_profile_payload(patient, doctor_scope, case_id))

    def put(self, request):
        patient, case_id, doctor_scope, can_edit, error = self._resolve_case_profile_context(request)
        if error:
            return error

        summary_text = request.data.get("clinical_summary")
        analysis_text = request.data.get("forms_tests_analysis")

        if summary_text is None and analysis_text is None:
            return Response({"error": "No case profile fields provided."}, status=status.HTTP_400_BAD_REQUEST)

        if not can_edit:
            return Response({"error": "This case is read-only for you."}, status=status.HTTP_403_FORBIDDEN)

        if summary_text is not None:
            ProfileService.update_summary(patient, str(summary_text), doctor_id=doctor_scope, case_id=case_id)
        if analysis_text is not None:
            ProfileService.update_forms_tests_analysis(patient, str(analysis_text), doctor_id=doctor_scope, case_id=case_id)

        payload = self._build_case_profile_payload(patient, doctor_scope, case_id)
        SessionReportView._refresh_visitor_dashboard_canvas(patient, doctor_scope, case_id)
        return Response(payload)

    def post(self, request):
        patient, case_id, doctor_scope, can_edit, error = self._resolve_case_profile_context(request)
        if error:
            return error
        if not can_edit:
            return Response({"error": "This case is read-only for you."}, status=status.HTTP_403_FORBIDDEN)

        uploaded_file = request.FILES.get("file")
        if not uploaded_file:
            return Response({"error": "File is required."}, status=status.HTTP_400_BAD_REQUEST)
        if not (uploaded_file.content_type or "").startswith("audio/"):
            return Response({"error": "Only audio files are allowed."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            duration_seconds = float(request.data.get("duration_seconds") or 0.0)
        except (TypeError, ValueError):
            duration_seconds = 0.0

        ProfileService.add_summary_voice_note(
            patient=patient,
            uploaded_file=uploaded_file,
            doctor_id=doctor_scope,
            case_id=case_id,
            uploaded_by_user_id=request.user.id,
            duration_seconds=duration_seconds,
            creator=request.user,
        )
        payload = self._build_case_profile_payload(patient, doctor_scope, case_id)
        SessionReportView._refresh_visitor_dashboard_canvas(patient, doctor_scope, case_id)
        return Response(payload, status=status.HTTP_201_CREATED)

    def delete(self, request):
        patient, case_id, doctor_scope, can_edit, error = self._resolve_case_profile_context(request)
        if error:
            return error
        if not can_edit:
            return Response({"error": "This case is read-only for you."}, status=status.HTTP_403_FORBIDDEN)

        voice_note_id = (
            request.data.get("voice_note_id")
            or request.query_params.get("voice_note_id")
        )
        if not voice_note_id:
            return Response({"error": "voice_note_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        deleted = ProfileService.delete_summary_voice_note(
            patient=patient,
            voice_note_id=str(voice_note_id),
            doctor_id=doctor_scope,
            case_id=case_id,
            creator=request.user,
        )
        if not deleted:
            return Response({"error": "Voice note not found."}, status=status.HTTP_404_NOT_FOUND)

        payload = self._build_case_profile_payload(patient, doctor_scope, case_id)
        SessionReportView._refresh_visitor_dashboard_canvas(patient, doctor_scope, case_id)
        return Response(payload)


class CaseProfileVoiceNoteDownloadView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]

    def get(self, request, voice_note_id):
        patient_id = request.query_params.get("visitor_id") or request.query_params.get("patient_id")
        case_id = request.query_params.get("case_id") or request.headers.get("X-Target-Case-ID")
        if not patient_id or not case_id:
            return Response({"error": "patient_id and case_id are required."}, status=status.HTTP_400_BAD_REQUEST)

        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return Response({"error": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        _, doctor_scope, _ = _resolve_expert_case_scope(request, patient, case_id)
        if not doctor_scope:
            if isinstance(case_id, str) and case_id.startswith("draft-"):
                doctor_scope = int(request.user.id)
            else:
                return Response({"error": "Access denied for this case."}, status=status.HTTP_403_FORBIDDEN)

        notes = ProfileService.get_summary_voice_notes(patient, doctor_id=doctor_scope, case_id=case_id)
        target = next((item for item in notes if item.get("id") == voice_note_id), None)
        if not target:
            return Response({"error": "Voice note not found."}, status=status.HTTP_404_NOT_FOUND)

        storage_path = target.get("storage_path")
        if not storage_path or not default_storage.exists(storage_path):
            return Response({"error": "Stored file missing."}, status=status.HTTP_404_NOT_FOUND)

        file_name = target.get("file_name") or "voice-note.webm"
        content_type = target.get("content_type") or mimetypes.guess_type(file_name)[0] or "audio/webm"
        file_obj = default_storage.open(storage_path, "rb")
        response = FileResponse(file_obj, content_type=content_type)
        response["Content-Disposition"] = f'inline; filename="{file_name}"'
        return response

class CompleteTaskView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, task_id):
        # Determine target patient based on Role
        target_patient = request.user
        selected_doctor_id = (
            request.data.get('expert_id')
            or request.data.get('doctor_id')
            or request.headers.get('X-Target-Expert-ID')
            or request.headers.get('X-Target-Doctor-ID')
        )
        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
        
        # Check if Doctor is acting on behalf of a patient
        if is_expert(request.user):
            patient_id = request.data.get('visitor_id') or request.data.get('patient_id')
            if patient_id:
                target_patient = get_object_or_404(CustomUser, pk=patient_id)
                # Verify permission
                if not VaniaAccessControl.verify_doctor_access(request.user, target_patient):
                    return Response({"error": "Access denied to this patient."}, status=status.HTTP_403_FORBIDDEN)
            if case_id:
                _, doctor_scope, can_edit = _resolve_expert_case_scope(request, target_patient, case_id)
                if not doctor_scope:
                    return Response({"error": "Access denied for this case."}, status=status.HTTP_403_FORBIDDEN)
                if not can_edit:
                    return Response({"error": "This case is read-only for you."}, status=status.HTTP_403_FORBIDDEN)
                selected_doctor_id = doctor_scope
            else:
                selected_doctor_id = request.user.id
        
        reflection = request.data.get('reflection', "") 
        new_status = request.data.get('status', 'DONE') 

        success = TaskService.update_task_status(
            patient=target_patient, 
            task_id=task_id, 
            status=new_status, 
            reflection=reflection,
            doctor_id=int(selected_doctor_id) if selected_doctor_id else None,
            case_id=case_id,
        )
        
        if not success: 
            return Response({"error": "Task not found."}, status=status.HTTP_404_NOT_FOUND)
        
        return Response({"status": "success"})
    
class RoleVerificationRequestView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    
    def post(self, request):
        role_id = request.data.get('target_role_id')
        if RoleVerificationRequest.objects.filter(user=request.user, target_role_id=role_id, status__in=[RoleVerificationRequest.Status.PENDING, RoleVerificationRequest.Status.APPROVED]).exists():
            return Response({"error": "You already have a pending or active request for this role."}, status=status.HTTP_400_BAD_REQUEST)
        
        serializer = RoleVerificationRequestSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response({"message": "Request submitted successfully."}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class LocationListView(generics.ListAPIView):
    """Publicly accessible list of locations for filtering doctors."""
    permission_classes = [permissions.AllowAny]
    pagination_class = None
    queryset = Location.objects.all().order_by('name')
    serializer_class = LocationSerializer


class PageTutorialMatchView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        current_path = normalize_tutorial_path(request.query_params.get("path") or "")
        tutorials = PageTutorial.objects.filter(is_public=True).order_by("-match_prefix", "-updated_at", "-id")

        best_match = None
        best_score = -1
        for tutorial in tutorials:
            target_path = tutorial.normalized_path or normalize_tutorial_path(tutorial.page_path)
            if not tutorial.matches_path(current_path):
                continue

            score = len(target_path)
            if not tutorial.match_prefix:
                score += 10000
            if score > best_score:
                best_match = tutorial
                best_score = score

        if not best_match:
            return Response({"tutorial": None})

        video_url = best_match.video.url if best_match.video else ""
        if video_url and not video_url.startswith(("http://", "https://")):
            video_url = request.build_absolute_uri(video_url)

        return Response({
            "tutorial": {
                "id": best_match.id,
                "title": best_match.title,
                "path": best_match.normalized_path,
                "video_url": video_url,
                "match_prefix": best_match.match_prefix,
            }
        })
    
class SessionReportView(APIView):
    """
    Allows a doctor to manually create or update the 'Session Support Document' 
    (Summary, Flashcards, etc.) for a specific session number.
    """
    permission_classes = [permissions.IsAuthenticated, IsDoctorUser]

    @staticmethod
    def _refresh_visitor_dashboard_canvas(patient, doctor_id: int, case_id: Optional[str]):
        try:
            PatientDataService.refresh_patient_dashboard_canvas(patient, doctor_id, case_id)
        except Exception as exc:
            logger.warning("Failed to refresh visitor dashboard canvas for patient %s: %s", patient.id, exc)

    @staticmethod
    def _normalize_swot(raw_swot):
        normalized = {
            "Strengths": [],
            "Weaknesses": [],
            "Opportunities": [],
            "Threats": [],
        }
        if not isinstance(raw_swot, dict):
            return normalized

        for key in normalized.keys():
            value = raw_swot.get(key, [])
            if isinstance(value, list):
                normalized[key] = [str(item).strip() for item in value if str(item).strip()]
            elif isinstance(value, str):
                normalized[key] = [line.strip() for line in value.splitlines() if line.strip()]
        return normalized

    def post(self, request):
        patient_id = request.data.get('visitor_id') or request.data.get('patient_id')
        session_number = request.data.get('session_number')
        case_id = request.data.get("case_id") or request.headers.get("X-Target-Case-ID")
        
        # Report Data
        summary = request.data.get('summary', '')
        private_notes = request.data.get('private_notes', '')
        raw_flashcards = request.data.get('flashcards', [])
        flashcards = normalize_flashcards(raw_flashcards)
        swot_analysis = self._normalize_swot(request.data.get('swot_analysis') or request.data.get('swot') or {})
        smart_goals = [
            str(item).strip()
            for item in (request.data.get('smart_goals') or request.data.get('goals') or [])
            if str(item).strip()
        ]
        logger.info(
            "🧪 [SessionReportView] patient=%s doctor=%s session=%s flashcards_in=%s flashcards_out=%s swot_keys=%s goals=%s",
            patient_id,
            request.user.id,
            session_number,
            len(raw_flashcards) if isinstance(raw_flashcards, list) else 0,
            len(flashcards),
            ",".join([key for key, items in swot_analysis.items() if items]),
            len(smart_goals),
        )
        
        if not patient_id or session_number is None:
            return Response({"error": "Patient ID and Session Number required."}, status=400)

        patient = get_object_or_404(CustomUser, pk=patient_id)
        if not VaniaAccessControl.verify_doctor_access(request.user, patient):
            return Response({"error": "Access denied."}, status=403)
        if case_id and not CaseService.expert_can_edit_case(patient, request.user, case_id):
            return Response({"error": "This case is read-only for you."}, status=403)

        # 1. Get Roadmap to check if session exists and if it has a doc_id
        roadmap = RoadmapService.get_or_create_roadmap(patient, doctor_id=request.user.id, case_id=case_id)
        target_session = next((s for s in roadmap.sessions if s.session_number == int(session_number)), None)
        if not target_session:
            target_session = RoadmapService.ensure_session(
                patient,
                session_number=int(session_number),
                title=request.data.get('topic') or f"جلسه {session_number}",
                scheduled_date=request.data.get('date') or timezone.now().strftime('%Y-%m-%d'),
                status="COMPLETED",
                doctor_id=request.user.id,
                case_id=case_id,
            )

        # 2. Prepare Payload (Structured JSON)
        # We merge with existing data if we are updating, to not lose other AI generated fields
        rich_payload = {
            "is_structured_report": True,
            "session_number": int(session_number),
            "date": request.data.get('date') or timezone.now().strftime('%Y-%m-%d'),
            "topic": target_session.title,
            "symptoms_analysis": summary, # This maps to the main summary
            "swot_analysis": swot_analysis,
            "smart_goals": smart_goals,
            "flashcards": flashcards,
            # We preserve existing smart_goals/swot if we are just editing the text
        }

        # 3. Update Existing or Create New
        log_entry = SessionService.save_structured_report(
            patient=patient,
            doctor=request.user,
            summary=json.dumps(rich_payload, ensure_ascii=False),
            private_notes=private_notes,
            doctor_id=request.user.id,
            case_id=case_id,
            entry_id=target_session.doc_id,
        )

        # 4. Ensure Roadmap is updated (Status -> COMPLETED, Link Doc ID)
        RoadmapService.complete_session(patient, int(session_number), str(log_entry.id), doctor_id=request.user.id, case_id=case_id)
        
        # 5. Fetch updated states to return for UI Sync
        updated_roadmap = RoadmapService.get_or_create_roadmap(patient, doctor_id=request.user.id, case_id=case_id)
        updated_history = SessionService.get_patient_history(patient, viewer_role='DOCTOR', doctor_id=request.user.id, case_id=case_id)
        self._refresh_visitor_dashboard_canvas(patient, request.user.id, case_id)

        return Response({
            "status": "success", 
            "roadmap": updated_roadmap.model_dump(),
            "history": updated_history
        })
