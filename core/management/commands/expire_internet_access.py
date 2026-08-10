from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import InternetEntitlement
from core.services.network_backends import get_network_backend

class Command(BaseCommand):
    help = 'Expire Internet entitlements whose validity window has ended (idempotent).'
    def handle(self, *args, **options):
        count = 0
        for entitlement in InternetEntitlement.objects.filter(status=InternetEntitlement.Status.ACTIVE, valid_until__lte=timezone.now()):
            entitlement.status = InternetEntitlement.Status.EXPIRED
            entitlement.save(update_fields=['status', 'updated_at'])
            get_network_backend(entitlement.network_backend).expire_access(entitlement)
            count += 1
        self.stdout.write(self.style.SUCCESS(f'Expired {count} Internet entitlement(s).'))
