#!/usr/bin/env python3
"""Independent, manual-only v3 narration; preparation never authorizes production.

No legacy narration/selection/state files are read. Provider responses and secrets
are never persisted or printed. Every production input is content addressed.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import wave
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
BRANCH = 'audiobook-v3-data'
MODEL = 'gemini-3.8-flash-tts'
ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/interactions'
STATE_DIRS = ('chapters', 'segments', 'opening', 'transcripts', 'plans')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)


def save_json(path, data):
    atomic(path, (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode())


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def log(event, **fields):
    # Callers supply only numeric values, fixed status tokens, and chapter IDs.
    print(json.dumps({'event': event, **fields}, ensure_ascii=False), flush=True)


class GateError(ValueError):
    pass


class Paused(Exception):
    pass


def contained(root, relative):
    """All manifest paths are relative; reject traversal and symlink escapes."""
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise GateError('Manifest paths must be nonempty relative paths.')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise GateError('Manifest path escapes its input directory.')
    return path


def checked_bytes(path, expected):
    data = path.read_bytes()
    if not isinstance(expected, str) or digest(data) != expected:
        raise GateError('An input checksum no longer matches its reviewed manifest.')
    return data


def split_text(text, limit=1200):
    chunks = []
    while len(text) > limit:
        boundary = text.rfind('\n', 0, limit + 1)
        if boundary < limit // 3:
            boundary = text.rfind(' ', 0, limit + 1)
        boundary = limit if boundary < 1 else boundary + 1
        chunks.append(text[:boundary])
        text = text[boundary:]
    if text:
        chunks.append(text)
    return chunks


def load_plan(inputs, source_root, selected=None, generate=False):
    manifest = json.loads((inputs / 'manifest.json').read_text())
    selection = json.loads((inputs / 'selection.json').read_text())
    if manifest.get('edition') != 3 or selection.get('edition') != 3:
        raise GateError('Both manifest and selection must explicitly select edition 3.')
    policy_bytes = checked_bytes(inputs / 'policy.md', manifest.get('policy_sha256'))
    if selection.get('policy_sha256') != digest(policy_bytes):
        raise GateError('Selection and manifest refer to different policies.')
    if selection.get('model') != MODEL:
        raise GateError('Unsupported model; changing models requires a separate approval.')
    entries = manifest.get('chapters', [])
    numbers = [c.get('chapter') for c in entries]
    if not entries or any(type(n) is not int or n < 1 for n in numbers) or len(numbers) != len(set(numbers)):
        raise GateError('Chapter IDs must be positive, distinct and present.')
    if selected is not None and not selected <= set(numbers):
        raise GateError('A requested chapter is absent from the manifest.')
    blockers = set()
    if manifest.get('production_enabled') is not True:
        blockers.add('manifest_production_not_enabled')
    if selection.get('production_enabled') is not True:
        blockers.add('production_not_enabled')
    sample = selection.get('sample_approval', {})
    identity = {'model': selection.get('model'), 'voice': selection.get('voice'), 'style': selection.get('style'),
                'opening_style': selection.get('opening', {}).get('style')}
    if not selection.get('voice') or not selection.get('style') or sample.get('approved') is not True or sample.get('identity_sha256') != digest(canonical(identity)) or not sample.get('evidence'):
        blockers.add('v3_voice_style_sample_approval_missing')
    opening = selection.get('opening', {})
    opening_text = opening.get('text', '')
    if not isinstance(opening_text, str) or not opening_text.strip() or not opening.get('style') or opening.get('approved') is not True or opening.get('text_sha256') != digest(opening_text.encode()) or not opening.get('approval_evidence'):
        blockers.add('v3_user_opening_wording_approval_missing')
    if opening.get('raw_file'):
        original = contained(inputs, opening['raw_file'])
        raw = checked_bytes(original, opening.get('raw_sha256'))
        receipt = json.loads(contained(inputs, opening['raw_metadata']).read_text())
        if (receipt.get('raw_wav_sha256') != digest(raw) or receipt.get('voice') != selection.get('voice')
                or receipt.get('model') != MODEL or receipt.get('text') != opening_text
                or receipt.get('style') != opening.get('style')):
            raise GateError('Approved opening recording does not match its audition metadata.')
        with wave.open(io.BytesIO(raw), 'rb') as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 24000):
                raise GateError('Approved opening WAV has an unexpected format.')
        opening['_approved_raw_path'] = str(original)
    chapters = []
    for entry in entries:
        if selected is not None and entry['chapter'] not in selected:
            continue
        metadata_bytes = checked_bytes(contained(inputs, entry['json']), entry.get('metadata_sha256'))
        data = json.loads(metadata_bytes)
        text = checked_bytes(contained(inputs, entry['txt']), entry.get('transcript_sha256')).decode()
        if data.get('edition') != 3 or data.get('chapter') != entry['chapter'] or data.get('policy_sha256') != digest(policy_bytes):
            raise GateError('Chapter identity or policy mismatch.')
        if data.get('source_sha256') != entry.get('source_sha256') or data.get('integrated_reading_sha256') != entry.get('integrated_reading_sha256'):
            raise GateError('Chapter source identity differs from the manifest.')
        checked_bytes(contained(source_root, data['source']), data['source_sha256'])
        checked_bytes(contained(inputs, data['source_snapshot']), data['integrated_reading_sha256'])
        # An independent integrated-body check detects changed reading overlays too.
        from reader_integrations import integrated_body
        raw = contained(source_root, data['source']).read_text()
        body = raw.split('---', 2)[2] if raw.startswith('---\n') else raw
        if digest(integrated_body(source_root, contained(source_root, data['source']), body).encode()) != data['integrated_reading_sha256']:
            raise GateError('The integrated reading source changed after review.')
        expected_text = data['title'] + '\n\n' + '\n\n'.join(b['spoken'] for b in data['blocks'] if b.get('spoken')) + '\n'
        if text != expected_text:
            raise GateError('Transcript is not the exact chapter title and reviewed block sequence.')
        review = data.get('review', {})
        ready = (entry.get('status') == 'text_checked' and data.get('status') == 'text_checked'
                 and not review.get('pending')
                 and review.get('visual_review_complete') is True
                 and review.get('prose_sequence_preserved') is True
                 and review.get('allowed_transformations_audited') is True
                 and bool(review.get('text_check_evidence')))
        if not ready:
            blockers.add('chapter_' + str(entry['chapter']) + '_text_review_incomplete')
        if data.get('production_enabled') is not True:
            blockers.add('chapter_' + str(entry['chapter']) + '_production_not_enabled')
        chunks = split_text(text)
        if ''.join(chunks) != text:
            raise GateError('Chunking changed transcript content.')
        identity = {'edition': 3, 'builder': 1, 'policy_sha256': digest(policy_bytes),
                    'source_sha256': data['source_sha256'], 'integrated_reading_sha256': data['integrated_reading_sha256'],
                    'transcript_sha256': entry['transcript_sha256'], 'model': MODEL,
                    'voice': selection.get('voice'), 'style': selection.get('style'),
                    'opening_text_sha256': opening.get('text_sha256'), 'opening_style': opening.get('style')}
        identity['opening_raw_sha256'] = opening.get('raw_sha256')
        chapters.append({'chapter': entry['chapter'], 'title': data['title'], 'transcript': text,
                         'source': data['source'], 'source_sha256': data['source_sha256'],
                         'identity': identity, 'chunks': chunks,
                         'id': str(entry['chapter']) + '-' + digest(canonical(identity))[:20], 'text_checked': ready})
    if not chapters:
        raise GateError('No chapters selected.')
    if generate and blockers:
        raise GateError('Production blocked: ' + ', '.join(sorted(blockers)))
    return chapters, selection, sorted(blockers)


def load_keys(environment):
    # Explicit precedence prevents combining duplicate credentials from old vars.
    raw = environment.get('GEMINI_API_KAYS', '')
    candidates = raw.splitlines() if raw.strip() else [environment.get('GEMINI_API_KEY_' + str(n), '') for n in range(1, 4)]
    if not any(k.strip() for k in candidates):
        candidates = [environment.get('GEMINI_API_KEY', '')]
    keys = list(dict.fromkeys(k.strip() for k in candidates if k.strip()))
    if not keys or any(any(ch.isspace() for ch in key) for key in keys):
        raise GateError('At least one valid runtime API secret is required.')
    return keys


class KeyPool:
    """Conservative quota management: unknown keys share one quota group.

    Rotating keys never creates independent project quota. After a 429 this run
    pauses durably, rather than repeatedly trying other keys of the same project.
    Authentication failures may move to another configured credential.
    """
    def __init__(self, keys, deadline, opener=urlopen, clock=time.monotonic, sleeper=time.sleep):
        self.keys = keys
        self.deadline, self.opener, self.clock, self.sleep = deadline, opener, clock, sleeper
        self.index, self.attempts, self.successes = 0, 0, 0
        self.disabled = set()

    def request(self, request_payload):
        transient = 0
        while True:
            if self.clock() >= self.deadline:
                raise Paused('time_budget_reached')
            usable = [i for i in range(len(self.keys)) if i not in self.disabled]
            if not usable:
                raise GateError('All configured credentials were rejected.')
            if self.index not in usable:
                self.index = usable[0]
            index = self.index
            self.attempts += 1
            req = Request(ENDPOINT, data=json.dumps(request_payload).encode(), method='POST',
                          headers={'Content-Type': 'application/json', 'x-goog-api-key': self.keys[index]})
            try:
                with self.opener(req, timeout=min(300, max(1, self.deadline - self.clock()))) as response:
                    status, raw = response.status, response.read()
            except HTTPError as error:
                status, raw = error.code, b''
                error.close()  # Never read, log or persist provider error bodies.
            except (URLError, TimeoutError, OSError):
                status, raw = 0, b''
            if status == 200:
                self.successes += 1
                self.index = (index + 1) % len(self.keys)
                return json.loads(raw)
            log('request_status', http_status=status, key_number=index + 1)
            if status == 429:
                raise Paused('project_quota_limited')
            if status in (401, 403):
                self.disabled.add(index)
                continue
            if status not in (0, 500, 502, 503, 504):
                raise GateError('Non-retryable provider HTTP status ' + str(status))
            transient += 1
            if transient > 6:
                raise Paused('provider_temporarily_unavailable')
            wait = min(10 * 2 ** (transient - 1), 300)
            if self.clock() + wait >= self.deadline:
                raise Paused('time_budget_reached')
            self.sleep(wait)


def payload(text, selection, opening=False):
    style = selection['opening'].get('style', selection['style']) if opening else selection['style']
    return {'model': MODEL, 'input': [{'type': 'user_input', 'content': [{'type': 'text', 'text': text,
            'annotations': [{'type': 'speech_metadata', 'style': style}]}]}],
            'response_format': {'type': 'audio', 'mime_type': 'audio/wav'},
            'generation_config': {'speech_config': [{'voice': selection['voice']}]}, 'store': False}


def decode_audio(data):
    if data.get('status') not in ('completed', None):
        raise GateError('Audio response is incomplete.')
    audio = [c for step in data.get('steps', []) if step.get('type') == 'model_output'
             for c in step.get('content', []) if c.get('type') == 'audio']
    if len(audio) != 1:
        raise GateError('Expected exactly one audio response.')
    raw = base64.b64decode(audio[0]['data'], validate=True)
    with wave.open(io.BytesIO(raw), 'rb') as wav:
        count = wav.getnframes()
        frames = wav.readframes(count)
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 24000) or len(frames) != count * 2 or count < 12000 or not any(frames):
            raise GateError('Audio format, duration or completeness validation failed.')
    return raw, count / 24000


def ffmpeg(args):
    result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', *args], capture_output=True)
    if result.returncode:
        raise GateError('Audio encoding/decoding validation failed.')


def git(state, *arguments, acceptable=(0,)):
    result = subprocess.run(['git', '-C', str(state), *arguments], capture_output=True)
    if result.returncode not in acceptable:
        raise GateError('State checkpoint git operation failed; local recovery retained.')
    return result


def checkpoint(state, message):
    if os.environ.get('AUDIOBOOK_V3_COMMIT_CHECKPOINT') != 'true':
        return
    # Refuse to stage source-repository files or a legacy branch by mistake.
    if git(state, 'rev-parse', '--show-toplevel').stdout.decode().strip() != str(state.resolve()):
        raise GateError('State must be a dedicated worktree.')
    git(state, 'add', '--all', '--', *[p for p in (*STATE_DIRS, 'progress.json', '.gitignore') if (state / p).exists()])
    changed = git(state, 'diff', '--cached', '--quiet', acceptable=(0, 1)).returncode
    if changed:
        git(state, 'commit', '-m', message)
    for attempt in range(3):
        remote = git(state, 'ls-remote', '--exit-code', '--heads', 'origin', BRANCH, acceptable=(0, 2))
        if remote.returncode == 0:
            git(state, 'fetch', '--no-tags', 'origin', BRANCH)
            # Unrelated source/master updates do not enter this orphan branch.
            # Conflicting state edits stop safely; do not force or overwrite them.
            result = subprocess.run(['git', '-C', str(state), 'rebase', 'FETCH_HEAD'], capture_output=True)
            if result.returncode:
                subprocess.run(['git', '-C', str(state), 'rebase', '--abort'], capture_output=True)
                raise GateError('Concurrent state conflict; recovery retained without overwrite.')
        pushed = subprocess.run(['git', '-C', str(state), 'push', 'origin', 'HEAD:refs/heads/' + BRANCH], capture_output=True)
        if pushed.returncode == 0:
            return
    raise GateError('State push failed after fresh fetch/rebase; recovery retained.')


def record_segment(folder, text, identity, selection, pool, opening=False):
    folder.mkdir(parents=True, exist_ok=True)
    audio = folder / 'audio.mp3'
    receipt = folder / 'receipt.json'
    expected = {'text_sha256': digest(text.encode()), 'identity_sha256': digest(canonical(identity))}
    if audio.exists() or receipt.exists():
        if not audio.exists() or not receipt.exists():
            raise GateError('Incomplete existing segment; explicit recovery required.')
        saved = json.loads(receipt.read_text())
        if any(saved.get(k) != v for k, v in expected.items()) or saved.get('sha256') != digest(audio.read_bytes()):
            raise GateError('Existing audio segment identity/checksum mismatch.')
        return audio
    wav, raw_receipt, temporary = folder / 'raw.wav', folder / 'raw-metadata.json', folder / 'working.mp3'
    if wav.exists() or raw_receipt.exists():
        if not wav.exists() or not raw_receipt.exists():
            raise GateError('Incomplete original WAV receipt; explicit recovery required.')
        saved = json.loads(raw_receipt.read_text())
        raw = wav.read_bytes()
        if any(saved.get(k) != v for k, v in expected.items()) or saved.get('raw_sha256') != digest(raw):
            raise GateError('Original WAV identity/checksum mismatch; preserved without overwrite.')
        seconds = saved['seconds']
    else:
        preset = selection['opening'].get('_approved_raw_path') if opening else None
        if preset:
            raw = checked_bytes(Path(preset), selection['opening']['raw_sha256'])
            with wave.open(io.BytesIO(raw), 'rb') as w: seconds = w.getnframes()/w.getframerate()
        else:
            raw, seconds = decode_audio(pool.request(payload(text, selection, opening)))
        atomic(wav, raw)
        save_json(raw_receipt, {**expected, 'raw_sha256': digest(raw), 'seconds': seconds,
            'edition': 3, 'model': MODEL, 'voice': selection['voice'], 'text': text,
            'style': selection['opening']['style'] if opening else selection['style'],
            'recorded_utc': datetime.now(timezone.utc).isoformat(),
            'source': 'approved-v3-audition-opening' if preset else 'provider-original',
            'status': 'original-preserved-before-encoding'})
        atomic(folder / 'transcript.txt', text.encode())
    ffmpeg(['-i', str(wav), '-ac', '1', '-ar', '24000', '-c:a', 'libmp3lame', '-b:a', '96k', str(temporary)])
    ffmpeg(['-i', str(temporary), '-f', 'null', '-'])
    temporary.replace(audio)
    save_json(receipt, {**expected, 'sha256': digest(audio.read_bytes()), 'seconds': seconds,
                        'edition': 3, 'model': MODEL, 'voice': selection['voice'], 'status': 'recorded-needs-listening-review'})
    return audio


def tag_chapter(path, chapter, selection, opening_text):
    from mutagen.id3 import ID3, TIT2, TALB, TPE1, TPE2, TRCK, TLAN, TCOP, APIC, USLT, TXXX
    tags = ID3()
    tags.add(TIT2(encoding=3, text=chapter['title']))
    tags.add(TRCK(encoding=3, text=str(chapter['chapter'])))
    tags.add(TALB(encoding=3, text='תאוריית המוזיקה הפתוחה — מהדורת שמע 3'))
    tags.add(TPE1(encoding=3, text=['Kris Shaffer', 'Bryn Hughes', 'Brian Moseley']))
    tags.add(TPE2(encoding=3, text='Open Music Theory בעברית'))
    tags.add(TLAN(encoding=3, text='heb'))
    tags.add(TCOP(encoding=3, text='CC BY-SA 4.0; see individual source credits'))
    tags.add(USLT(encoding=3, lang='heb', desc='מלל הקריינות בפועל', text=opening_text + '\n\n' + chapter['transcript']))
    for key, value in [('Edition', '3'), ('Voice', selection['voice']), ('Model', MODEL), ('Review', 'Recorded; listening review pending'), ('Input identity', digest(canonical(chapter['identity'])))]:
        tags.add(TXXX(encoding=3, desc=key, text=value))
    cover = ROOT / 'images/gramophone.jpg'
    if cover.exists():
        tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='עטיפת הספר', data=cover.read_bytes()))
    else:
        raise GateError('Required album cover is missing.')
    tags.save(path, v2_version=3)


def generate(chapters, selection, state, budget):
    if state.resolve() == ROOT.resolve() or state.name != 'audiobook-v3-state':
        raise GateError('Generation requires the separate audiobook-v3-state directory.')
    state.mkdir(parents=True, exist_ok=True)
    pool = KeyPool(load_keys(os.environ), time.monotonic() + budget)
    opening_text = selection['opening']['text']
    opening_identity = {'edition': 3, 'model': MODEL, 'voice': selection['voice'],
                        'style': selection['opening'].get('style', selection['style']), 'text': opening_text}
    opening_identity['approved_raw_sha256'] = selection['opening'].get('raw_sha256')
    report = {'edition': 3, 'status': 'running', 'selected_chapters': [c['chapter'] for c in chapters],
              'chapters': [], 'reason': None, 'requests': 0, 'successes': 0}
    def save():
        report.update(requests=pool.attempts, successes=pool.successes)
        save_json(state / 'progress.json', report)
    try:
        opening = record_segment(state / 'opening' / digest(canonical(opening_identity))[:20], opening_text, opening_identity, selection, pool, opening=True)
        save()
        checkpoint(state, 'Save independent v3 opening')
        for chapter in chapters:
            folder = state / 'segments' / chapter['id']
            destination = state / 'chapters' / (str(chapter['chapter']) + '-' + chapter['id'].split('-', 1)[1] + '.mp3')
            receipt = destination.with_suffix('.json')
            if destination.exists() or receipt.exists():
                if not destination.exists() or not receipt.exists():
                    raise GateError('Incomplete completed v3 chapter; explicit recovery required.')
                saved = json.loads(receipt.read_text())
                if saved.get('sha256') != digest(destination.read_bytes()) or saved.get('identity') != chapter['identity'] or saved.get('edition') != 3:
                    raise GateError('Completed v3 chapter checksum mismatch.')
                report['chapters'].append({'chapter': chapter['chapter'], 'status': 'recorded-needs-listening-review'})
                save()
                continue
            atomic(state / 'transcripts' / (chapter['id'] + '.txt'), chapter['transcript'].encode())
            save_json(state / 'plans' / (chapter['id'] + '.json'), chapter['identity'])
            segments = []
            for number, text in enumerate(chapter['chunks']):
                segment = record_segment(folder / str(number), text, chapter['identity'], selection, pool)
                segments.append(segment)
                save()
                checkpoint(state, f'Save v3 chapter {chapter["chapter"]} segment {number + 1}')
                log('segment_saved', chapter=chapter['chapter'], segment=number + 1)
            listing = folder / 'concat.txt'
            # Paths are resolved numeric/content-addressed names under runner workspace.
            def quote(path):
                return str(path.resolve()).replace("'", "'\\''")
            originals = [p.with_name('raw.wav') for p in [opening, *segments]]
            listing.write_text(''.join("file '" + quote(p) + "'\n" for p in originals))
            temporary = folder / 'working.mp3'
            ffmpeg(['-f', 'concat', '-safe', '0', '-i', str(listing), '-ac', '1', '-ar', '24000', '-c:a', 'libmp3lame', '-b:a', '96k', str(temporary)])
            tag_chapter(temporary, chapter, selection, opening_text)
            ffmpeg(['-i', str(temporary), '-f', 'null', '-'])
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary.replace(destination)
            save_json(receipt, {'edition': 3, 'chapter': chapter['chapter'], 'title': chapter['title'],
                                'sha256': digest(destination.read_bytes()), 'identity': chapter['identity'],
                                'opening_sha256': digest(opening.read_bytes()), 'status': 'recorded-needs-listening-review'})
            listing.unlink()
            report['chapters'].append({'chapter': chapter['chapter'], 'status': 'recorded-needs-listening-review'})
            save()
            checkpoint(state, f'Complete v3 chapter {chapter["chapter"]}')
        report['status'], report['reason'] = 'recorded-needs-listening-review', 'selected_chapters_recorded'
    except Paused as error:
        report['status'], report['reason'] = 'paused', str(error)
    except Exception as error:
        # Never serialize an exception/provider string; even exceptions can echo keys.
        report['status'], report['reason'] = 'error', 'generation_error_' + type(error).__name__
        raise GateError('V3 generation stopped; sanitized recovery state retained.') from None
    finally:
        save()
        checkpoint(state, 'Save v3 progress ' + report['status'])
        log('run_final', status=report['status'], requests=pool.attempts, recorded_chapters=len(report['chapters']))


def archive_state(state, destination):
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in state.rglob('*'):
            relative = path.relative_to(state)
            if path.is_file() and not path.is_symlink() and (relative.parts[0] in STATE_DIRS or str(relative) == 'progress.json') and not path.name.endswith('.tmp') and path.name not in ('working.wav', 'working.mp3', 'concat.txt'):
                archive.write(path, str(relative))



def unpack_inputs(archive_path, destination):
    """Validate the complete bounded archive before writing any input file."""
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise GateError('Input extraction destination must be empty.')
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        if len(members) > 1000 or sum(member.file_size for member in members) > 100 * 1024 * 1024:
            raise GateError('Input archive exceeds its safe extraction limits.')
        seen = set()
        for member in members:
            name = member.filename
            if '\\' in name or any(part in ('.', '..') for part in Path(name).parts) or ':' in name:
                raise GateError('Input archive contains an unsafe path.')
            path = contained(destination, name)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode) or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise GateError('Input archive contains a link or special file.')
            if path in seen:
                raise GateError('Input archive contains duplicate paths.')
            seen.add(path)
        # Reading checks ZIP CRC before any production gate is evaluated.
        payloads = [(member, archive.read(member)) for member in members if not member.is_dir()]
    destination.mkdir(parents=True, exist_ok=True)
    for member, data in payloads:
        atomic(contained(destination, member.filename), data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', type=Path, default=ROOT / 'dist/narration-v3-inputs')
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--state', type=Path, default=ROOT / 'dist/audiobook-v3-state')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--validate-production', action='store_true')
    parser.add_argument('--chapters', default='all')
    parser.add_argument('--budget-seconds', type=int, default=19800)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--unpack-inputs', type=Path)
    args = parser.parse_args()
    if args.unpack_inputs:
        unpack_inputs(args.unpack_inputs, args.inputs)
        log('input_bundle_unpacked', api_calls=0)
        return
    if args.archive:
        archive_state(args.state, args.archive)
        return
    if not 60 <= args.budget_seconds <= 19800:
        raise GateError('Budget must be between 60 and 19800 seconds.')
    selected = None if args.chapters == 'all' else {int(n.strip()) for n in args.chapters.split(',')}
    chapters, selection, blockers = load_plan(args.inputs.resolve(), args.source_root.resolve(), selected, generate=not args.prepare_only)
    if args.prepare_only:
        save_json(ROOT / 'dist/audiobook-v3-validation.json', {'edition': 3, 'status': 'preparation-only-no-api-calls',
            'chapters': len(chapters), 'text_checked_chapters': sum(c['text_checked'] for c in chapters),
            'segments': sum(len(c['chunks']) for c in chapters), 'production_blockers': blockers,
            'transcripts': [{'chapter': c['chapter'], 'identity': c['identity']} for c in chapters]})
        log('preparation_only', chapters=len(chapters), text_checked_chapters=sum(c['text_checked'] for c in chapters), production_blockers=len(blockers), api_calls=0)
        return
    if args.validate_production:
        log('production_inputs_validated', chapters=len(chapters), api_calls=0)
        return
    generate(chapters, selection, args.state.resolve(), args.budget_seconds)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # GateError messages contain only fixed local validation text / numeric IDs.
        log('stopped', reason=str(error) if isinstance(error, GateError) else type(error).__name__)
        raise SystemExit(1)
