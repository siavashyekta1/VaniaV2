# backend/billing/serializers.py
from rest_framework import serializers
from .models import BillingConfig, BillingProduct, Invoice, Transaction, SubscriptionPlan, UserWallet, FAQ

class BillingConfigSerializer(serializers.ModelSerializer):
    """
    Exposes economy settings to the frontend ConfigProvider.
    """
    class Meta:
        model = BillingConfig
        fields = (
            'currency_name', 
            'currency_symbol', 
            'daily_free_credits', 
            'transcription_cost_per_minute',
            'esanj_test_markup_percent',
            'bank_card_number',
            'bank_holder_name',
            'manual_payment_tips',
            'support_phone',
            'support_email',
            'support_address',
            'support_postal_code',
            'support_contacts',
        )

class FAQSerializer(serializers.ModelSerializer):
    class Meta:
        model = FAQ
        fields = ('id', 'question', 'answer', 'category')
        
# --- [NEW] Plan Serializer ---
class SubscriptionPlanSerializer(serializers.ModelSerializer):
    # Returns list of agent names included in this plan
    # This uses the reverse M2M relation from AgentService (related_name='agents')
    # Note: If your AgentService M2M related_name is 'agents', then Plan.agents.all() works.
    included_agents = serializers.SlugRelatedField(
        many=True, 
        read_only=True, 
        slug_field='name',
        source='agents' 
    )
    included_agent_slugs = serializers.SlugRelatedField(
        many=True,
        read_only=True,
        slug_field='slug',
        source='agents'
    )

    class Meta:
        model = SubscriptionPlan
        fields = (
            'id', 'slug', 'name', 'description', 'price', 
            'included_credits', 'duration_days', 'included_agents', 'included_agent_slugs',
            'audience', 'eligible_expert_professions'
        )

# --- Updated Product Serializer ---
class BillingProductSerializer(serializers.ModelSerializer):
    """
    Used for the Billing Page grid.
    If 'linked_plan' is present, we include its detailed structure.
    """
    plan_details = SubscriptionPlanSerializer(source='linked_plan', read_only=True)

    class Meta:
        model = BillingProduct
        fields = (
            'id', 'name', 'description', 'price', 
            'credit_amount', 'plan_details'
        )

class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = ('id', 'amount', 'transaction_type', 'description', 'timestamp')

class InvoiceSerializer(serializers.ModelSerializer):
    item_name = serializers.SerializerMethodField()
    item_description = serializers.SerializerMethodField()
    user_name = serializers.CharField(source='user.full_name', read_only=True)
    user_phone = serializers.CharField(source='user.phone_number', read_only=True)
    formatted_date = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = (
            'id', 'status', 'subtotal_amount', 'tax_rate', 'tax_amount', 'total_amount', 'created_at', 'formatted_date', 
            'item_name', 'item_description', 'user_name', 'user_phone',
            'transaction_ref_id', 'card_number', 'authority', 'discount_amount'
        )

    def get_formatted_date(self, obj):
        return obj.created_at.strftime("%Y-%m-%d %H:%M")

    def get_item_name(self, obj):
        if obj.content_object:
            return str(
                getattr(obj.content_object, 'name', None)
                or getattr(obj.content_object, 'title', None)
                or 'Unknown Item'
            )
        return "Unknown Item"

    def get_item_description(self, obj):
        if obj.content_object:
            if hasattr(obj.content_object, 'description'):
                return getattr(obj.content_object, 'description', '')
            if hasattr(obj.content_object, 'esanj_test_id'):
                return "دسترسی به تست تعاملی"
        return ""
