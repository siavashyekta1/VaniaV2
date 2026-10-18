from datetime import timedelta
from unittest import TestCase
from django.utils import timezone
from billing.models import UserWallet
from definitions.billing import PLANS


class StagePricingTests(TestCase):
    def test_catalog_quantities_prices_and_total_term_credits(self):
        visitor = [(p.monthly_credits, p.price, p.duration_days) for p in PLANS if p.audience == "VISITOR"]
        self.assertEqual(visitor, [(2500, 500000, 30), (5000, 1299000, 30), (25000, 2999000, 30)])
        expert = [p for p in PLANS if p.audience == "EXPERT"]
        self.assertEqual(len(expert), 28)
        six = [p for p in expert if p.duration_days == 180]
        self.assertEqual({(p.monthly_credits, p.price) for p in six}, {(20000,18880000),(30000,26880000),(40000,33880000),(50000,39880000)})

    def test_expired_wallet_is_not_active(self):
        wallet = UserWallet(active_plan_id=1, plan_expires_at=timezone.now()-timedelta(seconds=1))
        # Avoid database access from the active_plan descriptor in a pure unit test.
        from billing.models import SubscriptionPlan
        wallet.active_plan = SubscriptionPlan(pk=1)
        self.assertFalse(wallet.is_plan_active)
        wallet.plan_expires_at = timezone.now()+timedelta(days=1)
        self.assertTrue(wallet.is_plan_active)
