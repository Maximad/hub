"""Evidence-based readiness never turns uncertain legacy numbers into truth."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    ActivityLog, AuditEvent, Category, InventoryItem, Product, ProductRecipeItem,
    StockMovement, PostingCommand, Payment,
)
from core.services.operational_completeness import (
    counted_inventory_ids, inventory_completeness, product_completeness, recipe_completeness,
    verified_low_stock_items,
)


@override_settings(ALLOWED_HOSTS=["testserver"], SECURE_SSL_REDIRECT=False)
class OperationalCompletenessTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.cashier = User.objects.create_user(
            username="ops-ready-cashier", phone="+963998000411", password="x", role="cashier",
        )
        self.admin = User.objects.create_user(
            username="ops-ready-admin", phone="+963998000412", password="x", role="admin",
        )
        self.material = InventoryItem.objects.create(
            name_ar="بن عربي", code="OPS-ARABIC-BEAN", unit=InventoryItem.Unit.KG,
            current_quantity=Decimal("0"),
        )
        self.category = Category.objects.create(name_ar="مشاريب")
        self.product = Product.objects.create(
            name_ar="قهوة اختبار", category=self.category, price_syp=150,
            is_available=True, visible_on_qr=True, orderable_on_qr=True,
            requires_preparation=True, product_type=Product.ProductType.DRINK,
        )

    def test_verified_only_low_stock_is_consistent_on_dashboard_and_review_page(self):
        self.material.low_stock_threshold = Decimal("1.000")
        self.material.save(update_fields=["low_stock_threshold", "updated_at"])
        self.client.force_login(self.admin)

        # A historical recorded zero has not been physically checked.
        self.assertEqual(verified_low_stock_items(), [])
        dashboard = self.client.get(reverse("staff_inventory_home"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(list(dashboard.context["low_items"]), [])
        low_page = self.client.get(reverse("staff_inventory_low_stock"))
        self.assertEqual(low_page.status_code, 200)
        self.assertNotContains(low_page, self.material.name_ar)

        # Confirm the real zero count: it should be reviewable without a
        # stock movement, supplier purchase, payment or financial posting.
        count_url = reverse("staff_inventory_stock_count")
        preview = self.client.post(count_url, {
            "action": "preview", "item": str(self.material.pk), "count": "0",
            "reason": "",
        })
        token = preview.context["preview"]["token"]
        response = self.client.post(count_url, {
            "action": "confirm", "confirmation_token": token,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

        self.assertEqual([x.pk for x in verified_low_stock_items()], [self.material.pk])
        dashboard = self.client.get(reverse("staff_inventory_home"))
        self.assertEqual([x.pk for x in dashboard.context["low_items"]], [self.material.pk])
        low_page = self.client.get(reverse("staff_inventory_low_stock"))
        self.assertContains(low_page, self.material.name_ar)
        self.assertContains(low_page, "هذا تنبيه للمراجعة")

        # Even with a count, a higher actual stock or a removed threshold
        # must clear the recommendation. Never auto-create purchases.
        self.material.current_quantity = Decimal("2.000")
        self.material.save(update_fields=["current_quantity", "updated_at"])
        self.assertEqual(verified_low_stock_items(), [])
        self.material.low_stock_threshold = None
        self.material.save(update_fields=["low_stock_threshold", "updated_at"])
        self.assertEqual(verified_low_stock_items(), [])

    def test_legacy_zero_and_cost_estimate_never_count_as_verified(self):
        self.assertEqual(counted_inventory_ids([self.material.pk]), set())
        item = inventory_completeness(self.material, counted=False)
        self.assertEqual(item["state"], "unreviewed")
        self.assertFalse(item["count_verified"])
        self.assertIn("جرد فعلي موثق", item["needs"])
        self.material.estimated_unit_cost_syp = Decimal("100")
        item = inventory_completeness(self.material, counted=False)
        self.assertFalse(item["financially_verified"])
        self.assertEqual(item["state"], "unreviewed")

    def test_identical_confirmed_zero_is_audited_without_creating_movement_or_payment(self):
        self.client.force_login(self.cashier)
        url = reverse("staff_inventory_stock_count")
        preview = self.client.post(url, {
            "action": "preview", "item": str(self.material.pk), "count": "0",
            "reason": "",
        })
        self.assertEqual(preview.status_code, 200)
        token = preview.context["preview"]["token"]
        self.assertEqual(self.client.post(url, {
            "action": "confirm", "confirmation_token": token,
        }).status_code, 302)
        self.assertEqual(self.client.post(url, {
            "action": "confirm", "confirmation_token": token,
        }).status_code, 302)
        self.material.refresh_from_db()
        self.assertEqual(self.material.current_quantity, Decimal("0"))
        self.assertEqual(StockMovement.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(PostingCommand.objects.filter(
            command="inventory.stock_count",
        ).count(), 1)
        self.assertEqual(AuditEvent.objects.filter(
            action="inventory_physical_count_verified",
        ).count(), 1)
        self.assertEqual(ActivityLog.objects.filter(
            action="inventory_stock_count_confirmed",
        ).count(), 1)
        self.assertEqual(counted_inventory_ids([self.material.pk]), {self.material.pk})
        item = inventory_completeness(self.material, counted=True)
        self.assertEqual(item["state"], "operational_ready")
        self.assertFalse(item["financially_verified"])

    def test_changed_physical_count_is_also_evidence_and_list_marks_count(self):
        self.client.force_login(self.admin)
        url = reverse("staff_inventory_stock_count")
        preview = self.client.post(url, {
            "action": "preview", "item": str(self.material.pk),
            "count": "0.250", "reason": "جرد ميداني فعلي",
        })
        self.client.post(url, {
            "action": "confirm", "confirmation_token": preview.context["preview"]["token"],
        })
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(counted_inventory_ids([self.material.pk]), {self.material.pk})
        page = self.client.get(reverse("staff_inventory_items"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "مكتملة تشغيلياً")
        self.assertContains(page, "بانتظار توثيق التكلفة")

    def test_sale_ready_independent_of_incomplete_recipe_and_estimated_cost(self):
        draft = recipe_completeness(self.product)
        self.assertEqual(draft["state"], "unreviewed")
        sale = product_completeness(self.product, draft)
        self.assertEqual(sale["state"], "operational_ready")
        self.assertEqual(sale["recipe_state"], "unreviewed")
        self.assertFalse(sale["financially_verified"])

        line = ProductRecipeItem.objects.create(
            product=self.product, inventory_item=self.material, unit=InventoryItem.Unit.KG,
            quantity_per_unit=Decimal("0.010"), is_active=False,
        )
        self.assertEqual(recipe_completeness(self.product)["state"], "needs_completion")
        self.client.force_login(self.admin)
        page = self.client.get(reverse("staff_recipe_manager"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "جاهز للبيع")
        self.assertContains(page, "مسودة تحتاج اعتماداً")
        self.assertContains(page, "كلفة أولية")
        line.is_active = True
        line.save(update_fields=["is_active"])
        self.assertEqual(recipe_completeness(self.product)["state"], "operational_ready")

    def test_internet_service_never_requires_a_consumable_recipe(self):
        internet = Product.objects.create(
            name_ar="إنترنت ساعة", category=self.category, price_syp=100,
            product_type=Product.ProductType.INTERNET, item_type=Product.ItemType.SERVICE,
            requires_preparation=False,
        )
        self.assertEqual(recipe_completeness(internet)["state"], "not_applicable")

    def test_unauthorized_inventory_read_cannot_learn_count_evidence(self):
        waiter = get_user_model().objects.create_user(
            username="ops-ready-waiter", phone="+963998000413", password="x", role="waiter",
        )
        self.client.force_login(waiter)
        response = self.client.get(reverse("staff_inventory_items"))
        self.assertEqual(response.status_code, 302)
