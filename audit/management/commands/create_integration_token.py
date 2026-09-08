from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from audit.integration_auth import ALL_SCOPES, generate_token_value, validate_scopes
from audit.models import IntegrationToken


class Command(BaseCommand):
    help = 'Create a scoped Hub management API bearer token and print the secret once.'

    def add_arguments(self, parser):
        parser.add_argument('--name', required=True)
        parser.add_argument('--scope', action='append', dest='scopes', required=True, choices=sorted(ALL_SCOPES))
        parser.add_argument('--expires-in-days', type=int, default=90)
        parser.add_argument('--created-by', default='')
        parser.add_argument('--notes', default='')

    def handle(self, *args, **options):
        days = options['expires_in_days']
        if days < 1 or days > 365:
            raise CommandError('--expires-in-days must be between 1 and 365.')

        try:
            scopes = validate_scopes(options['scopes'])
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        created_by = None
        username = options['created_by'].strip()
        if username:
            User = get_user_model()
            try:
                created_by = User.objects.get(username=username, is_active=True)
            except User.DoesNotExist as exc:
                raise CommandError(f'Active user not found: {username}') from exc

        for _attempt in range(10):
            prefix, digest, complete_token = generate_token_value()
            if not IntegrationToken.objects.filter(prefix=prefix).exists():
                break
        else:
            raise CommandError('Could not generate a unique token prefix.')

        record = IntegrationToken.objects.create(
            name=options['name'].strip(),
            prefix=prefix,
            secret_digest=digest,
            scopes=scopes,
            expires_at=timezone.now() + timedelta(days=days),
            created_by=created_by,
            notes=options['notes'].strip(),
        )

        self.stdout.write(self.style.SUCCESS(f'Created integration token #{record.pk}: {record.name}'))
        self.stdout.write(f'Prefix: {record.prefix}')
        self.stdout.write(f'Scopes: {", ".join(scopes)}')
        self.stdout.write(f'Expires: {record.expires_at.isoformat()}')
        self.stdout.write(self.style.WARNING('Store the following bearer token now. It will not be shown again:'))
        self.stdout.write(complete_token)
