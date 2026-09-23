# backend/definitions/sync.py
import logging
import json
import os
from pathlib import Path
from decimal import Decimal
from django.db import transaction
from django.conf import settings
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware, is_naive
from django.contrib.auth import get_user_model  # [NEW] Import for User model

# --- Model Imports ---
from billing.models import BillingProduct, DiscountCode, SubscriptionPlan, BillingConfig
from services.models import AgentService, ServiceSuggestion
from services.models_canvas import CanvasType, AgentCanvasConfig
from vania_core.models import Location
from users.models import ExpertProfession
# --- Registries ---
from .billing import ALL_PRODUCTS, DISCOUNTS, PLANS
from .agents import AGENTS

from billing.models import BillingConfig, FAQ # [NEW] Import FAQ
from .support import SUPPORT_INFO, FAQS       # [NEW] Import Definitions

logger = logging.getLogger(__name__)

IRAN_LOCATIONS_DATA_FILE = Path(__file__).with_name("cities.json")

FALLBACK_IRAN_LOCATIONS = [
    "آذربایجان شرقی",
    "آذربایجان غربی",
    "اردبیل",
    "اصفهان",
    "البرز",
    "ایلام",
    "بوشهر",
    "تهران",
    "چهارمحال و بختیاری",
    "خراسان جنوبی",
    "خراسان رضوی",
    "خراسان شمالی",
    "خوزستان",
    "زنجان",
    "سمنان",
    "سیستان و بلوچستان",
    "فارس",
    "قزوین",
    "قم",
    "کردستان",
    "کرمان",
    "کرمانشاه",
    "کهگیلویه و بویراحمد",
    "گلستان",
    "گیلان",
    "لرستان",
    "مازندران",
    "مرکزی",
    "هرمزگان",
    "همدان",
    "یزد",
]


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def get_vania_locations():
    """
    Builds a flat, searchable location list that covers all provinces and cities of Iran.
    Falls back to province-level values if the bundled definitions file is unavailable.
    """
    try:
        with IRAN_LOCATIONS_DATA_FILE.open("r", encoding="utf-8") as source:
            provinces = json.load(source)

        locations = []
        for province in provinces:
            province_name = str(province.get("name") or "").strip()
            if not province_name:
                continue

            locations.append(province_name)
            for city in province.get("cities") or []:
                city_name = str(city.get("name") or "").strip()
                if not city_name:
                    continue
                locations.append(f"{province_name} - {city_name}")

        deduped_locations = list(dict.fromkeys(locations))
        if deduped_locations:
            return deduped_locations
    except Exception as exc:
        logger.warning(
            "⚠️ [Sync] Failed to load bundled Iran locations dataset from %s: %s",
            IRAN_LOCATIONS_DATA_FILE,
            exc,
        )

    return FALLBACK_IRAN_LOCATIONS
    
