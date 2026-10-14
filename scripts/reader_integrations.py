"""Reviewed reading additions; approved narration source files stay unchanged."""
from pathlib import Path
import hashlib
import json

DIRECTORY = 'reading/v2-integrations'

def is_integration(path, root):
    return Path(path).resolve().is_relative_to((Path(root) / DIRECTORY).resolve())

def integrated_body(root, path, body):
    root = Path(root).resolve()
    path = Path(path).resolve()
    manifest = root / DIRECTORY / 'manifest.json'
    if not manifest.exists():
        return body
    entries = json.loads(manifest.read_text(encoding='utf-8'))
    if entries.get('schema_version') != 1:
        raise ValueError('Unsupported reading integration schema')
    additions = []
    seen = set()
    for entry in entries['chapters']:
        target = (root / entry['target']).resolve()
        extra = (root / DIRECTORY / entry['addition']).resolve()
        if not target.is_relative_to(root) or not is_integration(extra, root):
            raise ValueError('Reading integration path outside its directory')
        if target in seen:
            raise ValueError('Duplicate reading integration target')
        seen.add(target)
        if target != path:
            continue
        if entry.get('review_status') != 'beta-text-reviewed':
            raise ValueError('Unreviewed reading integration: ' + entry['target'])
        if hashlib.sha256(target.read_bytes()).hexdigest() != entry['base_sha256']:
            raise ValueError('Reading base changed; review integration: ' + entry['target'])
        additions.append(extra.read_text(encoding='utf-8').strip())
    if not additions:
        return body
    notice = ('> **הרחבה מגרסה 2 — בטא:** ההסברים והוראות דפי העבודה שלהלן '
              'נבדקו מול המקור. דוגמאות התווים בדפי העבודה נשמרו במקורן; '
              'כותרותיהם באנגלית. קישורי השמע ושירותי החוץ טרם אומתו במלואם. '
              'הרחבה זו מיועדת למהדורת הקריאה וטרם נכללת בספר השמע.\n\n')
    return body.rstrip() + '\n\n---\n\n' + notice + '\n\n'.join(additions) + '\n'
