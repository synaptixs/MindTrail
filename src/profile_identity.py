"""Portable hash seed for immutable discovery scopes; never used as a file path."""
import json
from pathlib import Path

NAME = 'profile-origin.json'


def validate(value):
    if (not isinstance(value, dict) or set(value) != {'version', 'scopeSeed'} or value['version'] != 1
            or not isinstance(value['scopeSeed'], str) or not 1 <= len(value['scopeSeed']) <= 4096
            or '\0' in value['scopeSeed']):
        raise ValueError('Invalid workspace origin metadata')
    return value


def origin(profile, database):
    path = Path(profile) / NAME
    if path.is_symlink():
        raise ValueError('Invalid workspace origin location')
    if not path.exists():
        return {'version': 1, 'scopeSeed': str(Path(database).resolve())}
    if path.stat().st_size > 20_000:
        raise ValueError('Invalid workspace origin metadata')
    return validate(json.loads(path.read_text(encoding='utf-8')))
