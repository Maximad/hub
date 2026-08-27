import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.services.internet_operations import run_readonly_mikrotik_healthcheck


class Command(BaseCommand):
    help = 'فحص اتصال MikroTik للقراءة فقط'

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true')

    def handle(self, *args, **options):
        if not settings.MIKROTIK_ENABLED:
            result = {
                'ok': False,
                'enabled': False,
                'message': 'تكامل MikroTik معطّل؛ الوضع اليدوي آمن وفعّال.',
            }
            self.stdout.write(
                json.dumps(result, ensure_ascii=False)
                if options['json']
                else result['message']
            )
            return

        ok, message = run_readonly_mikrotik_healthcheck()
        result = {'ok': ok, 'enabled': True, 'message': message}
        if not ok:
            if options['json']:
                self.stdout.write(json.dumps(result, ensure_ascii=False))
            raise CommandError(message)

        self.stdout.write(
            json.dumps(result, ensure_ascii=False)
            if options['json']
            else self.style.SUCCESS(message)
        )
