from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Deprecated wrapper: delegates to bootstrap_masharib for Phase 1.5+ safe seed data.'

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.WARNING(
                'seed_demo_ar is deprecated; delegating to bootstrap_masharib for Phase 1.5+ compatible data.'
            )
        )
        call_command('bootstrap_masharib')
        self.stdout.write(self.style.SUCCESS('Bootstrap completed via seed_demo_ar wrapper.'))
