from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from core.services.internet_worker import run_internet_worker_cycle
from internet.models import InternetOperationsState


class InternetWorkerCycleTests(SimpleTestCase):
    @patch('core.services.internet_worker.close_old_connections')
    @patch('core.services.internet_worker.process_ready_session_network_operations')
    @patch('core.services.internet_worker.process_ready_network_operations')
    @patch('core.services.internet_worker.call_command')
    def test_cycle_runs_lifecycle_and_both_durable_queues(
        self, lifecycle, entitlement_jobs, session_jobs, close_connections,
    ):
        entitlement_jobs.return_value = (3, 2)
        session_jobs.return_value = (4, 4)

        with patch('core.services.internet_worker._record_heartbeat') as heartbeat:
            summary, errors = run_internet_worker_cycle(
                lifecycle_limit=25,
                network_limit=10,
                run_lifecycle=True,
            )

        lifecycle.assert_called_once()
        self.assertEqual(lifecycle.call_args.args[0], 'reconcile_internet_lifecycle')
        self.assertEqual(lifecycle.call_args.kwargs['limit'], 25)
        entitlement_jobs.assert_called_once_with(limit=10)
        session_jobs.assert_called_once_with(limit=10)
        heartbeat.assert_called_once()
        self.assertEqual(summary['lifecycle'], 'ok')
        self.assertEqual(summary['entitlement_network']['failed'], 1)
        self.assertEqual(summary['session_network']['failed'], 0)
        self.assertEqual(errors, [])
        self.assertEqual(close_connections.call_count, 2)

    @patch('core.services.internet_worker.close_old_connections')
    @patch('core.services.internet_worker.process_ready_session_network_operations')
    @patch('core.services.internet_worker.process_ready_network_operations')
    @patch('core.services.internet_worker.call_command')
    def test_component_failure_does_not_block_other_queues_or_leak_secrets(
        self, lifecycle, entitlement_jobs, session_jobs, close_connections,
    ):
        lifecycle.side_effect = RuntimeError('password=supersecret')
        entitlement_jobs.side_effect = RuntimeError('temporary entitlement queue failure')
        session_jobs.return_value = (2, 2)

        with patch('core.services.internet_worker._record_heartbeat'):
            summary, errors = run_internet_worker_cycle(run_lifecycle=True)

        self.assertEqual(summary['lifecycle'], 'error')
        session_jobs.assert_called_once_with(limit=100)
        self.assertEqual(summary['session_network']['processed'], 2)
        self.assertEqual([component for component, _ in errors], [
            'lifecycle', 'entitlement_network',
        ])
        self.assertNotIn('supersecret', str(errors))
        self.assertIn('sensitive details were removed', errors[0][1])

    @patch('core.services.internet_worker.close_old_connections')
    @patch('core.services.internet_worker.process_ready_session_network_operations', return_value=(0, 0))
    @patch('core.services.internet_worker.process_ready_network_operations', return_value=(0, 0))
    @patch('core.services.internet_worker.call_command')
    def test_network_only_cycle_skips_lifecycle(
        self, lifecycle, entitlement_jobs, session_jobs, close_connections,
    ):
        with patch('core.services.internet_worker._record_heartbeat'):
            summary, errors = run_internet_worker_cycle(run_lifecycle=False)

        lifecycle.assert_not_called()
        self.assertEqual(summary['lifecycle'], 'skipped')
        self.assertEqual(errors, [])


class InternetWorkerHeartbeatTests(TestCase):
    @patch('core.services.internet_worker.close_old_connections')
    @patch('core.services.internet_worker.process_ready_session_network_operations', return_value=(0, 0))
    @patch('core.services.internet_worker.process_ready_network_operations', return_value=(0, 0))
    @patch('core.services.internet_worker.call_command')
    def test_cycle_persists_secret_free_heartbeat(
        self, lifecycle, entitlement_jobs, session_jobs, close_connections,
    ):
        summary, errors = run_internet_worker_cycle(run_lifecycle=True)

        self.assertEqual(errors, [])
        state = InternetOperationsState.objects.get(key='default')
        self.assertIsNotNone(state.last_worker_seen_at)
        self.assertIsNotNone(state.last_lifecycle_at)
        self.assertEqual(state.last_worker_summary, summary)
        self.assertEqual(state.last_worker_error, '')


class InternetWorkerCommandTests(SimpleTestCase):
    @patch('core.management.commands.run_internet_worker.run_internet_worker_cycle')
    def test_once_runs_one_cycle_and_exits(self, cycle):
        cycle.return_value = ({
            'lifecycle': 'ok',
            'entitlement_network': {'processed': 0, 'succeeded': 0, 'failed': 0},
            'session_network': {'processed': 0, 'succeeded': 0, 'failed': 0},
        }, [])
        stdout = StringIO()

        call_command(
            'run_internet_worker',
            once=True,
            network_interval=1,
            lifecycle_interval=5,
            network_limit=10,
            lifecycle_limit=20,
            stdout=stdout,
        )

        cycle.assert_called_once_with(
            lifecycle_limit=20,
            network_limit=10,
            run_lifecycle=True,
        )
        self.assertIn('"worker":"internet"', stdout.getvalue())

    @patch('core.management.commands.run_internet_worker.run_internet_worker_cycle')
    def test_once_returns_nonzero_for_infrastructure_error(self, cycle):
        cycle.return_value = ({
            'lifecycle': 'error',
            'entitlement_network': {'processed': 0, 'succeeded': 0, 'failed': 0},
            'session_network': {'processed': 0, 'succeeded': 0, 'failed': 0},
        }, [('lifecycle', 'RuntimeError: unavailable')])

        with self.assertRaises(CommandError):
            call_command(
                'run_internet_worker',
                once=True,
                network_interval=1,
                lifecycle_interval=5,
            )

    def test_rejects_unsafe_poll_intervals(self):
        with self.assertRaises(CommandError):
            call_command('run_internet_worker', once=True, network_interval=0)
        with self.assertRaises(CommandError):
            call_command('run_internet_worker', once=True, lifecycle_interval=1)


class ProductionComposeWorkerTests(SimpleTestCase):
    def test_production_compose_has_private_restartable_worker(self):
        compose = (Path(settings.BASE_DIR) / 'docker-compose.prod.yml').read_text()
        self.assertIn('  internet-worker:', compose)
        worker = compose.split('  internet-worker:', 1)[1].split('\nvolumes:', 1)[0]

        self.assertIn('container_name: hub-internet-worker', worker)
        self.assertIn('restart: unless-stopped', worker)
        self.assertIn('python manage.py run_internet_worker', worker)
        self.assertIn('--network-interval 5', worker)
        self.assertIn('--lifecycle-interval 60', worker)
        self.assertIn('--network-limit 25', worker)
        self.assertIn('--lifecycle-limit 200', worker)
        self.assertIn('env_file:', worker)
        self.assertIn('- db', worker)
        self.assertNotIn('ports:', worker)
        self.assertNotIn('traefik.', worker)
