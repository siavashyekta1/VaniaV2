# backend/definitions/billing.py
from .base import ProductDef, DiscountDef, PlanDef


def _merge_agent_slugs(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for slug in group:
            if slug not in merged:
                merged.append(slug)
    return merged

ALL_AUDIENCE_AGENT_SLUGS = [
    "HAM-edalat",
    "HAM-gardeshgari",
    "HAM-mohajrt",
    "fal",
    "HAM-motalee",
    "HAM-shoghli",
    "HAM-moraje",
    "HAM-tahsili",
    "ravanyar",
    "vania-visitor-companion",
]

LAWYER_AGENT_SLUGS = [
    "supervisor-mashaghel",
    "vakil",
    "expert-lawyer-assistant",
    "vania-expert-assistant",
]
LAWYER_PLAN_AGENT_SLUGS = _merge_agent_slugs(ALL_AUDIENCE_AGENT_SLUGS, LAWYER_AGENT_SLUGS)

PSYCHIATRIST_AGENT_SLUGS = [
    "ravanyar-motekhases",
    "supervisor-mashaghel",
    "tarahi-darman",
    "tarahi-jalasat-darman",
    "tarahi-jalasat-daro-darman",
    "tarahi-jalasat-ravan-darman",
    "expert-psychiatrist-assistant",
    "vania-expert-assistant",
    "tashkil-parvande",
]
PSYCHIATRIST_PLAN_AGENT_SLUGS = _merge_agent_slugs(ALL_AUDIENCE_AGENT_SLUGS, PSYCHIATRIST_AGENT_SLUGS)

PSYCHOLOGIST_AGENT_SLUGS = [
    "ravanyar-motekhases",
    "ravansanj",
    "supervisor-mashaghel",
    "tarahi-darman",
    "tarahi-jalasat-darman",
    "tarahi-jalasat-ravan-darman",
    "tashkil-parvande",
    "expert-psychologist-assistant",
    "vania-expert-assistant",
]
PSYCHOLOGIST_PLAN_AGENT_SLUGS = _merge_agent_slugs(ALL_AUDIENCE_AGENT_SLUGS, PSYCHOLOGIST_AGENT_SLUGS)

GENERAL_DOCTOR_AGENT_SLUGS = [
    "expert-general-doctor-assistant",
    "vania-expert-assistant",
]
GENERAL_DOCTOR_PLAN_AGENT_SLUGS = _merge_agent_slugs(ALL_AUDIENCE_AGENT_SLUGS, GENERAL_DOCTOR_AGENT_SLUGS)

# --- 1. Subscription Plans ---
# These define the tiers. Agents are linked here by their slugs.
PLANS = [
    PlanDef(
        slug="visitor-30d",
        name="اشتراک عمومی برنزی",
        description="طرح عمومی برنزی با دسترسی به دستیارهای عمومی وانیا و اعتبار پایه برای شروع.",
        price=853125,
        duration_days=30,
        monthly_credits=350,
        included_agent_slugs=ALL_AUDIENCE_AGENT_SLUGS,
        audience="VISITOR",
    ),
    PlanDef(
        slug="visitor-90d",
        name="اشتراک عمومی نقره‌ای",
        description="طرح عمومی نقره‌ای با اعتبار بیشتر و دسترسی گسترده‌تر برای استفاده از دستیارهای عمومی وانیا.",
        price=2437500,
        duration_days=90,
        monthly_credits=1000,
        included_agent_slugs=ALL_AUDIENCE_AGENT_SLUGS,
        audience="VISITOR",
    ),
    PlanDef(
        slug="visitor-365d",
        name="اشتراک عمومی طلایی",
        description="طرح عمومی طلایی با بیشترین اعتبار برای استفاده حرفه‌ای از دستیارهای عمومی وانیا.",
        price=7312500,
        duration_days=365,
        monthly_credits=3000,
        included_agent_slugs=ALL_AUDIENCE_AGENT_SLUGS,
        audience="VISITOR",
    ),
    PlanDef(
        slug="expert-lawyer-30d",
        name="اشتراک حرفه‌ای وکلا - برنزی",
        description="طرح حرفه‌ای برنزی وکلا شامل دستیارهای عمومی وانیا به‌علاوه دستیارهای تخصصی حقوقی.",
        price=853125,
        duration_days=30,
        monthly_credits=350,
        included_agent_slugs=LAWYER_PLAN_AGENT_SLUGS,
        audience="EXPERT",
        eligible_expert_professions=["lawyer"],
    ),
    PlanDef(
        slug="expert-lawyer-90d",
        name="اشتراک حرفه‌ای وکلا - نقره‌ای",
        description="طرح حرفه‌ای نقره‌ای وکلا با اعتبار بیشتر برای استفاده از ابزارهای عمومی و تخصصی حقوقی.",
        price=2437500,
        duration_days=90,
        monthly_credits=1000,
        included_agent_slugs=LAWYER_PLAN_AGENT_SLUGS,
        audience="EXPERT",
        eligible_expert_professions=["lawyer"],
    ),
    PlanDef(
        slug="expert-lawyer-365d",
        name="اشتراک حرفه‌ای وکلا - طلایی",
        description="طرح حرفه‌ای طلایی وکلا با بیشترین اعتبار و دسترسی کامل به دستیارهای عمومی و تخصصی حقوقی.",
        price=7312500,
        duration_days=365,
        monthly_credits=3000,
        included_agent_slugs=LAWYER_PLAN_AGENT_SLUGS,
        audience="EXPERT",
        eligible_expert_professions=["lawyer"],
    ),
    PlanDef(
        slug="expert-psychiatrist-30d",
        name="اشتراک حرفه‌ای روانپزشکان - برنزی",
        description="طرح حرفه‌ای برنزی روانپزشکان شامل دستیارهای عمومی وانیا به‌علاوه دستیارهای تخصصی روانپزشکی.",
        price=853125,
        duration_days=30,
        monthly_credits=350,
        included_agent_slugs=PSYCHIATRIST_PLAN_AGENT_SLUGS,
        audience="EXPERT",
        eligible_expert_professions=["psychiatrist"],
    ),
    PlanDef(
        slug="expert-psychiatrist-90d",
        name="اشتراک حرفه‌ای روانپزشکان - نقره‌ای",
        description="طرح حرفه‌ای نقره‌ای روانپزشکان با اعتبار بیشتر برای استفاده از ابزارهای عمومی و تخصصی روانپزشکی.",
        price=2437500,
        duration_days=90,
        monthly_credits=1000,
        included_agent_slugs=PSYCHIATRIST_PLAN_AGENT_SLUGS,
        audience="EXPERT",
        eligible_expert_professions=["psychiatrist"],
    ),
    PlanDef(
        slug="expert-psychiatrist-365d",
        name="اشتراک حرفه‌ای روانپزشکان - طلایی",
        description="طرح حرفه‌ای طلایی روانپزشکان با بیشترین اعتبار و دسترسی کامل به دستیارهای عمومی و تخصصی.",
        price=7312500,
        duration_days=365,
        monthly_credits=3000,
        included_agent_slugs=PSYCHIATRIST_PLAN_AGENT_SLUGS,
        audience="EXPERT",
        eligible_expert_professions=["psychiatrist"],
    ),
    PlanDef(
        slug="expert-psychologist-30d",
        name="اشتراک حرفه‌ای روانشناسان - برنزی",
        description="طرح حرفه‌ای برنزی روانشناسان شامل دستیارهای عمومی وانیا به‌علاوه دستیارهای تخصصی روانشناسی.",
        price=853125,
        duration_days=30,
        monthly_credits=350,
        included_agent_slugs=PSYCHOLOGIST_PLAN_AGENT_SLUGS,
        audience="EXPERT",
        eligible_expert_professions=["psychologist"],
    ),
    PlanDef(
        slug="expert-psychologist-90d",
        name="اشتراک حرفه‌ای روانشناسان - نقره‌ای",
        description="طرح حرفه‌ای نقره‌ای روانشناسان با اعتبار بیشتر برای استفاده از ابزارهای عمومی و تخصصی روانشناسی.",
        price=2437500,
        duration_days=90,
        monthly_credits=1000,
        included_agent_slugs=PSYCHOLOGIST_PLAN_AGENT_SLUGS,
        audience="EXPERT",
        eligible_expert_professions=["psychologist"],
    ),
    PlanDef(
        slug="expert-psychologist-365d",
        name="اشتراک حرفه‌ای روانشناسان - طلایی",
        description="طرح حرفه‌ای طلایی روانشناسان با بیشترین اعتبار و دسترسی کامل به دستیارهای عمومی و تخصصی.",
        price=7312500,
        duration_days=365,
        monthly_credits=3000,
        included_agent_slugs=PSYCHOLOGIST_PLAN_AGENT_SLUGS,
        audience="EXPERT",
        eligible_expert_professions=["psychologist"],
    ),
    PlanDef(
        slug="expert-general-doctor-30d",
        name="اشتراک حرفه‌ای پزشکان - برنزی",
        description="طرح حرفه‌ای برنزی پزشکان شامل دستیارهای عمومی وانیا به‌علاوه دستیارهای تخصصی پزشکی.",
        price=853125,
        duration_days=30,
        monthly_credits=350,
        included_agent_slugs=GENERAL_DOCTOR_PLAN_AGENT_SLUGS,
        audience="EXPERT",
        eligible_expert_professions=["general_doctor"],
    ),
    PlanDef(
        slug="expert-general-doctor-90d",
        name="اشتراک حرفه‌ای پزشکان - نقره‌ای",
        description="طرح حرفه‌ای نقره‌ای پزشکان با اعتبار بیشتر برای استفاده از ابزارهای عمومی و تخصصی پزشکی.",
        price=2437500,
        duration_days=90,
        monthly_credits=1000,
        included_agent_slugs=GENERAL_DOCTOR_PLAN_AGENT_SLUGS,
        audience="EXPERT",
        eligible_expert_professions=["general_doctor"],
    ),
    PlanDef(
        slug="expert-general-doctor-365d",
        name="اشتراک حرفه‌ای پزشکان - طلایی",
        description="طرح حرفه‌ای طلایی پزشکان با بیشترین اعتبار و دسترسی کامل به دستیارهای عمومی و تخصصی پزشکی.",
        price=7312500,
        duration_days=365,
        monthly_credits=3000,
        included_agent_slugs=GENERAL_DOCTOR_PLAN_AGENT_SLUGS,
        audience="EXPERT",
        eligible_expert_professions=["general_doctor"],
    ),
]

# --- 2. Storefront Products ---

# A. Plan Activation Products (Generated automatically from Plans)
# These appear in the "Plans" section of the billing page.
PLAN_PRODUCTS = [
    ProductDef(
        name=f"فعال‌سازی: {p.name}",
        price=p.price,
        description=p.description,
        linked_plan_slug=p.slug,
        is_active=p.is_active
    ) for p in PLANS
]

# B. Credit Top-ups (Standalone)
# These appear in the "Top-up" section. They add to 'balance_paid'.
CREDIT_PACKS = [
    ProductDef(
        name="بسته ۵۰ سرمایه گفتگو", 
        credits=50, 
        price=240000,
        description="افزایش اعتبار قابل مصرف حساب به میزان ۵۰ سرمایه گفتگو."
    ),
    ProductDef(
        name="بسته ۱۰۰ سرمایه گفتگو", 
        credits=100, 
        price=480000,
        description="افزایش اعتبار قابل مصرف حساب به میزان ۱۰۰ سرمایه گفتگو."
    ),
    ProductDef(
        name="بسته ۲۵۰ سرمایه گفتگو", 
        credits=250, 
        price=1200000,
        is_active=False,
        description="افزایش اعتبار قابل مصرف حساب به میزان ۲۵۰ سرمایه گفتگو."
    ),
    ProductDef(
        name="بسته ۵۰۰ سرمایه گفتگو", 
        credits=500, 
        price=2400000,
        is_active=False,
        description="افزایش اعتبار قابل مصرف حساب به میزان ۵۰۰ سرمایه گفتگو."
    ),
]

ALL_PRODUCTS = PLAN_PRODUCTS + CREDIT_PACKS

# --- 3. Discounts ---
DISCOUNTS = [
    DiscountDef(
        code="free", 
        percent=100, 
        is_active=True, 
        max_amount=5000000000, 
        # [FIX] Added 'Z' to indicate UTC timezone explicitly
        expiry_date="2026-11-11T00:00:00Z" 
    ),
    DiscountDef(
        code="WELCOME",
        percent=20,
        is_active=True,
        max_amount=100000,
        max_fund=10000000
    )
]
