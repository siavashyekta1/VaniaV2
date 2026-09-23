# backend/vania_core/signals.py
import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from django.db import transaction

# [FIX] Import Notification and SecureMessage models
from .models import (
    RoleVerificationRequest, 
    DoctorProfile, 
    PatientInvite, 
    TreatmentConnection,
    Notification,
    SecureMessage
)
from users.models import ExpertProfession, UserRole
from users.roles import is_expert
from billing.services import activate_default_expert_plan_for_transferred_credits

logger = logging.getLogger(__name__)

@receiver(post_save, sender=RoleVerificationRequest)
def process_role_approval(sender, instance, created, **kwargs):
    """
    Signal Handler: When an admin approves a verification request, this logic runs
    to automatically update the user's role and create their professional profile.
    """
    if created:
        return

    user = instance.user
    role = instance.target_role

    if instance.status == RoleVerificationRequest.Status.APPROVED:
        logger.info(f"✅ [Signal] Processing approved verification for User {instance.user.id}")
        try:
            with transaction.atomic():
                profession_slug = instance.data.get("profession_slug")
                profession = None
                if profession_slug:
                    profession = ExpertProfession.objects.filter(slug=profession_slug).first()
                is_psychology_student = profession_slug == "psychology_student"

                user.role = role
                user.is_expert_verified = True
                # Psychology students get the approved expert workspace without
                # being represented as licensed doctors in public directories.
                user.is_verified_doctor = not is_psychology_student
                user.expert_profession = profession or user.expert_profession
                user.national_code = instance.data.get("national_code") or user.national_code
                user.medical_license = instance.data.get("credential_code") or user.medical_license
                user.expert_verification_meta = {
                    **(getattr(user, "expert_verification_meta", None) or {}),
                    "status": "approved",
                    "latest_message": "درخواست شما توسط ادمین تایید شد.",
                    "submitted_profession_slug": profession_slug or getattr(getattr(user, "expert_profession", None), "slug", None),
                    "submitted_credential_code": instance.data.get("credential_code") or user.medical_license,
                    "submitted_national_code": instance.data.get("national_code") or user.national_code,
                    "validation_kind": instance.data.get("validation_kind"),
                    "submitted_university_name": instance.data.get("university_name"),
                    "role_verification_request_id": instance.id,
                    "admin_review_recommended": False,
                }
                user.save()
                activate_default_expert_plan_for_transferred_credits(user)
                logger.info(f"   -> Expert verification synced for User {user.id}")

                if is_expert(user) and not is_psychology_student:
                    specialty = instance.data.get('specialty', 'General Practice')
                    profile, created_profile = DoctorProfile.objects.get_or_create(
                        user=user,
                        defaults={'specialty': specialty, 'is_public': False}
                    )
                    if created_profile:
                        logger.info(f"   -> DoctorProfile created for User {user.id}")
                
                Notification.objects.create(
                    recipient=user,
                    type=Notification.Type.SYSTEM,
                    title="درخواست شما تایید شد",
                    message=f"درخواست شما برای نقش '{role.name}' توسط ادمین تایید شد."
                )

        except Exception as e:
            logger.error(f"❌ [Signal] Failed to process role approval for {user.id}: {e}", exc_info=True)

    if instance.status == RoleVerificationRequest.Status.REJECTED:
        try:
            user.expert_verification_meta = {
                **(getattr(user, "expert_verification_meta", None) or {}),
                "status": "rejected",
                "latest_message": instance.admin_notes or "درخواست شما رد شد.",
                "role_verification_request_id": instance.id,
                "admin_notes": instance.admin_notes,
            }
            user.is_expert_verified = False
            user.is_verified_doctor = False
            user.save(update_fields=["expert_verification_meta", "is_expert_verified", "is_verified_doctor"])
        except Exception as e:
            logger.error(f"❌ [Signal] Failed to process role rejection for {user.id}: {e}", exc_info=True)

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def link_invites_on_signup(sender, instance, created, **kwargs):
    """
    Signal Handler: When a new user registers via OTP, this checks if their phone
    number matches any pending invitations from doctors and auto-links them.
    """
    if created and instance.phone_number:
        logger.info(f"🔗 [Signal] Checking for pending invites for new User {instance.phone_number}")
        pending_invites = PatientInvite.objects.filter(
            phone_number=instance.phone_number,
            status=PatientInvite.InviteStatus.SENT
        )
        
        if pending_invites.exists():
            with transaction.atomic():
                for invite in pending_invites:
                    # Auto-create the connection in a pending state for the patient to approve
                    TreatmentConnection.objects.create(
                        doctor=invite.doctor, 
                        patient=instance,
                        status=TreatmentConnection.Status.PENDING_PATIENT_APPROVAL,
                        notes=f"Auto-linked via Invite ID {invite.id}"
                    )
                    # Mark the invite as redeemed
                    invite.status = PatientInvite.InviteStatus.REGISTERED
                    invite.save()
                
                logger.info(f"   -> Auto-linked User {instance.id} to {pending_invites.count()} doctors.")


# [FIX] ADDED NEW SIGNAL FOR SECURE MESSAGES
@receiver(post_save, sender=SecureMessage)
def send_message_notification(sender, instance: SecureMessage, created, **kwargs):
    """
    When a new message is created, send a notification to the recipient.
    """
    # Only run for brand new messages to avoid spamming on edits
    if not created:
        return

    try:
        # Construct a deep link URL for the frontend to navigate to the correct chat
        deep_link = f"/dashboard/messages?userId={instance.sender.id}"

        # Create the notification object in the database
        Notification.objects.create(
            recipient=instance.recipient,
            sender=instance.sender,
            type=Notification.Type.NEW_MESSAGE,
            title=f"پیام جدید از {instance.sender.full_name or instance.sender.phone_number}",
            message=(instance.content[:70] + '...') if len(instance.content) > 70 else instance.content,
            payload={"url": deep_link}
        )
        logger.info(f"🔔 [Signal] Sent NEW_MESSAGE notification from {instance.sender.id} to {instance.recipient.id}")
    except Exception as e:
        # Log the error but don't crash the message sending process
        logger.error(f"❌ [Signal] Failed to create message notification: {e}")
