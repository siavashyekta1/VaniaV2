# backend/vania_core/admin.py
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html
from .models import (
    CaseAccessGrant,
    CaseContextEntry,
    EsanjTestAccessRule,
    EsanjTestAttempt,
    EsanjUserProfile,
    RoleVerificationRequest, 
    DoctorProfile, 
    TreatmentConnection, 
    PatientInvite,
    Notification,
    SecureMessage,
    Location,
    GoogleCalendarConnection,
    ExpertMeetingLink,
    PageTutorial,
)
from .esanj_client import EsanjAPIError, EsanjConfigurationError
from .esanj_views import sync_esanj_test_bank

@admin.register(RoleVerificationRequest)
class RoleVerificationRequestAdmin(admin.ModelAdmin):
    list_display = ('user', 'target_role', 'profession_label', 'submitted_code', 'university_name', 'status', 'created_at')
    list_filter = ('status', 'target_role', 'created_at')
    search_fields = ('user__phone_number', 'user__full_name')
    readonly_fields = ('user', 'target_role', 'created_at')
    
    fieldsets = (
        ('Applicant', {
            'fields': ('user', 'target_role', 'data')
        }),
        ('Verification Decision', {
            'fields': ('status', 'admin_notes'),
            'description': "Changing status to APPROVED will automatically trigger side effects via signals."
        }),
    )

    def profession_label(self, obj):
        return obj.data.get("profession_label") or obj.data.get("profession_slug") or "-"
    profession_label.short_description = "Profession"

    def submitted_code(self, obj):
        return obj.data.get("credential_code") or "-"
    submitted_code.short_description = "Submitted Code"

    def university_name(self, obj):
        return obj.data.get("university_name") or "-"
    university_name.short_description = "University"

@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)
    
@admin.register(DoctorProfile)
class DoctorProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'specialty', 'is_public', 'accepting_new_patients', 'created_at')
    list_filter = ('is_public', 'accepting_new_patients', 'specialty')
    search_fields = ('user__phone_number', 'user__full_name')
    autocomplete_fields = ['location']
    
@admin.register(TreatmentConnection)
class TreatmentConnectionAdmin(admin.ModelAdmin):
    list_display = ('doctor_phone', 'patient_phone', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('doctor__phone_number', 'patient__phone_number')
    autocomplete_fields = ['doctor', 'patient']
    
    readonly_fields = ('created_at', 'updated_at')
    fieldsets = (
        (None, {
            'fields': ('doctor', 'patient', 'status')
        }),
        ('Request Details', {
            'fields': ('request_data', 'notes'),
            'classes': ('collapse',),
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
        }),
    )

    def doctor_phone(self, obj):
        return obj.doctor.phone_number
    doctor_phone.short_description = "Doctor"

    def patient_phone(self, obj):
        return obj.patient.phone_number
    patient_phone.short_description = "Patient"


@admin.register(CaseAccessGrant)
class CaseAccessGrantAdmin(admin.ModelAdmin):
    list_display = ("case_id", "patient", "owner_doctor", "grantee_doctor", "access_mode", "status", "updated_at")
    list_filter = ("status", "access_mode", "created_at", "updated_at")
    search_fields = (
        "case_id",
        "patient__phone_number",
        "patient__full_name",
        "owner_doctor__phone_number",
        "owner_doctor__full_name",
        "grantee_doctor__phone_number",
        "grantee_doctor__full_name",
    )
    autocomplete_fields = ["patient", "owner_doctor", "grantee_doctor", "granted_by"]
    readonly_fields = ("created_at", "updated_at")


@admin.register(CaseContextEntry)
class CaseContextEntryAdmin(admin.ModelAdmin):
    list_display = ("user", "doctor_scope", "case_count", "case_titles_preview", "is_active", "created_at")
    list_filter = ("is_active", "source", "created_at")
    search_fields = ("user__phone_number", "user__full_name", "definition__key", "data")
    autocomplete_fields = ["user", "created_by"]
    readonly_fields = ("definition", "doctor_scope", "case_count", "case_titles_preview", "created_at")
    fields = ("user", "definition", "doctor_scope", "case_count", "case_titles_preview", "data", "source", "created_by", "is_active", "created_at")

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("user", "definition", "created_by")
            .filter(definition__key__startswith="vania_cases__doctor_")
        )

    def doctor_scope(self, obj):
        key = getattr(getattr(obj, "definition", None), "key", "") or ""
        return key.replace("vania_cases__doctor_", "") if key.startswith("vania_cases__doctor_") else "-"
    doctor_scope.short_description = "Owner Expert ID"

    def case_count(self, obj):
        cases = obj.data.get("cases", []) if isinstance(obj.data, dict) else []
        return len(cases) if isinstance(cases, list) else 0
    case_count.short_description = "Cases"

    def case_titles_preview(self, obj):
        cases = obj.data.get("cases", []) if isinstance(obj.data, dict) else []
        if not isinstance(cases, list) or not cases:
            return "-"
        titles = [str(item.get("title") or "بدون عنوان") for item in cases[:3]]
        suffix = " ..." if len(cases) > 3 else ""
        return " | ".join(titles) + suffix
    case_titles_preview.short_description = "Titles"

