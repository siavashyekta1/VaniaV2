# backend/billing/models.py
import uuid
from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache

class BillingConfig(models.Model):
    """
    Singleton model to manage economy settings dynamically via Admin.
    """
    currency_name = models.CharField(max_length=50, default="سکه", help_text="e.g. Coins, Credits")
    currency_symbol = models.CharField(max_length=10, default="🪙")
    bank_card_number = models.CharField(max_length=20, blank=True, default="6037997500000000")
    bank_holder_name = models.CharField(max_length=100, blank=True, default="نام صاحب حساب")
    manual_payment_tips = models.TextField(blank=True, default="لطفا پس از واریز، شناسه پرداخت را وارد کنید.")
    tokens_per_credit = models.PositiveIntegerField(
        default=2000, 
        help_text="How many LLM tokens equal 1 Credit?"
    )
    
    daily_free_credits = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('5.00'),
        help_text="Credits reset daily for every user."
    )
    
    transcription_cost_per_minute = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('10.00'),
        help_text="Cost in Credits per minute of audio."
    )

    esanj_test_markup_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('10.00'),
        help_text="Markup percentage added to upstream interactive test base prices.",
    )
    
    support_phone = models.CharField(max_length=50, default="09123456789", blank=True)
    support_email = models.EmailField(default="support@example.com", blank=True)
    support_address = models.TextField(default="تهران، خیابان ولیعصر", blank=True)
    support_postal_code = models.CharField(max_length=20, default="", blank=True)
    support_contacts = models.JSONField(
        default=list,
        blank=True,
        help_text="Structured support contacts for UI display (role/name/phone).",
    )
    
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Economy Configuration"
        verbose_name_plural = "Economy Configuration"

    def save(self, *args, **kwargs):
        self.pk = 1 # Force singleton
        super().save(*args, **kwargs)
        cache.delete('billing_config')

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def load(cls):
        config = cache.get('billing_config')
        if config is None:
            obj, created = cls.objects.get_or_create(pk=1)
            config = obj
            cache.set('billing_config', config, timeout=3600)
        return config

    def __str__(self):
        return "Global Economy Settings"

class FAQ(models.Model):
    """
    Frequently Asked Questions content.
    """
    question = models.CharField(max_length=500)
    answer = models.TextField()
    category = models.CharField(max_length=100, default="عمومی")
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['order']
        verbose_name = "FAQ"
        verbose_name_plural = "FAQs"

    def __str__(self):
        return self.question
    
class SubscriptionPlan(models.Model):
    class Audience(models.TextChoices):
        ALL = 'ALL', 'All'
        VISITOR = 'VISITOR', 'Visitor'
        EXPERT = 'EXPERT', 'Expert'

    """
    Defines a subscription tier (e.g. Pro, Enterprise).
    Buying this grants 'included_credits' and unlocks linked Agents.
    """
    slug = models.SlugField(unique=True, max_length=50, help_text="Internal ID (e.g. 'pro-plan')") 
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=18, decimal_places=0, help_text="Price in Toman")
    
    duration_days = models.PositiveIntegerField(
        default=30,
        help_text="Legacy catalog field kept for compatibility; plan access no longer expires by time.",
    )
    
    included_credits = models.DecimalField(
        max_digits=19, decimal_places=10, 
        default=Decimal('0.0'),
        help_text="Credits credited to 'Plan Balance' upon purchase."
    )
    audience = models.CharField(
        max_length=20,
        choices=Audience.choices,
        default=Audience.ALL,
    )
    eligible_expert_professions = models.JSONField(
        default=list,
        blank=True,
        help_text="List of eligible expert profession slugs for EXPERT audience plans.",
    )
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.slug})"


