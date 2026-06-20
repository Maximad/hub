from django.core.management.base import BaseCommand
from django.test import Client, override_settings

ROUTES = [
    ('PUBLIC', '/menu/'), ('PUBLIC', '/admin/login/'),
    ('STAFF', '/staff/'), ('STAFF', '/staff/orders/'), ('STAFF', '/staff/cashier/'),
    ('STAFF', '/staff/pos/'), ('STAFF', '/staff/reports/'), ('STAFF', '/staff/close-day/'),
    ('STAFF', '/staff/menu-tools/'), ('STAFF', '/staff/notifications/'), ('STAFF', '/staff/prep/'),
    ('STAFF', '/staff/prep/kitchen/'), ('STAFF', '/staff/prep/bar/'), ('STAFF', '/staff/finance/'),
    ('STAFF', '/staff/inventory/'),
]

class Command(BaseCommand):
    help = 'Smoke check public and staff routes without modifying data.'

    def handle(self, *args, **options):
        client = Client()
        failures = 0
        warnings = 0
        self.stdout.write('Hub smoke check')
        with override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'], DEBUG_PROPAGATE_EXCEPTIONS=True):
            for scope, route in ROUTES:
                try:
                    response = client.get(route)
                    status = response.status_code
                    if status >= 500:
                        label = 'FAIL'; failures += 1; error = 'server error'
                    elif status == 404:
                        label = 'WARN'; warnings += 1; error = 'unexpected 404'
                    elif status in {200, 302, 301, 403}:
                        label = 'PASS'; error = ''
                    else:
                        label = 'WARN'; warnings += 1; error = f'unexpected status {status}'
                except Exception as exc:
                    label = 'FAIL'; status = 'ERROR'; error = f'{exc.__class__.__name__}: {exc}'; failures += 1
                self.stdout.write(f'{label:4} {scope:6} {route:32} {status} {error}'.rstrip())
        self.stdout.write(f'Summary: PASS={len(ROUTES)-failures-warnings} WARN={warnings} FAIL={failures}')
        if failures:
            raise SystemExit(1)
