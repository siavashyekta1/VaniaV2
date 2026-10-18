# backend/billing/services.py
import logging
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from .utils import calculate_credit_cost 
from .models import UserWallet, Transaction, BillingConfig, BillingProduct, Invoice, SubscriptionPlan
from users.tasks import send_generic_sms 
from users.eligibility import is_staff_or_admin_user, is_user_eligible_for_plan
from services.access_service import access_service

logger = logging.getLogger(__name__)


def activate_default_expert_plan_for_transferred_credits(user) -> bool:
    """
    When a visitor upgrades to expert, keep their existing credits usable by
    switching the active plan to the matching expert bronze plan without granting
    that plan's included credits.
    """
    profession_slug = getattr(getattr(user, "expert_profession", None), "slug", None)
    if not profession_slug or not getattr(user, "is_expert_verified", False):
        return False

    wallet, _ = UserWallet.objects.select_for_update().get_or_create(user=user)
    if wallet.total_balance <= Decimal("0"):
        return False

    active_plan = wallet.active_plan
    if active_plan and active_plan.audience == SubscriptionPlan.Audience.EXPERT:
        return False

    preferred_slug = f"expert-{profession_slug}-30d"
    plan = (
        SubscriptionPlan.objects.filter(
            slug=preferred_slug,
            audience=SubscriptionPlan.Audience.EXPERT,
            is_active=True,
        ).first()
        or SubscriptionPlan.objects.filter(
            audience=SubscriptionPlan.Audience.EXPERT,
            eligible_expert_professions__contains=[profession_slug],
            is_active=True,
        ).order_by("price", "id").first()
    )

    if not plan or not is_user_eligible_for_plan(user, plan):
        logger.warning(
            "Could not activate default expert plan for transferred credits: user=%s profession=%s",
            getattr(user, "id", None),
            profession_slug,
        )
        return False

    wallet.active_plan = plan
    wallet.plan_expires_at = None
    wallet.save(update_fields=["active_plan", "plan_expires_at", "updated_at"])
    access_service.bump_user_cache_version(user.id)
    logger.info(
        "Activated default expert plan for transferred credits: user=%s plan=%s",
        user.id,
        plan.slug,
    )
    return True

def process_usage_charge(user, input_tokens: int, output_tokens: int, run_id: str = None) -> dict:
    """
    Deducts credits based on Plan Status.
    
    Logic:
    - IF Plan Active: Priority = Plan Balance -> Paid Balance (No Free Tier).
    - IF No Plan: Priority = Daily Free Tier ONLY (Cannot use Paid Balance).
    - If final usage exceeds usable credit, deduct the remaining usable amount and leave the wallet at zero.
    """
    cost = calculate_credit_cost(input_tokens, output_tokens)

    if is_staff_or_admin_user(user):
        return {
            "success": True,
            "deducted": Decimal(0),
            "new_daily_used": Decimal(0),
            "staff_unlimited": True,
        }
    
    if cost <= 0: 
        return {
            "success": True, 
            "deducted": Decimal(0), 
            "new_daily_used": Decimal(0)
        }

    config = BillingConfig.load()
    daily_limit = config.daily_free_credits 

    with transaction.atomic():
        # Lock wallet row to prevent race conditions
        wallet, _ = UserWallet.objects.select_for_update().get_or_create(user=user)
        
        remaining_cost = cost
        deducted_total = Decimal("0")
        
        is_plan_active = wallet.is_plan_active

        if is_plan_active:
            # --- SCENARIO A: SUBSCRIBER ---
            # 1. Use Plan Balance
            if remaining_cost > 0 and wallet.balance_plan > 0:
                deduct = min(wallet.balance_plan, remaining_cost)
                wallet.balance_plan -= deduct
                remaining_cost -= deduct
                deducted_total += deduct
            
            # 2. Use Paid Balance (Overage)
            if remaining_cost > 0 and wallet.balance_paid > 0:
                deduct = min(wallet.balance_paid, remaining_cost)
                wallet.balance_paid -= deduct
                remaining_cost -= deduct
                deducted_total += deduct
                
        else:
            # --- SCENARIO B: FREE USER ---
            # 1. Use Daily Free Tier ONLY
            free_available = max(Decimal(0), daily_limit - wallet.daily_free_used)
            if free_available > 0:
                deduct = min(free_available, remaining_cost)
                wallet.daily_free_used += deduct
                remaining_cost -= deduct
                deducted_total += deduct
            
            # Note: Paid balance is intentionally ignored here per requirements.

        wallet.save()

        if deducted_total > 0:
            # Log only the amount that was actually deducted.
            Transaction.objects.create(
                wallet=wallet,
                amount=-deducted_total,
                transaction_type=Transaction.TransactionType.SPEND,
                description=f"Run {run_id or 'N/A'}",
                reference_id=run_id
            )

        result = {
            "success": True,
            "deducted": deducted_total,
            "wallet_id": wallet.id,
            "new_daily_used": wallet.daily_free_used,
        }

        if remaining_cost > 0:
            msg = "Insufficient credits."
            if not is_plan_active and wallet.balance_paid > 0:
                msg = "Active plan required to use top-up balance."
            result.update(
                {
                    "partial": True,
                    "message": msg,
                    "shortfall": remaining_cost,
                }
            )

        return {
            **result
        }

