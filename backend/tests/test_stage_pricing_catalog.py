from datetime import timedelta
from unittest import TestCase
from django.utils import timezone
from billing.models import UserWallet
from definitions.billing import PLANS
from django.test import TestCase as DatabaseTestCase
from definitions.sync import DefinitionSync
from django.contrib.auth import get_user_model
from billing.models import SubscriptionPlan, BillingProduct, Invoice
from decimal import Decimal
from unittest.mock import patch


class PublicLargeOfferSyncTests(DatabaseTestCase):
    def setUp(self):
        self.old = SubscriptionPlan.objects.create(slug="visitor-25000-30d", name="Old large", price=2999000, included_credits=25000, duration_days=30)
        self.product = BillingProduct.objects.create(name="Old product", price=2999000, linked_plan=self.old)
        self.user = get_user_model().objects.create_user(phone_number="09120000818")
        self.wallet = self.user.wallet
        self.wallet.active_plan = self.old
        self.wallet.balance_plan = Decimal("23456")
        self.wallet.save()
        self.invoice = Invoice.objects.create(user=self.user, total_amount=3298900, content_object=self.product)

    def test_sync_is_targeted_idempotent_and_keeps_prior_entitlements(self):
        other = SubscriptionPlan.objects.create(slug="unrelated", name="Unrelated", price=123, included_credits=99)
        DefinitionSync.sync_plans_and_products()
        DefinitionSync.sync_plans_and_products()
        self.old.refresh_from_db()
        self.product.refresh_from_db()
        self.wallet.refresh_from_db()
        other.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertFalse(self.old.is_active)
        self.assertFalse(self.product.is_active)
        self.assertEqual(self.old.included_credits, 25000)
        self.assertEqual(self.wallet.active_plan_id, self.old.id)
        self.assertEqual(self.wallet.balance_plan, 23456)
        self.assertTrue(self.wallet.is_plan_active)
        self.assertEqual(self.invoice.content_object.pk, self.product.pk)
        self.assertEqual(self.invoice.total_amount, 3298900)
        self.assertTrue(other.is_active)
        new = SubscriptionPlan.objects.get(slug="visitor-18000-30d")
        self.assertEqual(new.included_credits, 18000)
        self.assertEqual(new.price, 2999000)
        self.assertEqual(new.duration_days, 30)
        self.assertEqual(BillingProduct.objects.filter(linked_plan=new, is_active=True).count(), 1)

    def test_sync_retires_legacy_catalog_without_rewriting_historical_plan(self):
        legacy = SubscriptionPlan.objects.create(slug="visitor-30d", name="Legacy", price=853125, included_credits=350, duration_days=30)
        product = BillingProduct.objects.create(name="Legacy product", price=853125, linked_plan=legacy)
        DefinitionSync.sync_plans_and_products()
        legacy.refresh_from_db()
        product.refresh_from_db()
        self.assertFalse(legacy.is_active)
        self.assertFalse(product.is_active)
        self.assertEqual(legacy.included_credits, 350)
        self.assertEqual(legacy.price, 853125)

    def test_failed_sync_rolls_back_catalog_changes(self):
        with patch("definitions.sync.BillingProduct.objects.update_or_create", side_effect=RuntimeError("Simulated failure")):
            with self.assertRaises(RuntimeError):
                DefinitionSync.sync_plans_and_products()
        self.old.refresh_from_db()
        self.product.refresh_from_db()
        self.assertTrue(self.old.is_active)
        self.assertTrue(self.product.is_active)
        self.assertFalse(SubscriptionPlan.objects.filter(slug="visitor-18000-30d").exists())


class StagePricingTests(TestCase):
    def test_catalog_quantities_prices_and_total_term_credits(self):
        visitor = [(p.monthly_credits, p.price, p.duration_days) for p in PLANS if p.audience == "VISITOR"]
        self.assertEqual(visitor, [(2500, 500000, 30), (5000, 1299000, 30), (18000, 2999000, 30)])
        public_large = next(p for p in PLANS if p.slug == "visitor-18000-30d")
        self.assertIn("36,000,000", public_large.description)
        self.assertFalse(any(p.slug == "visitor-25000-30d" for p in PLANS))
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
