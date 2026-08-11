# مصفوفة ثوابت الترحيل المالي

هذه المصفوفة هي مرجع طبقات الحماية. قاعدة البيانات هي الحاجز الأخير للثوابت المحلية،
بينما تنفّذ خدمات `core.services.posting` الفحوص التي تحتاج قراءة أكثر من صف داخل معاملة
وأقفال `SELECT … FOR UPDATE`. ولا يُعدّ `full_clean()` بديلاً عن أي منهما.

| الثابت | قيد PostgreSQL | فحص الخدمة المقفلة | فحص المطابقة | الاختبار |
|---|---|---|---|---|
| ترحيل نشط واحد لكل مصدر وعملية | `unique_active_posting_per_source` (جزئي للحالتين pending/posted) | `dispatch` يقفل المصدر وإيصال idempotency | `reconcile_postings` يرصد كل كتابة مالية تجاوزت الخدمة | `PostingInvariantConstraintTests.test_one_active_source_posting` |
| حركة صندوق نشطة واحدة للمصروف | `unique_active_expense_cash_movement` (جزئي: غير ملغاة) | `_sync_cash` يستخدم `select_for_update().update_or_create` | رصد `CashMovement` بلا `PostingCommand` | `PostingInvariantConstraintTests.test_one_active_expense_cash_movement` |
| حركة استلام واحدة لكل بند شراء | `unique_stock_receipt_per_purchase_line` (جزئي: استلام غير ملغى) | `purchases.receive` يقفل الشراء والبنود والمخزون ويقارن مجموع الكمية | رصد `StockMovement` بلا `PostingCommand` ومقارنة كمية البند عند إعادة الاستلام | `PostingServiceInvariantTests.test_purchase_receipt_is_exact_and_idempotent` |
| عكس واحد لكل سجل أصلي | `unique_reversal_per_posting_batch` و`unique_reversal_per_audit_event` | أمر العكس يمر عبر `dispatch` وقفل المصدر | سجل التدقيق وإيصال الأمر يربطان الأصل بالعكس | `PostingInvariantConstraintTests.test_one_reversal_per_original` |
| كل مبلغ/كمية واجبة الإيجاب موجب | `payment_amount_positive`, `expense_amount_positive`, `cash_movement_amount_positive`, `purchase_item_quantity_positive`, `stock_movement_quantity_positive`, `posting_entry_one_positive_side`, `transfer_amount_positive` | خدمات الدفع والاستلام والتحويل ترفض الصفر والسالب برسالة عربية | فحص التجاوز يرصد الكتابات المباشرة | `PostingInvariantConstraintTests.test_positive_amount` |
| اتجاه المخزون والصندوق صالح | `cash_movement_direction_valid`, `stock_movement_direction_valid` | `full_clean()` في خدمات الترحيل | فحص التجاوز للصفوف المنشأة خارج الخدمة | `PostingInvariantConstraintTests.test_valid_direction` |
| حسابا التحويل مختلفان | `transfer_accounts_distinct` | خدمة التحويل تتحقق قبل إنشاء الطرفين | مطابقة طرفي التحويل وإيصال أمره | `PostingInvariantConstraintTests.test_distinct_transfer_accounts` |
| حقول الحالة/الإلغاء/العكس متناسقة | `payment_reversal_fields_consistent`, `cash_movement_cancel_fields_consistent`, `stock_movement_cancel_fields_consistent`, `posting_batch_valid_state_times`, `posting_reversal_is_posted`, `transfer_valid_state_batch` | خدمات الإلغاء والعكس تغيّر الحقول معاً داخل معاملة | سجل التدقيق غير قابل للتعديل، وفحص التجاوز يرصد المسار غير المدعوم | `PostingInvariantConstraintTests.test_reversal_fields_are_consistent` |
| لا تتجاوز دفعات الطلب إجماليه | — (يعتمد على عدة صفوف) | `order_payments.collect` يقفل الطلب ثم يجمع الدفعات النشطة | تقارير الطلب تقارن المدفوع بالإجمالي | `PostingServiceInvariantTests.test_order_overpayment_is_arabic` |
| كمية استلام الشراء تساوي كمية البند | قيد التفرد يمنع استلاماً نشطاً ثانياً | `purchases.receive` يقفل البنود ويجمع حركات الاستلام قبل التطبيق | إعادة الاستلام تتحقق من التطابق ولا تضيف مخزوناً | `PostingServiceInvariantTests.test_purchase_receipt_is_exact_and_idempotent` |
| القيد المحاسبي متوازن قبل الترحيل | `posting_entry_one_positive_side` يحمي كل سطر؛ التوازن كليّ متعدد الصفوف | `ledger.post_balanced_batch` يقفل الدفعة ويجمع المدين والدائن | `PostingBatch.is_balanced()` يستخدم في المطابقة والإدارة | `PostingServiceInvariantTests.test_unbalanced_batch_is_arabic` |

## سياسة المشتريات عند الإطلاق

استلام المشتريات حركة مخزون **تشغيلية فقط** ما دامت D07–D11 غير معتمدة. تنشئ
الخدمة `FinanceReviewItem` واحداً للشراء، ولا تنشئ قيد مخزون/مورد أو حساب دائن أو
مقاصة مالك. كما يبقى دفع المورد محظوراً برسالة عربية صريحة. لا يتغير ذلك إلا بعد
اعتماد القرارات في `domain-decisions.md` وإضافة مسار ترحيل واختبارات قبول مستقلة.

## عرض الأخطاء

ترث أخطاء المجال من `ValidationError`. تستخرج واجهات الفريق الرسائل الحقلية بواسطة
`_validation_messages` وتعرض النص العربي كما صدر من الخدمة. وتعرض نماذج Django admin
رسائل `ValidationError` العربية وتعريفات `violation_error_message` الخاصة بالقيود من دون
استبدالها برسائل تقنية من قاعدة البيانات.

## تشغيل المطابقة

شغّل `python manage.py reconcile_postings`. يفشل الأمر إذا وجد سجلات مالية منشأة خارج
خدمات الترحيل، ويخزّن كل حالة في `PostingReconciliationFailure` لتبقى قابلة للمراجعة.
