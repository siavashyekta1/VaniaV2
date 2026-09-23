# backend/services/serializers.py
from rest_framework import serializers
from django.utils import timezone
from django.core.cache import cache

from .models import AgentService, ServiceSuggestion
from .models_canvas import AgentCanvasConfig
from users.eligibility import has_agent_access_override, is_staff_or_admin_user

# --- SERVICE DISCOVERY SERIALIZERS ---

class ServiceSuggestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceSuggestion
        fields = ['title', 'subtitle', 'prompt']

# --- MAIN AGENT SERVICE SERIALIZER ---
class ServiceSerializer(serializers.ModelSerializer):
    """
    Unified Serializer for Agent Services.
    Handles Marketplace logic, UI Configuration, and Capabilities.
    """
    suggestions = ServiceSuggestionSerializer(many=True, read_only=True)
    tags = serializers.JSONField(read_only=True)
    
    # --- Marketplace / Access Fields ---
    is_owned = serializers.SerializerMethodField()
    access_status = serializers.SerializerMethodField()

    # --- Agent Metadata ---
    # [FIX] The 'quick_actions' field and its method have been removed.
    reasoning_type = serializers.SerializerMethodField()

    # --- Dynamic UI Fields ---
    ui_config = serializers.SerializerMethodField()
    supported_canvases = serializers.SerializerMethodField()
    input_requirements = serializers.SerializerMethodField()

    # --- Demo Data Fields ---
    demo_config = serializers.JSONField(read_only=True)
    current_usage = serializers.SerializerMethodField()
    
    class Meta:
        model = AgentService
        fields = [
            'id', 'name', 'slug', 'description', 'system_prompt',
            # Marketplace
            'is_free', 'is_owned', 'access_status',
            'audience', 'eligible_expert_professions', 'requires_visitor_selector',
            # UI/Meta
            'cost_multiplier', 'is_public', 'is_active', 'tags', 'suggestions', 'model_id', 'user_guide',
            # Logic
            # [FIX] 'quick_actions' removed from this list.
            'reasoning_type', 'capabilities', 'enable_reasoning', 'reasoning_effort',
            # Dynamic UI
            'ui_config', 'supported_canvases', 'input_requirements',
            # Demo Config
            'demo_config', 'current_usage',
        ]

    def get_input_requirements(self, obj):
        if not obj.extra_config: return None
        return obj.extra_config.get('input_requirements', None)

    def get_supported_canvases(self, obj):
        if hasattr(obj, 'canvas_configs'):
            return [c.canvas.component_key for c in obj.canvas_configs.all()]
        configs = AgentCanvasConfig.objects.filter(agent=obj).select_related('canvas')
        return [c.canvas.component_key for c in configs]

    def get_ui_config(self, obj):
        canvases = self.get_supported_canvases(obj)
        has_canvas = len(canvases) > 0
        
        config = {
            "has_canvas": has_canvas,
            "default_width": 65,
            "show_voice_input": True,
            "mobile_view_default": "chat"
        }
        
        if hasattr(obj, 'extra_config') and obj.extra_config:
            overrides = obj.extra_config.copy()
            overrides.pop('input_requirements', None)
            config.update(overrides)
            
        return config

    def get_is_owned(self, obj):
        request = self.context.get('request')
        if request and is_staff_or_admin_user(request.user):
            return True
        if request and has_agent_access_override(request.user, obj):
            return True
        if obj.is_free: return True
        user_plan_id = self.context.get('user_active_plan_id')
        if not user_plan_id: return False
        return any(plan.id == user_plan_id for plan in obj.plans.all())

    def get_access_status(self, obj):
        if not obj.is_active: return "MAINTENANCE"
        if obj.is_free: return "FREE"
        if self.get_is_owned(obj): return "OWNED"
        return "LOCKED"

    def get_current_usage(self, obj):
        from .usage import demo_usage_service
        if self.get_is_owned(obj): return 0
        
        user = self.context['request'].user
        if not user or not user.is_authenticated: return 0
            
        config = obj.demo_config or {}
        scope = config.get("message_limit_scope", "SESSION")
        
        if scope == "DAILY":
            key = demo_usage_service.get_daily_cache_key(user.id, obj.slug)
            return cache.get(key, 0)
        elif scope == "TOTAL":
            key = demo_usage_service.get_total_cache_key(user.id, obj.slug)
            return cache.get(key, 0)
        return 0
    
    # [FIX] The get_quick_actions method has been completely removed.

    def get_reasoning_type(self, obj):
        model = (obj.model_id or "").lower()
        if any(m in model for m in ["gpt-5", "o1", "deepseek", "reasoner"]):
            return "NATIVE"
        if obj.enable_reasoning:
            return "HYBRID"
        return "NONE"
