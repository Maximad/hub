import json

from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError

from core.models import InternetEntitlement
from core.services.mikrotik import MikroTikError
from core.services.network_backends import MikroTikNetworkBackend


class Command(BaseCommand):
    help = 'تخطيط أو تنفيذ تجهيز MikroTik لاستحقاق واحد'

    def add_arguments(self, parser):
        parser.add_argument('entitlement_id', type=int)
        parser.add_argument('--execute', action='store_true')

    def handle(self, *args, **options):
        try:
            entitlement = InternetEntitlement.objects.get(pk=options['entitlement_id'])
            backend = MikroTikNetworkBackend()
            plan = backend.plan(entitlement)
            safe = {key: value for key, value in plan.items() if key != 'password'}
            self.stdout.write(json.dumps({'dry_run': not options['execute'], 'mapping': safe}, ensure_ascii=False))
            if options['execute']:
                backend.provision_access(entitlement)
                self.stdout.write(self.style.SUCCESS('تم تجهيز الاستحقاق الواحد بنجاح.'))
        except InternetEntitlement.DoesNotExist as exc:
            raise CommandError('الاستحقاق غير موجود.') from exc
        except (MikroTikError, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