def process_service_charge(user, amount: Decimal, description: str) -> dict:
    """
    Deducts credits for services (Transcription).
    Follows same strict Plan vs Free logic.
    """
    if amount <= 0:
        return {"success": True}

    if is_staff_or_admin_user(user):
        return {"success": True, "deducted": Decimal(0), "staff_unlimited": True}

    daily_limit = BillingConfig.load().daily_free_credits

    with transaction.atomic():
        wallet, _ = UserWallet.objects.select_for_update().get_or_create(user=user)
        
        is_plan_active = wallet.is_plan_active
        
        # Calculate Total Available based on status
        if is_plan_active:
            total_available = wallet.balance_plan + wallet.balance_paid
        else:
            total_available = max(Decimal(0), daily_limit - wallet.daily_free_used)
        
        if total_available < amount:
            msg = "اعتبار کافی نیست."
            if not is_plan_active and (wallet.balance_plan + wallet.balance_paid) >= amount:
                msg = "برای استفاده از اعتبار ذخیره شده، نیاز به طرح فعال دارید."
            return {
                "success": False, 
                "message": msg, 
                "shortfall": amount - total_available
            }

        remaining_cost = amount

        if is_plan_active:
            # 1. Plan
            if remaining_cost > 0 and wallet.balance_plan > 0:
                deduct = min(wallet.balance_plan, remaining_cost)
                wallet.balance_plan -= deduct
                remaining_cost -= deduct

            # 2. Paid
            if remaining_cost > 0 and wallet.balance_paid > 0:
                deduct = min(wallet.balance_paid, remaining_cost)
                wallet.balance_paid -= deduct
                remaining_cost -= deduct
        else:
            # 1. Free
            if remaining_cost > 0:
                # We already checked availability above
                wallet.daily_free_used += remaining_cost
                remaining_cost = 0

        wallet.save()

        Transaction.objects.create(
            wallet=wallet,
            amount=-amount,
            transaction_type=Transaction.TransactionType.SERVICE_CHARGE,
            description=description
        )

        return {"success": True}

class FulfillmentService:
    """
    Handles the delivery of purchased items (Plans or Credits) 
    once an Invoice status becomes PAID.
    """
    @classmethod
    @transaction.atomic
    def execute(cls, invoice: Invoice):
        if invoice.status != Invoice.Status.PAID: 
            logger.warning(f"Attempted fulfillment on unpaid invoice {invoice.id}")
            return
        
        # [FIX] IDEMPOTENCY CHECK
        # Check if a transaction for this invoice already exists to prevent double-dipping
        if Transaction.objects.filter(reference_id=str(invoice.id)).exists():
            logger.info(f"⚠️ Invoice {invoice.id} already fulfilled. Skipping.")
            return

        # The content_object is always a BillingProduct in this new architecture
        product = invoice.content_object
        user = invoice.user
        
        logger.info(f"🚀 Fulfilling Invoice {invoice.id} for User {user.id}")

        if isinstance(product, BillingProduct):
            cls._fulfill_product(user, product, invoice)
        else:
            wallet, _ = UserWallet.objects.select_for_update().get_or_create(user=user)
            Transaction.objects.create(
                wallet=wallet,
                amount=Decimal("0"),
                transaction_type=Transaction.TransactionType.SERVICE_CHARGE,
                description=f"Unlocked service purchase: {product}",
                reference_id=str(invoice.id),
            )

    @staticmethod
    def _fulfill_product(user, product: BillingProduct, invoice: Invoice):
        wallet, _ = UserWallet.objects.select_for_update().get_or_create(user=user)
        # 1. Handle Plan Activation
        if product.linked_plan:
            new_plan = product.linked_plan
            if not is_user_eligible_for_plan(user, new_plan):
                logger.warning(
                    "Skipping ineligible plan fulfillment: user=%s plan=%s",
                    user.id,
                    new_plan.slug,
                )
                return

            had_active_plan = wallet.is_plan_active
            carried_plan_balance = wallet.balance_plan if had_active_plan else Decimal("0")

            same_plan = wallet.active_plan and wallet.active_plan.id == new_plan.id
            wallet.active_plan = new_plan
            wallet.plan_expires_at = (
                timezone.now() + timedelta(days=new_plan.duration_days)
                if new_plan.duration_days else None
            )
            wallet.balance_plan = carried_plan_balance + new_plan.included_credits
            desc = (
                f"Added Credits To Plan: {new_plan.name}"
                if same_plan
                else f"Upgraded Plan: {new_plan.name}"
                if had_active_plan
                else f"Activated Plan: {new_plan.name}"
            )

            Transaction.objects.create(
                wallet=wallet,
                amount=new_plan.included_credits,
                transaction_type=Transaction.TransactionType.PLAN_ACTIVATION,
                description=desc,
                reference_id=str(invoice.id)
            )
            
            # Async Notification
            try:
                send_generic_sms.delay(user.phone_number, f"اشتراک جدید برای شما فعال شد.")
            except Exception as e:
                logger.error(f"SMS Error: {e}")
            access_service.bump_user_cache_version(user.id)

        # 2. Handle Credit Top-Up (if product has credit amount)
        if product.credit_amount > 0:
            wallet.balance_paid += product.credit_amount
            
            Transaction.objects.create(
                wallet=wallet,
                amount=product.credit_amount,
                transaction_type=Transaction.TransactionType.DEPOSIT,
                description=f"Top-up: {product.name}",
                reference_id=str(invoice.id)
            )
            
            # Async Notification
            try:
                send_generic_sms.delay(user.phone_number, f"مبلغ {int(product.credit_amount)} اعتبار گفتگو به حساب شما اضافه شد.")
            except Exception as e:
                logger.error(f"SMS Error: {e}")
            
        wallet.save()
