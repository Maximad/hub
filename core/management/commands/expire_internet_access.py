from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import InternetEntitlement
from core.services.internet_lifecycle import expire_internet_entitlement

class Command(BaseCommand):
    help = 'Expire Internet entitlements whose validity window has ended (idempotent).'
    def handle(self, *args, **options):
        count = 0
        for entitlement in InternetEntitlement.objects.filter(status=InternetEntitlement.Status.ACTIVE, valid_until__lte=timezone.now()):
            expire_internet_entitlement(entitlement, effective_at=entitlement.valid_until)
            count += 1
        self.stdout.write(self.style.SUCCESS(f'Expired {count} Internet entitlement(s).'))
