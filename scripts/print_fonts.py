"""Pinned, locally bundled fonts shared by the print and phone-image exports."""
import hashlib
import json
from pathlib import Path


FAMILY = '"OMT Music", "OMT Hebrew", "DejaVu Sans", sans-serif'


def font_css(root):
    directory = Path(root).resolve() / 'assets/print-fonts'
    manifest = json.loads((directory / 'manifest.json').read_text())
    for item in manifest['files']:
        path = directory / item['file']
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Print font checksum mismatch: ' + item['file'])
    hebrew = (directory / 'NotoSansHebrew.ttf').as_uri()
    music = (directory / 'NotoMusic-Regular.ttf').as_uri()
    # Restrict the first family to notation, leaving Hebrew/Latin to the text face.
    # Both exports embed the same bytes; no network or host font discovery is needed.
    return f'''
@font-face {{ font-family: "OMT Hebrew"; src: url("{hebrew}"); font-weight: 100 900; font-style: normal; }}
@font-face {{ font-family: "OMT Music"; src: url("{music}"); font-weight: 100 900; font-style: normal; unicode-range: U+2669-266F, U+1D100-1D1FF; }}
body {{ font-family: {FAMILY}; }}
'''