@admin.register(PatientInvite)
class PatientInviteAdmin(admin.ModelAdmin):
    list_display = ('phone_number', 'doctor', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('phone_number', 'doctor__phone_number')
    readonly_fields = ('id', 'created_at')

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'title', 'type', 'is_read', 'created_at')
    list_filter = ('type', 'is_read', 'created_at')
    search_fields = ('recipient__phone_number', 'title', 'message')
    readonly_fields = ('created_at',)

@admin.register(SecureMessage)
class SecureMessageAdmin(admin.ModelAdmin):
    list_display = ('sender', 'recipient', 'short_content', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('sender__phone_number', 'recipient__phone_number', 'content')
    readonly_fields = ('created_at',)

    def short_content(self, obj):
        return (obj.content[:50] + '...') if len(obj.content) > 50 else obj.content
    short_content.short_description = "Content"


@admin.register(GoogleCalendarConnection)
class GoogleCalendarConnectionAdmin(admin.ModelAdmin):
    list_display = ("client_id", "calendar_id", "is_connected", "updated_at")
    readonly_fields = ("is_connected", "auth_link_display", "created_at", "updated_at")
    fieldsets = (
        ("Google OAuth", {
            "fields": ("client_id", "client_secret", "calendar_id"),
            "description": "Use a Google Cloud OAuth Web Application. This shared account is used for all platform-created Meet links.",
        }),
        ("Connection Status", {
            "fields": ("is_connected", "auth_link_display"),
        }),
        ("Metadata", {
            "fields": ("created_at", "updated_at"),
        }),
    )

    def has_add_permission(self, request):
        return not GoogleCalendarConnection.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = GoogleCalendarConnection.get_solo()
        return redirect(reverse("admin:vania_core_googlecalendarconnection_change", args=[obj.pk]))

    def auth_link_display(self, obj):
        if obj.client_id and obj.client_secret:
            url = reverse("vania_core:google-calendar-login")
            return format_html(
                '<a class="button" href="{}" target="_blank" '
                'style="background:#4285F4;color:white;padding:10px 15px;border-radius:4px;text-decoration:none;">'
                'Authenticate with Google'
                "</a>",
                url,
            )
        return "Enter Client ID and Client Secret, then save to enable authentication."

    auth_link_display.short_description = "Action Required"


@admin.register(ExpertMeetingLink)
class ExpertMeetingLinkAdmin(admin.ModelAdmin):
    list_display = ("creator", "visitor", "started_at", "created_at")
    search_fields = ("creator__phone_number", "visitor__phone_number", "meet_link", "google_event_id")
    readonly_fields = ("creator", "visitor", "google_event_id", "meet_link", "attendee_emails", "started_at", "ends_at", "created_at")

    def has_add_permission(self, request):
        return False


@admin.register(PageTutorial)
class PageTutorialAdmin(admin.ModelAdmin):
    list_display = ("title", "normalized_path", "match_prefix", "is_public", "updated_at")
    list_filter = ("is_public", "match_prefix", "created_at", "updated_at")
    search_fields = ("title", "page_path", "normalized_path")
    readonly_fields = ("normalized_path", "created_at", "updated_at")
    fields = ("title", "page_path", "normalized_path", "video", "match_prefix", "is_public", "created_at", "updated_at")


@admin.register(EsanjTestAccessRule)
class EsanjTestAccessRuleAdmin(admin.ModelAdmin):
    change_list_template = "admin/vania_core/esanjtestaccessrule/change_list.html"
    list_display = (
        "esanj_test_id",
        "title",
        "is_active",
        "allow_visitors",
        "allow_experts",
        "profession_summary",
        "base_price",
        "last_synced_at",
    )
    list_editable = ("is_active", "allow_visitors", "allow_experts")
    list_filter = ("is_active", "allow_visitors", "allow_experts", "eligible_expert_professions")
    search_fields = ("title", "title_employee", "esanj_test_id", "notes")
    filter_horizontal = ("eligible_expert_professions",)
    readonly_fields = ("upstream_payload", "last_synced_at", "created_at", "updated_at")
    actions = ("sync_from_esanj", "enable_for_visitors", "enable_for_experts", "disable_tests")
    fieldsets = (
        ("Test", {
            "fields": ("esanj_test_id", "title", "title_employee", "base_price", "is_active", "notes"),
        }),
        ("Availability", {
            "fields": ("allow_visitors", "allow_experts", "eligible_expert_professions"),
            "description": "If expert professions are empty, all expert subtypes can access this test when experts are enabled.",
        }),
        ("Upstream", {
            "classes": ("collapse",),
            "fields": ("upstream_payload", "last_synced_at", "created_at", "updated_at"),
        }),
    )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "sync/",
                self.admin_site.admin_view(self.sync_bank_view),
                name="vania_core_esanjtestaccessrule_sync",
            ),
        ]
        return custom_urls + urls

    def sync_bank_view(self, request):
        try:
            created, updated = sync_esanj_test_bank()
        except (EsanjConfigurationError, EsanjAPIError) as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
        else:
            self.message_user(
                request,
                f"Esanj test bank synced. Created: {created}, updated: {updated}.",
                level=messages.SUCCESS,
            )
        return redirect("admin:vania_core_esanjtestaccessrule_changelist")

    def profession_summary(self, obj):
        professions = list(obj.eligible_expert_professions.values_list("name", flat=True)[:3])
        if not professions:
            return "All experts" if obj.allow_experts else "-"
        suffix = " ..." if obj.eligible_expert_professions.count() > 3 else ""
        return ", ".join(professions) + suffix
    profession_summary.short_description = "Expert professions"

    @admin.action(description="Sync test bank from Esanj")
    def sync_from_esanj(self, request, queryset):
        try:
            created, updated = sync_esanj_test_bank()
        except (EsanjConfigurationError, EsanjAPIError) as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return
        self.message_user(request, f"Esanj test bank synced. Created: {created}, updated: {updated}.", level=messages.SUCCESS)

    @admin.action(description="Enable selected tests for visitors")
    def enable_for_visitors(self, request, queryset):
        count = queryset.update(is_active=True, allow_visitors=True)
        self.message_user(request, f"{count} tests enabled for visitors.", level=messages.SUCCESS)

    @admin.action(description="Enable selected tests for experts")
    def enable_for_experts(self, request, queryset):
        count = queryset.update(is_active=True, allow_experts=True)
        self.message_user(request, f"{count} tests enabled for experts.", level=messages.SUCCESS)

    @admin.action(description="Disable selected tests")
    def disable_tests(self, request, queryset):
        count = queryset.update(is_active=False)
        self.message_user(request, f"{count} tests disabled.", level=messages.SUCCESS)


@admin.register(EsanjTestAttempt)
class EsanjTestAttemptAdmin(admin.ModelAdmin):
    list_display = ("test_title", "user", "status", "age", "sex", "started_at", "completed_at")
    list_filter = ("status", "sex", "started_at", "completed_at")
    search_fields = ("test_title", "user__phone_number", "user__full_name", "id")
    readonly_fields = (
        "id",
        "user",
        "access_rule",
        "esanj_test_id",
        "test_title",
        "age",
        "sex",
        "employee_id",
        "questionnaire",
        "answers",
        "result_json",
        "grading_json",
        "error_message",
        "started_at",
        "updated_at",
        "submitted_at",
        "completed_at",
    )

    def has_add_permission(self, request):
        return False


@admin.register(EsanjUserProfile)
class EsanjUserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "employee_id", "employee_username", "last_synced_at")
    search_fields = ("user__phone_number", "user__full_name", "employee_id", "employee_username")
    readonly_fields = ("upstream_payload", "last_synced_at", "created_at", "updated_at")
