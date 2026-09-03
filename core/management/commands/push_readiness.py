"""Read-only operational readiness for staff Web Push."""

from __future__ import annotations

import json
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import NotificationLog, PushSubscription


class Command(BaseCommand):
    help = 'تدقيق جاهزية تنبيهات Web Push للقراءة فقط.'

    STALE_PENDING_MINUTES = 10
    FAILURE_WINDOW_HOURS = 24

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
        enabled = bool(getattr(settings, 'PUSH_NOTIFICATIONS_ENABLED', False))

        if not enabled:
            self.add(
                'PASS',
                'push_disabled',
                'تنبيهات الخلفية معطلة؛ العامل سيبقى خاملاً ولن يرسل.',
            )
        else:
            self._configuration()
            self._subscriptions()
            self._queue_health()

        counts = {
            level: sum(row['status'] == level for row in self.results)
            for level in ('PASS', 'WARN', 'FAIL')
        }
        overall = 'FAIL' if counts['FAIL'] else ('WARN' if counts['WARN'] else 'PASS')
        payload = {
            'status': overall,
            'enabled': enabled,
            'counts': counts,
            'checks': self.results,
            'read_only': True,
        }

        if options['as_json']:
            self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            self.stdout.write('تدقيق جاهزية Web Push (للقراءة فقط)')
            for row in self.results:
                self.stdout.write(f"{row['status']:4} {row['code']}: {row['message']}")
                if row['status'] != 'PASS':
                    self.stdout.write(f"     الحل: {row['resolution']}")
            self.stdout.write(
                f"النتيجة: {overall} — PASS={counts['PASS']} WARN={counts['WARN']} FAIL={counts['FAIL']}"
            )

        if counts['FAIL'] or (options['strict'] and counts['WARN']):
            raise SystemExit(1)

    def _configuration(self):
        provider = str(getattr(settings, 'PUSH_PROVIDER', '')).strip().lower()
        public_key = str(getattr(settings, 'VAPID_PUBLIC_KEY', '')).strip()
        private_key = str(getattr(settings, 'VAPID_PRIVATE_KEY', '')).strip()
        subject = str(getattr(settings, 'VAPID_SUBJECT', '')).strip()
        hosts = tuple(getattr(settings, 'PUSH_ENDPOINT_ALLOWED_HOSTS', ()) or ())

        valid = bool(
            provider == 'webpush'
            and public_key
            and private_key
            and public_key != private_key
            and (subject.startswith('mailto:') or subject.startswith('https://'))
            and hosts
        )
        self.add(
            'PASS' if valid else 'FAIL',
            'push_configuration',
            'إعداد Web Push مكتمل دون عرض أي مفاتيح.' if valid else 'إعداد Web Push ناقص أو غير صالح.',
            'راجع PUSH_PROVIDER وVAPID_* وقائمة مزودي push المسموحين؛ لا تطبع المفاتيح في السجلات.',
        )

    def _subscriptions(self):
        active = PushSubscription.objects.filter(
            provider='webpush',
            permission_state='granted',
            is_active=True,
            revoked_at__isnull=True,
        ).count()
        revoked = PushSubscription.objects.filter(revoked_at__isnull=False).count()
        self.add(
            'PASS' if active else 'WARN',
            'push_subscriptions',
            f'أجهزة push النشطة: {active}؛ الاشتراكات الملغاة: {revoked}.',
            'سجّل جهاز اختبار واحداً على الأقل قبل توسيع التفعيل.' if not active else 'لا يلزم إجراء.',
        )

    def _queue_health(self):
        now = timezone.now()
        pending = NotificationLog.objects.filter(channel='browser', status='pending')
        pending_count = pending.count()
        stale_cutoff = now - timedelta(minutes=self.STALE_PENDING_MINUTES)
        stale_due = pending.filter(next_attempt_at__lte=stale_cutoff).count()
        failure_cutoff = now - timedelta(hours=self.FAILURE_WINDOW_HOURS)
        recent_failed = NotificationLog.objects.filter(
            channel='browser',
            status='failed',
            updated_at__gte=failure_cutoff,
        ).count()

        self.add(
            'WARN' if stale_due else 'PASS',
            'push_queue',
            f'قيد الانتظار: {pending_count}؛ متأخر عن موعد المحاولة: {stale_due}.',
            'تحقق من notification-worker وسجلاته إذا بقيت سجلات مستحقة لأكثر من عشر دقائق.' if stale_due else 'لا يلزم إجراء.',
        )
        self.add(
            'WARN' if recent_failed else 'PASS',
            'push_recent_failures',
            f'عمليات إرسال فاشلة خلال آخر 24 ساعة: {recent_failed}.',
            'راجع رموز الأخطاء المجرّدة وحالة الاشتراكات دون عرض endpoints أو مفاتيح.' if recent_failed else 'لا يلزم إجراء.',
        )
