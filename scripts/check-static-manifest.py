"""Fail before traffic cutover if the image has an incomplete static manifest."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent / 'staticfiles'
manifest = json.loads((root / 'staticfiles.json').read_text())
paths = manifest.get('paths', {})
if not paths or any(not (root / value).is_file() for value in paths.values()):
    raise SystemExit('Release static manifest is empty or incomplete')
print(f'PASS: {len(paths)} immutable static assets in release image')
