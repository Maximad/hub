import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.mikrotik import MikroTikError
from core.services.network_backends import MikroTikNetworkBackend


class Command(BaseCommand):
    help = 'فحص اتصال MikroTik للقراءة فقط'

    def add_arguments(self, parser): parser.add_argument('--json', action='store_true')

    def handle(self, *args, **options):
        if not settings.MIKROTIK_ENABLED:
            result = {'ok': False, 'enabled': False, 'message': 'تكامل MikroTik معطّل؛ الوضع اليدوي آمن وفعّال.'}
            self.stdout.write(json.dumps(result, ensure_ascii=False) if options['json'] else result['message'])
            return
        try:
            MikroTikNetworkBackend().test_connection()
        except MikroTikError as exc:
            result = {'ok': False, 'enabled': True, 'message': str(exc)}
            if options['json']: self.stdout.write(json.dumps(result, ensure_ascii=False))
            raise CommandError(result['message'])
        result = {'ok': True, 'enabled': True, 'message': 'اتصال MikroTik سليم (فحص قراءة فقط).'}
        self.stdout.write(json.dumps(result, ensure_ascii=False) if options['json'] else self.style.SUCCESS(result['message']))