class DefinitionSync:
    
    @staticmethod
    def sync_admin_user():
        """
        [NEW] Ensures a default Superuser exists for development/testing.
        """
        logger.info("\n🔄 [Sync] Checking Admin User...")
        User = get_user_model()
        
        admin_phone = os.getenv("SYNC_ADMIN_PHONE", "09123456789").strip()
        admin_pass = os.getenv("SYNC_ADMIN_PASSWORD", "Adminadmin@123")
        admin_email = os.getenv("SYNC_ADMIN_EMAIL", "admin@example.com").strip()
        admin_full_name = os.getenv("SYNC_ADMIN_FULL_NAME", "مدیر سیستم").strip()
        force_sync_password = _env_flag("FORCE_SYNC_ADMIN_PASSWORD", default=bool(settings.DEBUG))

        try:
            user = User.objects.filter(phone_number=admin_phone).first()
            if not user:
                User.objects.create_superuser(
                    phone_number=admin_phone,
                    password=admin_pass,
                    email=admin_email,
                    full_name=admin_full_name,
                )
                logger.info(f"✅ [Sync] Admin created. Phone: {admin_phone} | Pass: {admin_pass}")
                return

            updated_fields = []
            if not user.is_staff:
                user.is_staff = True
                updated_fields.append("is_staff")
            if not user.is_superuser:
                user.is_superuser = True
                updated_fields.append("is_superuser")
            if not user.is_active:
                user.is_active = True
                updated_fields.append("is_active")
            if not user.email:
                user.email = admin_email
                updated_fields.append("email")
            if not user.full_name:
                user.full_name = admin_full_name
                updated_fields.append("full_name")

            password_matches = user.has_usable_password() and user.check_password(admin_pass)
            if not password_matches and force_sync_password:
                user.set_password(admin_pass)
                updated_fields.append("password")
            elif not password_matches:
                logger.warning(
                    "⚠️ [Sync] Bootstrap admin user %s exists but its password does not match SYNC_ADMIN_PASSWORD. "
                    "Keeping the existing password. Set FORCE_SYNC_ADMIN_PASSWORD=true to reset it explicitly.",
                    admin_phone,
                )

            if updated_fields:
                user.save(update_fields=updated_fields)
                logger.info("✅ [Sync] Admin user updated: %s", ", ".join(updated_fields))
            else:
                logger.info("   [Sync] Admin user already exists and is in sync.")
        except Exception as e:
            logger.error(f"❌ [Sync] Failed to create/sync admin user: {e}")
            # We don't raise here to allow the rest of the sync to proceed

    @staticmethod
    def sync_agents(prompt_slugs=None):
        """Sync agent metadata without clobbering database-managed prompts.

        Existing prompts are preserved unless their slug is explicitly supplied.
        New agents still receive the prompt from their code definition.
        """
        prompt_slugs = set(prompt_slugs or [])
        logger.info(f"🔄 [Sync] Agents: {len(AGENTS)}")
        for a in AGENTS:
            demo_config_dict = a.demo_config.to_dict() if a.demo_config else {}
            existing_prompt = (
                AgentService.objects.filter(slug=a.slug)
                .values_list("system_prompt", flat=True)
                .first()
            )
            system_prompt = (
                a.system_prompt
                if existing_prompt is None or a.slug in prompt_slugs
                else existing_prompt
            )
            agent_obj, _ = AgentService.objects.update_or_create(
                slug=a.slug,
                defaults={
                    "name": a.name,
                    "model_id": a.model_id,
                    "description": a.description,
                    "system_prompt": system_prompt,
                    "capabilities": a.capabilities,
                    "tags": a.tags,
                    "user_guide": a.user_guide,
                    "is_public": a.is_public,
                    "is_active": a.is_active,
                    "is_free": a.is_free,
                    "audience": a.audience,
                    "eligible_expert_professions": a.eligible_expert_professions,
                    "requires_visitor_selector": a.requires_visitor_selector,
                    "cost_multiplier": a.cost_multiplier,
                    "enable_reasoning": a.enable_reasoning,
                    "reasoning_effort": a.reasoning_effort,
                    "static_tools": a.static_tools,
                    "extra_config": a.extra_config,
                    "demo_config": demo_config_dict,
                }
            )
        
            
            ServiceSuggestion.objects.filter(service=agent_obj).delete()
            for s in a.suggestions:
                ServiceSuggestion.objects.create(
                    service=agent_obj, 
                    title=s.title, 
                    prompt=s.prompt, 
                    subtitle=s.subtitle
                )

            if a.default_open_canvases:
                for key in a.default_open_canvases:
                    CanvasType.objects.get_or_create(
                        component_key=key,
                        defaults={"name": key, "slug": key.lower()}
                    )
                
                canvases = CanvasType.objects.filter(component_key__in=a.default_open_canvases)
                for c in canvases:
                    AgentCanvasConfig.objects.update_or_create(
                        agent=agent_obj, 
                        canvas=c, 
                        defaults={'is_default_open': True}
                    )

    @staticmethod
    def sync_plans_and_products():
        logger.info(f"🔄 [Sync] Plans & Products")
        
        slug_to_plan_map = {}
        
        # 1. Sync Plans
        for p_def in PLANS:
            plan_obj, _ = SubscriptionPlan.objects.update_or_create(
                slug=p_def.slug,
                defaults={
                    "name": p_def.name,
                    "description": p_def.description,
                    "price": Decimal(p_def.price),
                    "duration_days": p_def.duration_days,
                    "included_credits": Decimal(p_def.monthly_credits),
                    "audience": p_def.audience,
                    "eligible_expert_professions": p_def.eligible_expert_professions,
                    "is_active": p_def.is_active
                }
            )
            slug_to_plan_map[p_def.slug] = plan_obj

            if p_def.included_agent_slugs:
                agents = AgentService.objects.filter(slug__in=p_def.included_agent_slugs)
                plan_obj.agents.set(agents)

        # 2. Sync Products
        for prod_def in ALL_PRODUCTS:
            linked_plan = None
            if prod_def.linked_plan_slug:
                linked_plan = slug_to_plan_map.get(prod_def.linked_plan_slug)
            
            BillingProduct.objects.update_or_create(
                name=prod_def.name,
                defaults={
                    "description": prod_def.description,
                    "price": Decimal(prod_def.price),
                    "credit_amount": Decimal(prod_def.credits),
                    "linked_plan": linked_plan,
                    "is_active": prod_def.is_active
                }
            )

    @staticmethod
    def sync_discounts():
        logger.info(f"🔄 [Sync] Discounts: {len(DISCOUNTS)}")
        for d in DISCOUNTS:
            expiry_aware = None
            if d.expiry_date:
                dt = parse_datetime(d.expiry_date)
                if dt and is_naive(dt):
                    expiry_aware = make_aware(dt)
                else:
                    expiry_aware = dt

            DiscountCode.objects.update_or_create(
                code=d.code,
                defaults={
                    "percent": d.percent,
                    "max_amount_per_usage": Decimal(d.max_amount) if d.max_amount else None,
                    "max_fund": Decimal(d.max_fund) if d.max_fund else None,
                    "is_active": d.is_active,
                    "expiry_date": expiry_aware
                }
            )

    @staticmethod
    def sync_billing_config():
        """
        Sets default Economy and Manual Payment settings.
        Now allows defining Token Rates and Free Credits directly here.
        """
        logger.info(f"🔄 [Sync] Global Billing Config")
        
        # We use update_or_create to ensure these defaults are applied
        BillingConfig.objects.update_or_create(
            pk=1,
            defaults={
                "currency_symbol": "اعتبار",
                "currency_name": "سرمایه گفتگو",
                
                # --- ECONOMY SETTINGS ---
                # Defined here instead of Environment Variables
                "daily_free_credits": Decimal("5.0"), 
                "tokens_per_credit": 2000,
                
                # --- PAYMENT SETTINGS ---
                "bank_card_number": "5029381016591620",
                "bank_holder_name": "جلال مرادی",
                "manual_payment_tips": "لطفاً مبلغ دقیق فاکتور را به شماره کارت بالا واریز کرده و کد پیگیری تراکنش را در کادر زیر وارد نمایید. تایید پرداخت ممکن است تا 24 ساعت زمان ببرد.",
                "support_phone": SUPPORT_INFO["phone"],
                "support_email": SUPPORT_INFO["email"],
                "support_address": SUPPORT_INFO["address"],
                "support_postal_code": SUPPORT_INFO.get("postal_code", ""),
                "support_contacts": SUPPORT_INFO.get("contacts", []),
            }
        )

    @staticmethod
    def sync_faqs():
        logger.info(f"🔄 [Sync] FAQs: {len(FAQS)} items")
        for item in FAQS:
            FAQ.objects.update_or_create(
                question=item.question,
                defaults={
                    "answer": item.answer,
                    "category": item.category,
                    "order": item.order,
                    "is_active": True
                }
            )


    @staticmethod
    def sync_locations():
        """
        Syncs predefined Vania locations from definitions to the DB.
        """
        locations = get_vania_locations()
        logger.info(f"🔄 [Sync] Vania Locations: {len(locations)} items")
        for loc_name in locations:
            Location.objects.update_or_create(
                name=loc_name,
            )

    @staticmethod
    def sync_expert_professions():
        logger.info("🔄 [Sync] Expert Professions")
        professions = [
            {
                "slug": "psychologist",
                "name": "روانشناس و مشاور",
                "description": "متخصص روانشناسی و مشاوره",
                "validation_kind": "real_psychologist",
                "validation_config": {
                    "credential_label": "کد نظام روان‌شناسی",
                    "credential_placeholder": "شماره عضویت نظام روان‌شناسی را وارد کنید",
                    "credential_help": "کدی که از سازمان نظام روان‌شناسی دریافت کرده‌اید را وارد کنید.",
                    "sample_code": "",
                },
            },
            {
                "slug": "psychiatrist",
                "name": "روان پزشک",
                "description": "متخصص روان پزشکی",
                "validation_kind": "manual_psychiatrist",
                "validation_config": {
                    "credential_label": "کد نظام پزشکی",
                    "credential_placeholder": "شماره نظام پزشکی را وارد کنید",
                    "credential_help": "اطلاعات این حوزه به صورت دستی بررسی می‌شود. کد نظام پزشکی خود را وارد کنید.",
                    "sample_code": "",
                },
            },
            {
                "slug": "lawyer",
                "name": "وکیل",
                "description": "متخصص حقوق",
                "validation_kind": "real_lawyer",
                "validation_config": {
                    "credential_label": "شناسه پروانه وکالت",
                    "credential_placeholder": "شناسه پروانه وکالت را وارد کنید",
                    "credential_help": "شماره پروانه معتبر وکالت را وارد کنید.",
                    "sample_code": "",
                },
            },
            {
                "slug": "general_doctor",
                "name": "پزشک",
                "description": "پزشک",
                "validation_kind": "manual_general_doctor",
                "validation_config": {
                    "accepted_codes": ["123456"],
                    "credential_label": "کد اعتبارسنجی پزشک",
                    "credential_placeholder": "شماره نظام پزشکی را وارد کنید",
                    "credential_help": "اطلاعات این حوزه به صورت دستی بررسی می‌شود. برای تست همچنان می‌توانید از کد 123456 استفاده کنید.",
                    "sample_code": "123456",
                },
            },
        ]
        for item in professions:
            ExpertProfession.objects.update_or_create(
                slug=item["slug"],
                defaults={
                    "name": item["name"],
                    "description": item["description"],
                    "is_active": True,
                    "validation_kind": item["validation_kind"],
                    "validation_config": item["validation_config"],
                },
            )
                    
    @classmethod
    def sync_all(cls, agent_prompt_slugs=None):
        logger.info("--- Starting Definition Synchronization ---")
        # [NEW] Admin sync is often best done outside atomic block or handled carefully
        # but here it is fine as it uses get_user_model
        cls.sync_admin_user() 
        
        with transaction.atomic():
            cls.sync_billing_config()
            cls.sync_faqs()
            cls.sync_agents(prompt_slugs=agent_prompt_slugs)
            cls.sync_locations()
            cls.sync_expert_professions()
            cls.sync_plans_and_products()
            cls.sync_discounts()
        logger.info("--- Synchronization Complete ---")
