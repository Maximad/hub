"""Read-only Hub v1 launch-specific readiness checks.

This command complements the deeper launch_readiness, internet_readiness and
push_readiness commands. It focuses on configuration mistakes that directly
break the launch flows finalized in the Hub v1 sprint.
"""

import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from catalog.models import PrepStation, ProductOptionGroupAssignment
from core.models import Product
from core.services.internet_readiness import internet_readiness_report


class Command(BaseCommand):
    help = 'Read-only Hub v1 launch-flow readiness audit.'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json')
        parser.add_argument('--strict', action='store_true')

    def add(self, status, code, message, resolution='لا يلزم إجراء.'):
        self.results.append({
            'status': status,
            'code': code,
            'message': message,
            'resolution': resolution,
        })

    def handle(self, *args, **options):
        self.results = []
        self._catalog_options()
        self._catalog_channels()
        self._prep_roles()
        self._internet()

        counts = {
            level: sum(row['status'] == level for row in self.results)
            for level in ('PASS', 'WARN', 'FAIL')
        }
        overall = 'FAIL' if counts['FAIL'] else ('WARN' if counts['WARN'] else 'PASS')
        payload = {
            'status': overall,
            'counts': counts,
            'checks': self.results,
            'read_only': True,
        }

        if options['as_json']:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            self.stdout.write('Hub v1 launch readiness (read-only)')
            for row in self.results:
                self.stdout.write(f"{row['status']:4} {row['code']}: {row['message']}")
                if row['status'] != 'PASS':
                    self.stdout.write(f"     الحل: {row['resolution']}")
            self.stdout.write(
                f"النتيجة: {overall} — PASS={counts['PASS']} WARN={counts['WARN']} FAIL={counts['FAIL']}"
            )

        if counts['FAIL'] or (options['strict'] and counts['WARN']):
            raise CommandError('Hub v1 readiness failed.')

    def _catalog_options(self):
        invalid = []
        empty = []
        for assignment in (
            ProductOptionGroupAssignment.objects
            .filter(is_active=True)
            .select_related('product', 'group')
            .prefetch_related('group__options')
        ):
            group = assignment.group
            if not group.is_active or not group.applies_to_product(assignment.product):
                invalid.append(assignment.pk)
                continue
            if not any(option.is_active for option in group.options.all()):
                empty.append(assignment.pk)

        if invalid:
            self.add(
                'FAIL',
                'invalid_option_assignments',
                f'روابط خيارات فعالة لن تظهر بشكل صحيح: {len(invalid)}.',
                'صحح تصنيف المنتج/المجموعة أو عطّل الربط غير الصالح.',
            )
        else:
            self.add('PASS', 'invalid_option_assignments', 'لا توجد روابط خيارات فعالة غير صالحة.')

        if empty:
            self.add(
                'FAIL',
                'empty_option_assignments',
                f'روابط خيارات فعالة بلا أي خيار فعّال: {len(empty)}.',
                'فعّل خياراً واحداً على الأقل أو عطّل ربط المجموعة بالمنتج.',
            )
        else:
            self.add('PASS', 'empty_option_assignments', 'كل ربط خيارات فعّال يملك خياراً فعّالاً.')

    def _catalog_channels(self):
        # POS intentionally uses the public menu as its visibility baseline.
        # These rows are not broken anymore, but surfacing them helps admins see
        # legacy configuration that no longer has an effect.
        hidden_but_pos = Product.objects.filter(
            is_available=True,
            visible_on_qr=False,
            visible_on_pos=True,
            orderable_on_pos=True,
        ).count()
        self.add(
            'WARN' if hidden_but_pos else 'PASS',
            'legacy_pos_only_visibility',
            (
                f'{hidden_but_pos} منتجات مهيأة كنقطة بيع لكنها مخفية من المنيو؛ '
                'Hub v1 سيخفيها من POS أيضاً.'
                if hidden_but_pos
                else 'لا توجد منتجات تعتمد على اختلاف قديم بين ظهور المنيو وPOS.'
            ),
            'راجع المنتج إذا كان المقصود أن يظهر في التشغيل؛ اجعل ظهوره في المنيو صريحاً.',
        )

    def _prep_roles(self):
        bar = PrepStation.objects.filter(code='bar', is_active=True).first()
        kitchen = PrepStation.objects.filter(code='kitchen', is_active=True).first()
        if kitchen:
            self.add('PASS', 'kitchen_station', 'محطة المطبخ الفعالة موجودة.')
        else:
            self.add('FAIL', 'kitchen_station', 'لا توجد محطة مطبخ فعالة.', 'فعّل محطة التحضير kitchen.')

        if bar:
            self.add('PASS', 'bar_station', 'محطة البار الفعالة موجودة.')
            User = get_user_model()
            bartender_count = User.objects.filter(role='bartender', is_active=True).count()
            self.add(
                'PASS' if bartender_count else 'WARN',
                'bartender_coverage',
                f'موظفو البار الفعالون: {bartender_count}.',
                'أنشئ/عيّن مستخدم بدور البار قبل الاعتماد على تنبيهات البار.' if not bartender_count else 'لا يلزم إجراء.',
            )
        else:
            self.add('WARN', 'bar_station', 'محطة البار غير مفعلة.', 'فعّل bar إذا كان البار سيستخدم في الافتتاح.')

    def _internet(self):
        report = internet_readiness_report()
        findings = report.get('findings', [])
        failures = [row for row in findings if row.get('severity') == 'FAIL']
        warnings = [row for row in findings if row.get('severity') == 'WARN']
        if failures:
            self.add(
                'FAIL',
                'internet_readiness',
                f'جاهزية الإنترنت تحتوي {len(failures)} أخطاء مانعة.',
                'شغّل python manage.py internet_readiness للحصول على التفاصيل.',
            )
        elif warnings:
            self.add(
                'WARN',
                'internet_readiness',
                f'جاهزية الإنترنت تحتوي {len(warnings)} تحذيرات.',
                'شغّل python manage.py internet_readiness وراجع التحذيرات قبل التشغيل.',
            )
        else:
            self.add('PASS', 'internet_readiness', 'جاهزية الإنترنت لا تحتوي أخطاء أو تحذيرات.')
