import json
import signal
import threading
import time

from django.core.management.base import BaseCommand, CommandError

from core.services.internet_worker import run_internet_worker_cycle


class Command(BaseCommand):
    help = 'Run the production Internet lifecycle/network worker loop.'

    def add_arguments(self, parser):
        parser.add_argument('--network-interval', type=float, default=5.0)
        parser.add_argument('--lifecycle-interval', type=float, default=60.0)
        parser.add_argument('--network-limit', type=int, default=25)
        parser.add_argument('--lifecycle-limit', type=int, default=200)
        parser.add_argument('--once', action='store_true')

    def _validate(self, options):
        if not 1 <= options['network_limit'] <= 1000:
            raise CommandError('--network-limit must be between 1 and 1000')
        if not 1 <= options['lifecycle_limit'] <= 1000:
            raise CommandError('--lifecycle-limit must be between 1 and 1000')
        if not 1 <= options['network_interval'] <= 300:
            raise CommandError('--network-interval must be between 1 and 300 seconds')
        if not 5 <= options['lifecycle_interval'] <= 3600:
            raise CommandError('--lifecycle-interval must be between 5 and 3600 seconds')

    def _write_cycle(self, summary, errors, *, lifecycle_ran):
        network_activity = (
            summary['entitlement_network']['processed']
            or summary['session_network']['processed']
        )
        if lifecycle_ran or network_activity or errors:
            payload = {'worker': 'internet', **summary}
            if errors:
                payload['errors'] = [
                    {'component': component, 'error': error}
                    for component, error in errors
                ]
            self.stdout.write(json.dumps(payload, separators=(',', ':')))

    def handle(self, *args, **options):
        self._validate(options)
        stop_event = threading.Event()

        def request_stop(signum, frame):  # pragma: no cover - signal delivery is integration behavior
            stop_event.set()

        if not options['once'] and threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                signal.signal(signum, request_stop)

        network_interval = options['network_interval']
        lifecycle_interval = options['lifecycle_interval']
        next_lifecycle_at = 0.0

        while not stop_event.is_set():
            now = time.monotonic()
            run_lifecycle = now >= next_lifecycle_at
            summary, errors = run_internet_worker_cycle(
                lifecycle_limit=options['lifecycle_limit'],
                network_limit=options['network_limit'],
                run_lifecycle=run_lifecycle,
            )
            self._write_cycle(summary, errors, lifecycle_ran=run_lifecycle)

            if run_lifecycle:
                next_lifecycle_at = now + lifecycle_interval

            if options['once']:
                if errors:
                    raise CommandError('Internet worker cycle completed with infrastructure errors.')
                return

            stop_event.wait(network_interval)
