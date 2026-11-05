"""Pinned, locally bundled fonts shared by the print and phone-image exports."""
import hashlib
import json
import re
from pathlib import Path
from bs4 import NavigableString


FAMILY = '"OMT Music", "OMT Hebrew", "DejaVu Sans", sans-serif'
NOTE_SPELLING = re.compile(
    r'(?<![A-Za-z])([A-G][♯♭♮𝄪𝄫]*(?:[ \t]*[–—−/-][ \t]*[A-G][♯♭♮𝄪𝄫]*)+'
    r'|[A-G][♯♭♮𝄪𝄫]+)(?![A-Za-z])')


def isolate_note_spellings(soup):
    """Keep note letters with their accidentals in LTR order, without changing text."""
    for text in list(soup.find_all(string=NOTE_SPELLING)):
        if any(parent.name in {'style', 'script', 'svg', 'math', 'bdi'}
               or parent.get('dir') == 'ltr' for parent in text.parents):
            continue
        parts = NOTE_SPELLING.split(str(text))
        for index, part in enumerate(parts):
            if index % 2:
                node = soup.new_tag('bdi', dir='ltr')
                node.string = part
                text.insert_before(node)
            elif part:
                text.insert_before(NavigableString(part))
        text.extract()


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
bdi {{ direction: ltr; unicode-bidi: isolate; white-space: nowrap; }}
'''
