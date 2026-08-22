"""A single, read-only go/no-go audit for the first production launch."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, no_translations
from django.db import connection
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.questioner import MigrationQuestioner
from django.db.migrations.state import ProjectState
from django.test import Client, override_settings
from django.utils import timezone

from catalog.models import MenuSection
from core.management.commands.reset_prelaunch_data import DELETE_MODELS
from core.models import (ExchangeRate, ExpenseCategory, FinanceReviewItem, FinancialAccount,
                         PostingReconciliationFailure, SystemSetting)
from core.services.finance_reconciliation import FinanceReconciler
from core.services.network_backends import get_network_backend
from core.services.posting.rollout import current_rollout
from internet.models import WifiNetwork


class Command(BaseCommand):
    help = 'تدقيق جاهزية الإطلاق للقراءة فقط (PASS/WARN/FAIL).'
    requires_system_checks = []

    ROUTES = ('/menu/', '/admin/login/', '/staff/', '/staff/orders/', '/staff/pos/')
    PRE_LEDGER_EXPECTED_FINDINGS = frozenset({'payment_missing_posting'})

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', dest='as_json')
        parser.add_argument('--strict', action='store_true')
        parser.add_argument('--allow-operational-data', action='store_true',
                            help='Allow intentionally retained pre-launch operational records.')

    def handle(self, *args, **options):
        self.results = []
        gates = (
            self._migrations, self._security, self._storage, self._backup,
            self._reference_data, self._admin, self._usd, self._rollout,
            self._integrity, lambda: self._operational(options['allow_operational_data']),
            self._routes, self._network,
        )
        for gate in gates:
            try:
                gate()
            except Exception as exc:
                self.add('FAIL', gate.__name__.lstrip('_'),
                         f'تعذر تنفيذ الفحص ({exc.__class__.__name__}).',
                         'راجع السجلات والإعدادات ثم أعد التدقيق.')
        counts = {level: sum(r['status'] == level for r in self.results)
                  for level in ('PASS', 'WARN', 'FAIL')}
        overall = 'FAIL' if counts['FAIL'] else ('WARN' if counts['WARN'] else 'PASS')
        payload = {'status': overall, 'counts': counts, 'checks': self.results,
                   'read_only': True}
        if options['as_json']:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write('تدقيق جاهزية الإطلاق (للقراءة فقط)')
            for row in self.results:
                self.stdout.write(f"{row['status']:4} {row['code']}: {row['message']}")
                if row['status'] != 'PASS':
                    self.stdout.write(f"     الحل: {row['resolution']}")
            self.stdout.write(f"النتيجة: {overall} — PASS={counts['PASS']} WARN={counts['WARN']} FAIL={counts['FAIL']}")
        if counts['FAIL'] or (options['strict'] and counts['WARN']):
            raise SystemExit(1)

    def add(self, status, code, message, resolution='لا يلزم إجراء.'):
        self.results.append({'status': status, 'code': code, 'message': message,
                             'resolution': resolution})

    @no_translations
    def _migrations(self):
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        state = executor.loader.project_state()
        changes = MigrationAutodetector(state, ProjectState.from_apps(apps),
                                        MigrationQuestioner(specified_apps=set(), dry_run=True)).changes(
                                            graph=executor.loader.graph)
        if pending:
            self.add('FAIL', 'migrations_applied', f'توجد {len(pending)} ترحيلات غير مطبقة.', 'شغّل migrate ثم أعد الفحص.')
        else:
            self.add('PASS', 'migrations_applied', 'كل الترحيلات مطبقة.')
        if changes:
            self.add('FAIL', 'model_migrations', 'توجد تغييرات نماذج بلا ترحيل.', 'أنشئ الترحيلات وراجعها وانشرها.')
        else:
            self.add('PASS', 'model_migrations', 'لا توجد تغييرات نماذج بلا ترحيل.')

    def _security(self):
        placeholder = not settings.SECRET_KEY or settings.SECRET_KEY.lower().startswith('django-insecure-') or settings.SECRET_KEY in {'debug-only-not-for-production', 'change-me-in-production'}
        checks = {
            'debug_disabled': not settings.DEBUG,
            'secret_key_safe': not placeholder,
            'https_redirect': bool(settings.SECURE_SSL_REDIRECT),
            'secure_cookies': bool(settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE),
            'allowed_hosts': bool(settings.ALLOWED_HOSTS) and '*' not in settings.ALLOWED_HOSTS,
            'csrf_origins': bool(settings.CSRF_TRUSTED_ORIGINS) and all(str(x).startswith('https://') for x in settings.CSRF_TRUSTED_ORIGINS),
        }
        for code, ok in checks.items():
            self.add('PASS' if ok else 'FAIL', code, 'الإعداد آمن.' if ok else 'إعداد الإنتاج غير آمن أو ناقص.',
                     'صحح متغيرات بيئة Django الخاصة بالأمان؛ لن يعرض التدقيق قيمها.')

    def _storage(self):
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            ok = cursor.fetchone() == (1,)
        self.add('PASS' if ok else 'FAIL', 'database', 'قاعدة البيانات متاحة.' if ok else 'قاعدة البيانات غير متاحة.', 'تحقق من خدمة قاعدة البيانات واتصال التطبيق.')
        for code, path in (('media_storage', Path(settings.MEDIA_ROOT)), ('static_storage', Path(settings.STATIC_ROOT))):
            ok = path.is_dir() and os.access(path, os.R_OK | os.X_OK)
            self.add('PASS' if ok else 'FAIL', code, 'التخزين متاح للقراءة.' if ok else 'مسار التخزين غير متاح.', 'أنشئ/اربط المسار واضبط صلاحيات القراءة للتطبيق.')

    def _backup(self):
        root = Path(getattr(settings, 'PRELAUNCH_BACKUP_ROOT', '/opt/hub/backups/production'))
        candidates = sorted((p for p in root.glob('hub-*/manifest.txt') if (p.parent / 'SUCCESS').is_file()), reverse=True) if root.is_dir() else []
        if not candidates:
            self.add('FAIL', 'recent_backup', 'لا توجد نسخة احتياطية ناجحة.', 'شغّل backup-production.sh وتحقق منها.')
            self.add('FAIL', 'restore_verified', 'لا يوجد دليل تحقق من الاستعادة.', 'شغّل verify-production-backup.sh على النسخة الحديثة.')
            return
        manifest = candidates[0]
        values = dict(line.split('=', 1) for line in manifest.read_text().splitlines() if '=' in line)
        created = datetime.strptime(values.get('created_utc', ''), '%Y%m%dT%H%M%SZ').replace(tzinfo=datetime_timezone.utc)
        max_hours = getattr(settings, 'PRELAUNCH_BACKUP_MAX_AGE_HOURS', 24)
        recent = 0 <= (timezone.now() - created).total_seconds() <= max_hours * 3600
        self.add('PASS' if recent else 'FAIL', 'recent_backup', 'بيان النسخة الاحتياطية حديث.' if recent else 'بيان النسخة الاحتياطية قديم.', 'أنشئ نسخة كاملة حديثة.')
        evidence = manifest.parent / 'RESTORE_VERIFIED'
        verified = evidence.is_file() and evidence.stat().st_mtime >= manifest.stat().st_mtime
        self.add('PASS' if verified else 'FAIL', 'restore_verified', 'يوجد دليل حديث لاختبار الاستعادة.' if verified else 'دليل اختبار الاستعادة مفقود أو قديم.', 'شغّل verify-production-backup.sh بعد إنشاء النسخة.')

    def _reference_data(self):
        models = (SystemSetting, MenuSection, ExpenseCategory, FinancialAccount)
        missing = [m._meta.label for m in models if not m.objects.exists()]
        self.add('FAIL' if missing else 'PASS', 'reference_data',
                 f"بيانات مرجعية ناقصة ({len(missing)} مجموعة)." if missing else 'البيانات المرجعية المطلوبة موجودة.',
                 'نفّذ أوامر bootstrap/import المعتمدة للبيانات المرجعية.')

    def _admin(self):
        exists = get_user_model().objects.filter(is_active=True, is_staff=True, is_superuser=True).exists()
        self.add('PASS' if exists else 'FAIL', 'active_admin', 'يوجد مسؤول نشط (دون عرض هويته).' if exists else 'لا يوجد مسؤول نشط.', 'أنشئ حساب مسؤول واحداً على الأقل وفعّله.')

    def _usd(self):
        required = getattr(settings, 'LAUNCH_USD_REQUIRED', True)
        current = ExchangeRate.objects.filter(foreign_currency='USD', effective_date__lte=timezone.localdate(), superseded_by__isnull=True).exists()
        status = 'PASS' if current else ('FAIL' if required else 'WARN')
        self.add(status, 'usd_rate', 'يوجد سعر USD حالي.' if current else ('سعر USD مطلوب وغير موجود.' if required else 'USD غير مطلوب حالياً ولا يوجد سعر.'), 'أضف سعر USD نافذاً أو وثّق LAUNCH_USD_REQUIRED=False.')

    def _rollout(self):
        rollout = current_rollout()
        flags = {'ledger_writes': rollout.ledger_writes, 'dual_reads': rollout.dual_reads,
                 'report_reads': rollout.report_reads}
        self.add('PASS', 'rollout_flags', 'القيم الفعلية: ' + ', '.join(f'{k}={str(v).lower()}' for k, v in flags.items()))

    def _integrity(self):
        findings = FinanceReconciler().run()
        rollout = current_rollout()
        expected_pre_ledger = []
        blocking_findings = findings
        if not rollout.ledger_writes:
            expected_pre_ledger = [
                row for row in findings if row.get('code') in self.PRE_LEDGER_EXPECTED_FINDINGS
            ]
            blocking_findings = [
                row for row in findings if row.get('code') not in self.PRE_LEDGER_EXPECTED_FINDINGS
            ]

        unresolved = (
            PostingReconciliationFailure.objects.filter(resolved_at__isnull=True).count()
            + FinanceReviewItem.objects.filter(resolved_at__isnull=True).count()
        )
        blocking_count = len(blocking_findings) + unresolved

        if blocking_count:
            self.add(
                'FAIL', 'integrity_reconciliation',
                f'نتائج سلامة/تسوية مانعة: {blocking_count}.',
                'شغّل reconcile_finance وreconcile_postings وعالج كل نتيجة مانعة؛ لا تستخدم التدقيق للتنظيف.',
            )
        elif expected_pre_ledger:
            self.add(
                'WARN', 'integrity_reconciliation',
                f'نتائج متوقعة قبل تفعيل ledger writes: {len(expected_pre_ledger)}.',
                'تبقى ظاهرة في reconcile_finance؛ عالجها قبل تفعيل POSTING_LEDGER_WRITES_ENABLED.',
            )
        else:
            self.add('PASS', 'integrity_reconciliation', 'لا توجد نتائج سلامة/تسوية مانعة.')

    def _operational(self, allowed):
        count = sum(apps.get_model(label).objects.count() for label in DELETE_MODELS)
        status = 'WARN' if count and allowed else ('FAIL' if count else 'PASS')
        self.add(status, 'prelaunch_operational_data', f'السجلات التشغيلية قبل الإطلاق: {count}.', 'نفّذ reset_prelaunch_data بإجراءاته الآمنة، أو استخدم --allow-operational-data إذا كان الاحتفاظ مقصوداً.')

    def _routes(self):
        client, failures = Client(), []
        with override_settings(ALLOWED_HOSTS=['testserver'], DEBUG_PROPAGATE_EXCEPTIONS=True):
            for route in self.ROUTES:
                response = client.get(route)
                if response.status_code not in {200, 301, 302, 403}:
                    failures.append((route, response.status_code))
        self.add('FAIL' if failures else 'PASS', 'critical_routes', f'مسارات غير سليمة: {len(failures)}.' if failures else 'مسارات القائمة والإدارة والموظفين سليمة.', 'راجع URL والواجهات والسجلات لمسارات القائمة والإدارة والموظفين.')

    def _network(self):
        codes = set(WifiNetwork.objects.filter(is_active=True).values_list('network_backend', flat=True)) or {'manual'}
        unavailable = []
        for code in codes:
            try:
                if code == 'mikrotik' and not getattr(settings, 'MIKROTIK_ENABLED', False):
                    unavailable.append(code)
                    continue
                if not get_network_backend(code).test_connection(): unavailable.append(code)
            except Exception:
                unavailable.append(code)
        self.add('FAIL' if unavailable else 'PASS', 'network_backend', f'واجهات شبكة غير متاحة: {len(unavailable)}.' if unavailable else 'واجهة الشبكة المكوّنة متاحة.', 'صحح إعداد واجهة الشبكة أو عطّل الشبكة غير الجاهزة.')
