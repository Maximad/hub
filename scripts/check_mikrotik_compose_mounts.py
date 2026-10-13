"""Check effective production Compose CA mounts without printing secrets.

Input is the JSON output of docker compose config --format json. This checker
never sends network requests or changes RouterOS settings.
"""
import json
from pathlib import Path
import sys


ROUTER_SERVICES = ('web', 'internet-worker', 'management-mcp')


def config_findings(config, *, is_file=None):
    is_file = is_file or (lambda path: Path(path).is_file())
    services = config.get('services') or {}
    errors = []
    for service_name in ROUTER_SERVICES:
        service = services.get(service_name)
        if not isinstance(service, dict):
            errors.append(f'{service_name}: service missing')
            continue
        environment = service.get('environment') or {}
        if not isinstance(environment, dict):
            errors.append(f'{service_name}: expected resolved environment')
            continue
        if str(environment.get('MIKROTIK_VERIFY_TLS', '')).lower() not in ('true', '1', 'yes', 'on'):
            errors.append(f'{service_name}: RouterOS TLS verification is not enabled')
        ca = environment.get('MIKROTIK_CA_FILE') or ''
        if not isinstance(ca, str) or not ca.startswith('/'):
            errors.append(f'{service_name}: RouterOS CA container path not configured')
            continue
        matching = [
            volume for volume in (service.get('volumes') or [])
            if isinstance(volume, dict) and volume.get('target') == ca
        ]
        if len(matching) != 1:
            errors.append(f'{service_name}: RouterOS CA file is not uniquely mounted')
            continue
        volume = matching[0]
        if volume.get('type') != 'bind' or volume.get('read_only') is not True:
            errors.append(f'{service_name}: CA mount must be a read-only bind')
            continue
        source = volume.get('source') or ''
        if not source or not is_file(source):
            errors.append(f'{service_name}: CA source file is missing')
    return errors


def main():
    try:
        config = json.load(sys.stdin)
        issues = config_findings(config)
    except (ValueError, TypeError, OSError):
        print('FAIL: could not validate effective Docker Compose CA configuration', file=sys.stderr)
        return 1
    if issues:
        for item in issues:
            print(f'FAIL: {item}', file=sys.stderr)
        return 1
    print('PASS: required Django services have read-only RouterOS CA mounts and TLS verification')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