class UserWallet(models.Model):
    """
    The single source of truth for a user's credits and active plan.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='wallet'
    )

    # --- PLAN STATUS ---
    active_plan = models.ForeignKey(
        SubscriptionPlan, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='wallets'
    )
    plan_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Legacy field retained for compatibility. Plan ownership no longer expires by time.",
    )

    # --- CREDIT BUCKETS ---
    # Credits from Subscriptions
    balance_plan = models.DecimalField(max_digits=19, decimal_places=10, default=Decimal('0.0'))
    
    # Credits Top-ups (Never expire)
    balance_paid = models.DecimalField(max_digits=19, decimal_places=10, default=Decimal('0.0'))
    
    # Daily Free Tier tracking (Reset via Celery task)
    daily_free_used = models.DecimalField(max_digits=19, decimal_places=10, default=Decimal('0.0'))

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Wallet: {self.user.phone_number}"

    @property
    def total_balance(self):
        return self.balance_plan + self.balance_paid
    
    @property
    def is_plan_active(self):
        return self.active_plan is not None and (
            self.plan_expires_at is None or self.plan_expires_at > timezone.now()
        )


class BillingProduct(models.Model):
    """
    Items purchasable in the store.
    Can be a simple Credit Top-up OR a Plan Activation.
    """
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=18, decimal_places=2)
    
    # If set, this gives raw credits (Top-up)
    credit_amount = models.DecimalField(max_digits=19, decimal_places=10, default=Decimal('0.0'))
    
    # If set, this activates a specific plan
    linked_plan = models.ForeignKey(
        SubscriptionPlan, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='products',
        help_text="If set, purchasing this product activates the subscription."
    )
    
    is_active = models.BooleanField(default=True)

    def __str__(self):
        type_label = "Plan" if self.linked_plan else "Top-up"
        return f"{self.name} ({type_label})"


class Transaction(models.Model):
    class TransactionType(models.TextChoices):
        DEPOSIT = 'DEPOSIT', 'Deposit'
        SPEND = 'SPEND', 'Spend (Tokens)'
        RESET = 'RESET', 'Daily Reset'
        ADJUSTMENT = 'ADJUSTMENT', 'Admin Adjustment'
        SERVICE_CHARGE = 'SERVICE', 'Service Charge'
        PLAN_ACTIVATION = 'PLAN', 'Plan Activation'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(UserWallet, on_delete=models.CASCADE, related_name='transactions')
    amount = models.DecimalField(max_digits=19, decimal_places=10)
    transaction_type = models.CharField(max_length=20, choices=TransactionType.choices)
    description = models.CharField(max_length=255, blank=True)
    timestamp = models.DateTimeField(default=timezone.now)
    reference_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['wallet', 'timestamp']),
        ]


class DiscountCode(models.Model):
    code = models.CharField(max_length=50, unique=True, db_index=True)
    percent = models.PositiveIntegerField(help_text="Discount percentage (1-100)")
    max_amount_per_usage = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Cap for discount amount per single order"
    )
    
    max_fund = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True,
        help_text="Code stops working if used_fund >= max_fund."
    )
    used_fund = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    
    expiry_date = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.code} ({self.percent}%)"


class Invoice(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending Payment'
        WAITING_APPROVAL = 'WAITING', 'Waiting for Approval' 
        PAID = 'PAID', 'Paid'
        CANCELLED = 'CANCELLED', 'Cancelled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='invoices')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    subtotal_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('10.00'))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    subscription_duration_days = models.PositiveIntegerField(null=True, blank=True)
    duration_extension_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    created_at = models.DateTimeField(auto_now_add=True)
    
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, null=True)
    object_id = models.PositiveIntegerField(null=True)
    content_object = GenericForeignKey('content_type', 'object_id')

    discount_code = models.ForeignKey(DiscountCode, on_delete=models.SET_NULL, null=True, blank=True)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    transaction_ref_id = models.CharField(max_length=100, blank=True, null=True)
    card_number = models.CharField(max_length=20, blank=True, null=True)
    authority = models.CharField(max_length=255, blank=True, null=True, db_index=True)
    payment_date = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"Inv {self.id} - {self.user} ({self.status})"
