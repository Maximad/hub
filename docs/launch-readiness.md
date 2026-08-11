# تدقيق جاهزية الإطلاق

بعد النشر شغّل `python manage.py launch_readiness`. يعرض الأمر `PASS` و`WARN`
و`FAIL` بالعربية ولا يكتب في قاعدة البيانات أو التخزين ولا يغيّر أعلام الإطلاق.
يعيد `FAIL` الرمز 1، بينما `WARN` يعيد 0 إلا مع `--strict`. للاستخدام الآلي:
`python manage.py launch_readiness --json`.

## معالجة النتائج

| الفحص | المعالجة |
|---|---|
| migrations | شغّل `migrate`، وأنشئ ترحيلاً لأي تغيير نماذج. |
| security | عطّل DEBUG، واضبط مفتاحاً غير تجريبي وHTTPS والكوكيز والمضيفين ومصادر CSRF عبر البيئة. لا يطبع الأمر أياً من القيم الحساسة. |
| database/media/static | شغّل الخدمات واربط مسارات التخزين بصلاحية قراءة. الفحص لا ينشئ ملف اختبار. |
| backup/restore | شغّل `backup-production.sh` ثم `verify-production-backup.sh`؛ الأخير ينشئ `RESTORE_VERIFIED` بعد استعادة ناجحة فقط. |
| reference_data | نفّذ أوامر bootstrap/import المعتمدة حتى توجد إعدادات النظام وأقسام القائمة وتصنيفات المصروفات والحسابات. |
| active_admin | أنشئ وفعّل مسؤولاً؛ لا تعرض النتيجة هويته. |
| usd_rate | أضف سعراً نافذاً. إن لم يستخدم الموقع USD اضبط `LAUNCH_USD_REQUIRED=False` لتحويل الغياب إلى WARN. |
| rollout_flags | راجع القيم الفعلية المعروضة؛ الأمر لا يغيرها. |
| integrity_reconciliation | شغّل أوامر التسوية للقراءة فقط، حقق في الأيتام والتكرارات والنتائج، ثم عالجها بإجراء تشغيلي منفصل. |
| prelaunch_operational_data | استخدم إجراء `reset_prelaunch_data` الموثق. فقط إذا كان الاحتفاظ مقصوداً استخدم `--allow-operational-data` (WARN ولا يحذف شيئاً). |
| critical_routes/network_backend | أصلح توجيه القائمة/الإدارة/الموظفين واتصال backend المكوّن ثم أعد التدقيق. |

يشغّل النشر التدقيق بعد اكتمال النشر للعرض فقط؛ لا يستخدمه خطوة ترحيل أو تنظيف.
