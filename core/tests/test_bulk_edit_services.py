from django.test import TestCase

from catalog.models import ProductMedia
from core.models import ActivityLog, Category, Product
from core.services.bulk_edit import (
    DestructiveOperationNotSupported,
    apply_bulk_update,
    deactivate_product_media,
    ensure_operation_supported,
    preview_bulk_update,
    validate_selected_objects,
)


class BulkEditServiceTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name_ar="مشروبات")
        self.product = Product.objects.create(
            category=self.category,
            name_ar="قهوة",
            name_en="Coffee",
            price_syp=1000,
        )
        self.other_product = Product.objects.create(
            category=self.category,
            name_ar="شاي",
            name_en="Tea",
            price_syp=800,
        )

    def test_validate_selected_objects_accepts_primary_keys_and_public_codes(self):
        objects = validate_selected_objects(
            Product,
            [self.product.pk, str(self.other_product.public_code)],
        )

        self.assertEqual(
            [obj.pk for obj in objects], [self.product.pk, self.other_product.pk]
        )

    def test_preview_bulk_update_reports_changes_without_saving(self):
        result = preview_bulk_update(
            Product,
            [str(self.product.public_code)],
            {"price_syp": 1200, "is_available": False},
        )

        self.assertFalse(result.saved)
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(
            result.changes[0].changes,
            {
                "price_syp": {"before": 1000, "after": 1200},
                "is_available": {"before": True, "after": False},
            },
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.price_syp, 1000)
        self.assertTrue(self.product.is_available)
        self.assertEqual(ActivityLog.objects.count(), 0)

    def test_apply_bulk_update_saves_inside_atomic_and_logs_before_after_values(self):
        result = apply_bulk_update(
            Product,
            [self.product.pk],
            {"price_syp": 1500, "visible_on_qr": False},
            action="staff_bulk_product_edit",
        )

        self.assertTrue(result.saved)
        self.assertEqual(result.matched_count, 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.price_syp, 1500)
        self.assertFalse(self.product.visible_on_qr)

        log = ActivityLog.objects.get(pk=result.activity_log_ids[0])
        self.assertEqual(log.action, "staff_bulk_product_edit")
        self.assertEqual(log.details["model"], "core.Product")
        self.assertEqual(
            log.details["object_identifier"], str(self.product.public_code)
        )
        self.assertEqual(
            log.details["changes"],
            {
                "price_syp": {"before": 1000, "after": 1500},
                "visible_on_qr": {"before": True, "after": False},
            },
        )

    def test_destructive_operations_are_rejected_for_products_and_media(self):
        with self.assertRaises(DestructiveOperationNotSupported):
            ensure_operation_supported(Product, "delete", explicitly_supported=True)
        with self.assertRaises(DestructiveOperationNotSupported):
            ensure_operation_supported(
                ProductMedia, "delete", explicitly_supported=True
            )

    def test_deactivate_product_media_updates_is_active_instead_of_deleting(self):
        media = ProductMedia.objects.create(
            product=self.product,
            media_type=ProductMedia.MediaType.IMAGE,
            url="https://example.com/coffee.jpg",
            is_active=True,
        )

        result = deactivate_product_media([str(media.uuid)])

        self.assertTrue(result.saved)
        media.refresh_from_db()
        self.assertFalse(media.is_active)
        self.assertTrue(ProductMedia.objects.filter(pk=media.pk).exists())
        log = ActivityLog.objects.get(pk=result.activity_log_ids[0])
        self.assertEqual(log.action, "bulk_deactivate_product_media")
        self.assertEqual(log.details["model"], "catalog.ProductMedia")
        self.assertEqual(
            log.details["changes"]["is_active"], {"before": True, "after": False}
        )
