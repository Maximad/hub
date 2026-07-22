from django.core.management.base import BaseCommand
from catalog.models import MediaAsset


class Command(BaseCommand):
    help = 'Generate cached product card thumbnails without modifying original media.'

    def handle(self, *args, **options):
        scanned = generated = skipped = failed = 0
        for asset in MediaAsset.objects.filter(is_active=True, media_type__in=[MediaAsset.MediaType.IMAGE, MediaAsset.MediaType.GIF]).exclude(file=''):
            scanned += 1
            for width in (320, 640):
                try:
                    name = asset._thumbnail_storage_name(width)
                    if asset.file.storage.exists(name):
                        skipped += 1
                    elif asset.generate_thumbnail(width=width):
                        generated += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
        self.stdout.write(self.style.SUCCESS(f'scanned={scanned} generated={generated} skipped={skipped} failed={failed}'))
