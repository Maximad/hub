import json
import signal
import threading

from django.core.management.base import BaseCommand, CommandError

from core.services.notification_delivery import run_notification_worker_cycle


class Command(BaseCommand):
    help = 'Run the durable background staff push-notification worker.'

    def add_arguments(self, parser):
        parser.add_argument('--interval', type=float, default=5.0)
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--once', action='store_true')

    def _validate(self, options):
        if not 1 <= options['limit'] <= 500:
            raise CommandError('--limit must be between 1 and 500')
        if not 1 <= options['interval'] <= 300:
            raise CommandError('--interval must be between 1 and 300 seconds')

    def _write_cycle(self, summary, errors, *, force=False):
        activity = any(summary[key] for key in ('claimed', 'sent', 'retried', 'failed', 'skipped'))
        if force or activity or errors:
            payload = {'worker': 'notifications', **summary}
            if errors:
                payload['errors'] = [
                    {'component': component, 'error': error}
                    for component, error in errors
                ]
            self.stdout.write(json.dumps(payload, separators=(',', ':')))

    def handle(self, *args, **options):
        self._validate(options)
        stop_event = threading.Event()

        def request_stop(signum, frame):  # pragma: no cover - integration signal behavior
            stop_event.set()

        if not options['once'] and threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                signal.signal(signum, request_stop)

        while not stop_event.is_set():
            summary, errors = run_notification_worker_cycle(limit=options['limit'])
            self._write_cycle(summary, errors, force=options['once'])

            if options['once']:
                if errors:
                    raise CommandError('Notification worker cycle completed with infrastructure errors.')
                return

            stop_event.wait(options['interval'])
