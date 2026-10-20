from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from billing.models import BillingProduct, Invoice, SubscriptionPlan, Transaction
from billing.services import FulfillmentService, activate_default_expert_plan_for_transferred_credits
from users.models import ExpertProfession, UserRole
from rest_framework.test import APIRequestFactory, force_authenticate
from billing.views import PurchaseDirectView, ApplyDiscountView
from billing.serializers import InvoiceSerializer
from billing.models import DiscountCode


class FulfillmentServicePlanCreditTests(TestCase):
    def setUp(self):
        visitor_role = UserRole.objects.create(slug="visitor", name="مراجعه‌کننده")
        self.user = get_user_model().objects.create_user(
            phone_number="09120000002",
            password="Password123!",
            role=visitor_role,
        )
        self.wallet = self.user.wallet

    def _create_plan_product(self, *, slug: str, name: str, included_credits: str, duration_days: int = 30):
        plan = SubscriptionPlan.objects.create(
            slug=slug,
            name=name,
            price=Decimal("1000"),
            duration_days=duration_days,
            included_credits=Decimal(included_credits),
        )
        product = BillingProduct.objects.create(
            name=f"{name} Product",
            price=Decimal("1000"),
            linked_plan=plan,
        )
        return plan, product

    def _create_paid_invoice(self, product: BillingProduct):
        return Invoice.objects.create(
            user=self.user,
            status=Invoice.Status.PAID,
            total_amount=product.price,
            content_type=ContentType.objects.get_for_model(BillingProduct),
            object_id=product.id,
        )

    @patch("billing.services.send_generic_sms.delay")
    def test_extended_purchase_stores_server_price_and_fulfills_sixty_days_once(self, _mock_sms):
        plan, product = self._create_plan_product(slug="extended", name="Extended", included_credits="2500")
        product.price = Decimal("500000")
        product.save()
        request = APIRequestFactory().post("/purchase/", {
            "id": product.id, "extend_duration": True,
            "price": 1, "subscription_duration_days": 999,
            "duration_extension_amount": 0,
        }, format="json")
        force_authenticate(request, user=self.user)
        response = PurchaseDirectView.as_view()(request)
        self.assertEqual(response.status_code, 201)
        invoice = Invoice.objects.get(pk=response.data["invoice_id"])
        self.assertEqual(invoice.subtotal_amount, Decimal("1000000"))
        self.assertEqual(invoice.tax_amount, Decimal("100000"))
        self.assertEqual(invoice.total_amount, Decimal("1100000"))
        self.assertEqual(invoice.subscription_duration_days, 60)
        self.assertEqual(invoice.duration_extension_amount, Decimal("500000"))
        self.assertEqual(InvoiceSerializer(invoice).data["subscription_duration_days"], 60)
        self.assertIn("60", InvoiceSerializer(invoice).data["item_description"])
        # A later catalog edit must not change the term already invoiced.
        plan.duration_days = 90
        plan.save()
        invoice.status = Invoice.Status.PAID
        invoice.save()
        FulfillmentService.execute(invoice)
        FulfillmentService.execute(invoice)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_plan, Decimal("2500"))
        self.assertAlmostEqual((self.wallet.plan_expires_at - timezone.now()).total_seconds(), 60 * 86400, delta=10)

    def test_default_purchase_keeps_original_price_and_term(self):
        for days in [30, 180]:
            with self.subTest(days=days):
                _, product = self._create_plan_product(slug=f"normal-{days}", name="Normal", included_credits="100", duration_days=days)
                request = APIRequestFactory().post("/purchase/", {"id": product.id}, format="json")
                force_authenticate(request, user=self.user)
                response = PurchaseDirectView.as_view()(request)
                self.assertEqual(response.status_code, 201)
                invoice = Invoice.objects.get(pk=response.data["invoice_id"])
                self.assertEqual(invoice.total_amount, Decimal("1100"))
                self.assertEqual(invoice.subscription_duration_days, days)
                self.assertEqual(invoice.duration_extension_amount, 0)

    def test_extension_rejects_non_monthly_products_and_invalid_boolean(self):
        _, six_month = self._create_plan_product(slug="six-month", name="Six", included_credits="20000", duration_days=180)
        topup = BillingProduct.objects.create(name="Topup", price=1000, credit_amount=10)
        _, monthly = self._create_plan_product(slug="monthly", name="Monthly", included_credits="100")
        for product, flag in [(six_month, True), (topup, True), (monthly, "false"), (monthly, 1), (monthly, None)]:
            with self.subTest(product=product.id, flag=flag):
                request = APIRequestFactory().post("/purchase/", {"id": product.id, "extend_duration": flag}, format="json")
                force_authenticate(request, user=self.user)
                response = PurchaseDirectView.as_view()(request)
                self.assertEqual(response.status_code, 400)
        self.assertFalse(Invoice.objects.exists())

    def test_discount_preserves_extended_term_and_surcharge_in_subtotal(self):
        _, product = self._create_plan_product(slug="discount-extended", name="Discount", included_credits="100")
        request = APIRequestFactory().post("/purchase/", {"id": product.id, "extend_duration": True}, format="json")
        force_authenticate(request, user=self.user)
        response = PurchaseDirectView.as_view()(request)
        invoice = Invoice.objects.get(pk=response.data["invoice_id"])
        DiscountCode.objects.create(code="TEN", percent=10)
        for _ in range(2):
            request = APIRequestFactory().post("/discount/", {"code": "TEN"}, format="json")
            force_authenticate(request, user=self.user)
            response = ApplyDiscountView.as_view()(request, invoice_id=invoice.id)
            self.assertEqual(response.status_code, 200)
        invoice.refresh_from_db()
        self.assertEqual(invoice.subtotal_amount, Decimal("501000"))
        self.assertEqual(invoice.total_amount, Decimal("495990"))
        self.assertEqual(invoice.subscription_duration_days, 60)

    @patch("billing.services.send_generic_sms.delay")
    def test_six_month_plan_grants_total_credits_once_for_180_days(self, _mock_sms):
        _, product = self._create_plan_product(slug="six-month-fulfill", name="Six months", included_credits="20000", duration_days=180)
        invoice = self._create_paid_invoice(product)
        FulfillmentService.execute(invoice)
        FulfillmentService.execute(invoice)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_plan, Decimal("20000"))
        self.assertAlmostEqual((self.wallet.plan_expires_at - timezone.now()).total_seconds(), 180 * 86400, delta=10)

    @patch("billing.services.send_generic_sms.delay")
    def test_same_plan_purchase_stacks_credits_with_purchased_term(self, _mock_sms):
        plan, product = self._create_plan_product(
            slug="visitor-30d",
            name="Visitor 30D",
            included_credits="100",
            duration_days=30,
        )
        self.wallet.active_plan = plan
        self.wallet.balance_plan = Decimal("35")
        self.wallet.save(update_fields=["active_plan", "balance_plan", "updated_at"])

        FulfillmentService.execute(self._create_paid_invoice(product))

        self.wallet.refresh_from_db()
        tx = Transaction.objects.get(
            wallet=self.wallet,
            transaction_type=Transaction.TransactionType.PLAN_ACTIVATION,
        )

        self.assertEqual(self.wallet.active_plan_id, plan.id)
        self.assertEqual(self.wallet.balance_plan, Decimal("135.0000000000"))
        self.assertAlmostEqual((self.wallet.plan_expires_at-timezone.now()).total_seconds(), 30*86400, delta=10)
        self.assertEqual(tx.amount, Decimal("100.0000000000"))
        self.assertIn("Added Credits To Plan", tx.description)

    @patch("billing.services.send_generic_sms.delay")
    def test_active_plan_upgrade_keeps_remaining_credits_and_adds_new_plan_credits(self, _mock_sms):
        current_plan, _current_product = self._create_plan_product(
            slug="visitor-basic-30d",
            name="Visitor Basic",
            included_credits="100",
            duration_days=30,
        )
        upgraded_plan, upgraded_product = self._create_plan_product(
            slug="expert-30d",
            name="Expert 30D",
            included_credits="250",
            duration_days=30,
        )

        self.wallet.active_plan = current_plan
        self.wallet.balance_plan = Decimal("40")
        self.wallet.save(update_fields=["active_plan", "balance_plan", "updated_at"])

        FulfillmentService.execute(self._create_paid_invoice(upgraded_product))

        self.wallet.refresh_from_db()
        tx = Transaction.objects.get(
            wallet=self.wallet,
            transaction_type=Transaction.TransactionType.PLAN_ACTIVATION,
        )

        self.assertEqual(self.wallet.active_plan_id, upgraded_plan.id)
        self.assertEqual(self.wallet.balance_plan, Decimal("290.0000000000"))
        self.assertAlmostEqual((self.wallet.plan_expires_at-timezone.now()).total_seconds(), 30*86400, delta=10)
        self.assertEqual(tx.amount, Decimal("250.0000000000"))
        self.assertIn("Upgraded Plan", tx.description)

    @patch("billing.services.send_generic_sms.delay")
    def test_existing_plan_purchase_carries_remaining_subscription_balance(self, _mock_sms):
        expired_plan, _expired_product = self._create_plan_product(
            slug="expired-30d",
            name="Expired 30D",
            included_credits="100",
            duration_days=30,
        )
        new_plan, new_product = self._create_plan_product(
            slug="visitor-pro-30d",
            name="Visitor Pro",
            included_credits="200",
            duration_days=30,
        )

        self.wallet.active_plan = expired_plan
        self.wallet.balance_plan = Decimal("55")
        self.wallet.save(update_fields=["active_plan", "balance_plan", "updated_at"])

        FulfillmentService.execute(self._create_paid_invoice(new_product))

        self.wallet.refresh_from_db()

        self.assertEqual(self.wallet.active_plan_id, new_plan.id)
        self.assertEqual(self.wallet.balance_plan, Decimal("255.0000000000"))
        self.assertAlmostEqual((self.wallet.plan_expires_at-timezone.now()).total_seconds(), 30*86400, delta=10)

    def test_visitor_upgrade_activates_matching_expert_bronze_without_granting_extra_credits(self):
        expert_role = UserRole.objects.create(slug="expert", name="متخصص")
        profession = ExpertProfession.objects.create(slug="psychologist", name="روانشناس")
        visitor_plan = SubscriptionPlan.objects.create(
            slug="visitor-30d",
            name="Visitor Bronze",
            price=Decimal("1000"),
            included_credits=Decimal("2600"),
            audience=SubscriptionPlan.Audience.VISITOR,
        )
        expert_bronze = SubscriptionPlan.objects.create(
            slug="expert-psychologist-30d",
            name="Psychologist Bronze",
            price=Decimal("10000"),
            included_credits=Decimal("30000"),
            audience=SubscriptionPlan.Audience.EXPERT,
            eligible_expert_professions=["psychologist"],
        )
        self.wallet.active_plan = visitor_plan
        self.wallet.balance_plan = Decimal("35")
        self.wallet.balance_paid = Decimal("7")
        self.wallet.save(update_fields=["active_plan", "balance_plan", "balance_paid", "updated_at"])

        self.user.role = expert_role
        self.user.expert_profession = profession
        self.user.is_expert_verified = True
        self.user.save(update_fields=["role", "expert_profession", "is_expert_verified"])

        activated = activate_default_expert_plan_for_transferred_credits(self.user)

        self.assertTrue(activated)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.active_plan_id, expert_bronze.id)
        self.assertEqual(self.wallet.balance_plan, Decimal("35.0000000000"))
        self.assertEqual(self.wallet.balance_paid, Decimal("7.0000000000"))
        self.assertFalse(
            Transaction.objects.filter(
                wallet=self.wallet,
                transaction_type=Transaction.TransactionType.PLAN_ACTIVATION,
            ).exists()
        )
