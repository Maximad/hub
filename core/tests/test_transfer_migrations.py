import uuid
from datetime import date
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class TransferBigintMigrationTests(TransactionTestCase):
    """Exercise the production 0028 -> 0034 path on PostgreSQL without leaking schema state."""

    migrate_from = ('core', '0028_purchase_lifecycle')
    migrate_to = ('core', '0034_currency_safety')

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.latest_targets = self.executor.loader.graph.leaf_nodes()
        self.executor.migrate([self.migrate_from])

    def tearDown(self):
        # Migration tests mutate the shared test schema. Always restore every app
        # to the current project leaf nodes before Django continues with later tests.
        self.executor.loader.build_graph()
        self.executor.migrate(self.latest_targets)
        super().tearDown()

    def column_type(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT data_type
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'core_transfer'
                  AND column_name = 'id'
                """
            )
            return cursor.fetchone()[0]

    def test_0028_to_latest_preserves_bigint_transfer_and_relations(self):
        if connection.vendor != 'postgresql':
            self.skipTest('This production migration regression test requires PostgreSQL.')

        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        Account = old_apps.get_model('core', 'FinancialAccount')
        Transfer = old_apps.get_model('core', 'Transfer')
        source = Account.objects.create(
            code='migration-source', name_ar='مصدر', account_type='asset', is_active=True,
        )
        destination = Account.objects.create(
            code='migration-destination', name_ar='وجهة', account_type='asset', is_active=True,
        )
        transfer = Transfer.objects.create(
            source_account=source, destination_account=destination,
            amount=Decimal('25.00'), business_date=date(2026, 8, 7),
        )
        transfer_id = transfer.pk
        self.assertIsInstance(transfer_id, int)
        self.assertEqual(self.column_type(), 'bigint')

        self.executor.loader.build_graph()
        self.executor.migrate([self.migrate_to])
        new_apps = self.executor.loader.project_state([self.migrate_to]).apps
        Transfer = new_apps.get_model('core', 'Transfer')
        PostingBatch = new_apps.get_model('core', 'PostingBatch')
        CashMovement = new_apps.get_model('core', 'CashMovement')
        migrated = Transfer.objects.get(pk=transfer_id)

        posting_batch = PostingBatch.objects.create(
            id=uuid.uuid4(), operation_type='transfer.post', business_date=date(2026, 8, 7),
            idempotency_key='migration-post', status='draft',
        )
        reversal_batch = PostingBatch.objects.create(
            id=uuid.uuid4(), operation_type='transfer.reverse', business_date=date(2026, 8, 7),
            idempotency_key='migration-reverse', status='draft',
        )
        # The current constraint requires a transfer with a posting batch to be
        # in a posted/reversed state. Use a valid historical state while testing
        # only the bigint PK and relation compatibility of this migration path.
        migrated.state = 'reversed'
        migrated.posting_batch = posting_batch
        migrated.reversal_batch = reversal_batch
        migrated.save(update_fields=['state', 'posting_batch', 'reversal_batch'])
        movement = CashMovement.objects.create(
            business_date=date(2026, 8, 7), movement_type='cash_deposit', direction='in',
            amount_syp=Decimal('25.00'), title='migration projection', is_generated=True,
            financial_account=destination, transfer=migrated, transfer_leg='incoming',
        )

        self.assertEqual(self.column_type(), 'bigint')
        self.assertEqual(Transfer.objects.filter(pk=transfer_id).count(), 1)
        self.assertEqual(movement.transfer_id, transfer_id)
        self.assertEqual(migrated.posting_batch_id, posting_batch.pk)
        self.assertEqual(migrated.reversal_batch_id, reversal_batch.pk)
