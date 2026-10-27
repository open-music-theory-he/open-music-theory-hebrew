#!/usr/bin/env python3
"""Boundary tests: edition isolation, stale approvals, quota/security and durable git."""
import base64
from contextlib import contextmanager
import io
import json
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import wave
import zipfile

import export_audiobook_v3 as v3


class V3Tests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root / 'narration-v3'
            inputs.mkdir()
            source = root / 'source'
            source.mkdir()
            body = 'תוכן מוסיקלי עם C♯ ו־B♭.\n'
            (source / 'chapter.md').write_text(body)
            (inputs / 'snapshot.md').write_text(body)
            (inputs / 'policy.md').write_text('policy3')
            policy = v3.digest(b'policy3')
            text = '1 — כותרת\n\n' + body
            metadata = {'edition': 3, 'chapter': 1, 'title': '1 — כותרת', 'source': 'chapter.md',
                'policy_sha256': policy, 'source_sha256': v3.digest(body.encode()),
                'integrated_reading_sha256': v3.digest(body.encode()), 'source_snapshot': 'snapshot.md',
                'status': 'text_checked', 'production_enabled': True,
                'review': {'pending': [], 'visual_review_complete': True, 'prose_sequence_preserved': True,
                           'allowed_transformations_audited': True, 'text_check_evidence': 'local-block-review'},
                'blocks': [{'spoken': body.strip()}]}
            # Transcript convention adds one trailing newline.
            text = metadata['title'] + '\n\n' + metadata['blocks'][0]['spoken'] + '\n'
            v3.save_json(inputs / 'chapter.json', metadata)
            (inputs / 'chapter.txt').write_text(text)
            entry = {'chapter': 1, 'json': 'chapter.json', 'txt': 'chapter.txt', 'status': 'text_checked',
                     'metadata_sha256': v3.digest((inputs / 'chapter.json').read_bytes()),
                     'transcript_sha256': v3.digest(text.encode()), 'source_sha256': metadata['source_sha256'],
                     'integrated_reading_sha256': metadata['integrated_reading_sha256']}
            manifest = {'edition': 3, 'production_enabled': True, 'policy_sha256': policy, 'chapters': [entry]}
            v3.save_json(inputs / 'manifest.json', manifest)
            selection = {'edition': 3, 'model': v3.MODEL, 'voice': 'Iapetus', 'style': 'exact approved style',
                'policy_sha256': policy, 'production_enabled': True,
                'opening': {'text': 'approved new opening', 'style': 'approved fast clear opening style', 'text_sha256': v3.digest(b'approved new opening'),
                            'approved': True, 'approval_evidence': 'user-approval'},
                'sample_approval': {'approved': True, 'evidence': 'new-v3-sample-user-approval'}}
            selection['sample_approval']['identity_sha256'] = v3.digest(v3.canonical({**{k: selection[k] for k in ('model', 'voice', 'style')}, 'opening_style': selection['opening']['style']}))
            v3.save_json(inputs / 'selection.json', selection)
            yield root, inputs, source, metadata, manifest, selection

    def test_checked_plan_retains_exact_text_and_abc(self):
        with self.fixture() as (_, inputs, source, *_):
            chapters, _, blockers = v3.load_plan(inputs, source, generate=True)
            self.assertEqual(blockers, [])
            self.assertEqual(''.join(chapters[0]['chunks']), (inputs / 'chapter.txt').read_text())
            self.assertIn('C♯', chapters[0]['transcript'])

    def test_missing_opening_and_sample_cannot_generate(self):
        with self.fixture() as (_, inputs, source, metadata, manifest, selection):
            selection['opening'] = {}
            selection['sample_approval'] = {}
            v3.save_json(inputs / 'selection.json', selection)
            self.assertGreaterEqual(len(v3.load_plan(inputs, source)[2]), 2)
            with self.assertRaises(v3.GateError):
                v3.load_plan(inputs, source, generate=True)

    def test_status_or_visual_pending_cannot_be_approved_by_manifest(self):
        with self.fixture() as (_, inputs, source, metadata, manifest, _):
            metadata['review']['pending'] = [{'kind': 'unreviewed_image'}]
            v3.save_json(inputs / 'chapter.json', metadata)
            manifest['chapters'][0]['metadata_sha256'] = v3.digest((inputs / 'chapter.json').read_bytes())
            v3.save_json(inputs / 'manifest.json', manifest)
            with self.assertRaises(v3.GateError):
                v3.load_plan(inputs, source, generate=True)

    def test_changed_source_or_transcript_requires_review(self):
        with self.fixture() as (_, inputs, source, *_):
            (source / 'chapter.md').write_text('changed')
            with self.assertRaises(v3.GateError):
                v3.load_plan(inputs, source)
        with self.fixture() as (_, inputs, source, *_):
            with (inputs / 'chapter.txt').open('a') as stream:
                stream.write('added text')
            with self.assertRaises(v3.GateError):
                v3.load_plan(inputs, source)

    def test_reading_overlay_change_invalidates_integrated_hash(self):
        with self.fixture() as (_, inputs, source, metadata, *_):
            overlay = source / 'reading/v2-integrations'
            overlay.mkdir(parents=True)
            (overlay / 'addition.md').write_text('new reading source')
            v3.save_json(overlay / 'manifest.json', {'schema_version': 1, 'chapters': [{
                'target': 'chapter.md', 'addition': 'addition.md', 'base_sha256': metadata['source_sha256'],
                'review_status': 'beta-text-reviewed'}]})
            with self.assertRaises(v3.GateError):
                v3.load_plan(inputs, source)

    def test_manifest_paths_and_duplicate_ids_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(v3.GateError):
                v3.contained(Path(directory), '../other')
        with self.fixture() as (_, inputs, source, _, manifest, _):
            manifest['chapters'].append(manifest['chapters'][0])
            v3.save_json(inputs / 'manifest.json', manifest)
            with self.assertRaises(v3.GateError):
                v3.load_plan(inputs, source)

    def test_unreviewed_selected_chapter_does_not_fall_back(self):
        with self.fixture() as (_, inputs, source, *_):
            with self.assertRaises(v3.GateError):
                v3.load_plan(inputs, source, {99})

    def test_style_change_invalidates_sample_approval(self):
        with self.fixture() as (_, inputs, source, *rest):
            selection = rest[-1]
            selection['style'] += ' a changed style'
            v3.save_json(inputs / 'selection.json', selection)
            with self.assertRaises(v3.GateError):
                v3.load_plan(inputs, source, generate=True)

    def test_runtime_secrets_precedence_and_deduplication(self):
        self.assertEqual(v3.load_keys({'GEMINI_API_KAYS': 'test-a\ntest-b\ntest-a\n', 'GEMINI_API_KEY': 'ignored'}), ['test-a', 'test-b'])
        self.assertEqual(v3.load_keys({'GEMINI_API_KEY_1': 'test-numbered'}), ['test-numbered'])
        self.assertEqual(v3.load_keys({'GEMINI_API_KEY': 'test-single'}), ['test-single'])
        with self.assertRaises(v3.GateError):
            v3.load_keys({'GEMINI_API_KEY': 'bad whitespace'})

    def test_quota_pauses_project_without_trying_sibling_key_or_leaking_body(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            raise HTTPError(v3.ENDPOINT, 429, 'private provider text', {}, io.BytesIO(b'credential-secret'))
        pool = v3.KeyPool(['test-secret-a', 'test-secret-b'], 100, opener=opener, clock=lambda: 1)
        log = io.StringIO()
        with patch('sys.stdout', log), self.assertRaises(v3.Paused):
            pool.request({})
        self.assertEqual(len(calls), 1)
        self.assertNotIn('secret', log.getvalue())
        self.assertNotIn('private', log.getvalue())

    def test_auth_rejection_rotates_but_never_logs_error_body(self):
        calls = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return b'{"ok": true}'
        def opener(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise HTTPError(v3.ENDPOINT, 403, 'private', {}, io.BytesIO(b'secret'))
            return Response()
        pool = v3.KeyPool(['test-a', 'test-b'], 100, opener=opener, clock=lambda: 1)
        with patch('sys.stdout', io.StringIO()):
            self.assertEqual(pool.request({}), {'ok': True})
        self.assertEqual(pool.attempts, 2)

    def test_wav_container_is_decoded_without_double_wrapping(self):
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as wav:
            wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\x01\x00' * 24000)
        raw = stream.getvalue()
        response = {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [{'type': 'audio', 'data': base64.b64encode(raw).decode()}]}]}
        decoded, duration = v3.decode_audio(response)
        self.assertEqual(decoded, raw)
        self.assertEqual(duration, 1)


    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg not installed in validation-only environment')
    def test_synthetic_audio_composition_tags_and_resume_without_provider(self):
        # Synthetic PCM tests the actual encoder/concat/tag path, never a TTS call.
        from mutagen.id3 import ID3
        with self.fixture() as (root, inputs, source, *_):
            chapters, selection, _ = v3.load_plan(inputs, source, generate=True)
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, 'wb') as wav:
                wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
                wav.writeframes(b'\x01\x00' * 24000)
            response = {'steps': [{'type': 'model_output', 'content': [{'type': 'audio', 'data': base64.b64encode(wav_buffer.getvalue()).decode()}]}]}
            class FakePool:
                def __init__(self, *args):
                    self.attempts = self.successes = 0
                def request(self, payload):
                    self.attempts += 1
                    self.successes += 1
                    return response
            state = root / 'audiobook-v3-state'
            legacy = root / 'audiobook-v2-state'
            legacy.mkdir()
            (legacy / 'preserve.mp3').write_bytes(b'old-edition-unchanged')
            with patch.dict('os.environ', {'GEMINI_API_KEY': 'synthetic-test-only', 'GEMINI_API_KAYS': '',
                'GEMINI_API_KEY_1': '', 'GEMINI_API_KEY_2': '', 'GEMINI_API_KEY_3': '', 'AUDIOBOOK_V3_COMMIT_CHECKPOINT': 'false'}), patch.object(v3, 'KeyPool', FakePool), patch('sys.stdout', io.StringIO()):
                v3.generate(chapters, selection, state, 60)
                report = json.loads((state / 'progress.json').read_text())
                self.assertEqual(report['requests'], 2)  # New opening and one chapter segment.
                originals = list(state.rglob('raw.wav'))
                self.assertEqual(len(originals), 2)
                self.assertTrue(all(p.read_bytes() == wav_buffer.getvalue() for p in originals))
                audio = next((state / 'chapters').glob('*.mp3'))
                tags = ID3(audio)
                self.assertEqual(str(tags['TIT2']), chapters[0]['title'])
                self.assertEqual(str(tags['TRCK']), '1')
                self.assertEqual(tags.getall('USLT')[0].text, selection['opening']['text'] + '\n\n' + chapters[0]['transcript'])
                old_hash = v3.digest(audio.read_bytes())
                v3.generate(chapters, selection, state, 60)
                self.assertEqual(json.loads((state / 'progress.json').read_text())['requests'], 0)
                self.assertEqual(v3.digest(audio.read_bytes()), old_hash)
                self.assertEqual((legacy / 'preserve.mp3').read_bytes(), b'old-edition-unchanged')

    def test_original_wav_survives_failed_encoding_and_resume_makes_no_provider_call(self):
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as w:
            w.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            w.writeframes(b'\x01\x00' * 24000)
        raw = stream.getvalue()
        class Pool:
            calls = 0
            def request(self, payload):
                self.calls += 1
                return {'steps':[{'type':'model_output','content':[{'type':'audio','data':base64.b64encode(raw).decode()}]}]}
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);pool=Pool();selection={'voice':'Iapetus','style':'approved','opening':{}}
            with patch.object(v3,'ffmpeg',side_effect=RuntimeError('encoding interrupted')):
                with self.assertRaises(RuntimeError):v3.record_segment(folder,'exact text',{'edition':3},selection,pool)
            self.assertEqual((folder/'raw.wav').read_bytes(),raw)
            def encode(args):
                if args[-1]!='-':Path(args[-1]).write_bytes(b'test mp3')
            with patch.object(v3,'ffmpeg',side_effect=encode):v3.record_segment(folder,'exact text',{'edition':3},selection,pool)
            self.assertEqual(pool.calls,1)
            self.assertEqual((folder/'raw.wav').read_bytes(),raw)

    def test_changed_original_wav_is_rejected_without_provider_call(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'raw.wav').write_bytes(b'tampered')
            v3.save_json(folder/'raw-metadata.json',{'text_sha256':v3.digest(b'text'),'identity_sha256':v3.digest(v3.canonical({})), 'raw_sha256':'invalid','seconds':1})
            with self.assertRaises(v3.GateError):v3.record_segment(folder,'text',{}, {'voice':'Iapetus','style':'x','opening':{}},None)

    def test_durable_checkpoint_rebases_unrelated_concurrent_state_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, work, other = root / 'remote.git', root / 'audiobook-v3-state', root / 'other'
            def command(*args, cwd=None):
                return subprocess.run(['git', *map(str, args)], cwd=cwd, capture_output=True, check=True)
            command('init', '--bare', remote)
            command('init', work)
            for folder in [work]:
                command('config', 'user.name', 'test', cwd=folder)
                command('config', 'user.email', 'test@example.test', cwd=folder)
            (work / 'progress.json').write_text('{}')
            command('add', '.', cwd=work)
            command('commit', '-m', 'initial', cwd=work)
            command('remote', 'add', 'origin', remote, cwd=work)
            command('push', 'origin', 'HEAD:' + v3.BRANCH, cwd=work)
            command('clone', '--branch', v3.BRANCH, remote, other)
            command('config', 'user.name', 'test', cwd=other)
            command('config', 'user.email', 'test@example.test', cwd=other)
            (other / 'notes.txt').write_text('external state note')
            command('add', '.', cwd=other)
            command('commit', '-m', 'concurrent independent note', cwd=other)
            command('push', cwd=other)
            (work / 'progress.json').write_text('{"edition":3}')
            with patch.dict('os.environ', {'AUDIOBOOK_V3_COMMIT_CHECKPOINT': 'true'}):
                v3.checkpoint(work, 'local state checkpoint')
            self.assertTrue((work / 'notes.txt').exists())
            self.assertEqual(command('show', v3.BRANCH + ':notes.txt', cwd=remote).stdout.decode(), 'external state note')


    def test_opening_style_change_invalidates_chapter_cache_identity(self):
        with self.fixture() as (_, inputs, source, metadata, manifest, selection):
            first = v3.load_plan(inputs, source)[0][0]['id']
            selection['opening']['style'] = 'different opening style'
            v3.save_json(inputs / 'selection.json', selection)
            second = v3.load_plan(inputs, source)[0][0]['id']
            self.assertNotEqual(first, second)
            with self.assertRaises(v3.GateError):
                v3.load_plan(inputs, source, generate=True)

    def test_input_extraction_rejects_traversal_and_symlinks_before_writes(self):
        for name, symlink in [('../outside.txt', False), ('/absolute.txt', False), ('link', True)]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / 'input.zip'
                with zipfile.ZipFile(archive, 'w') as output:
                    output.writestr('safe.json', '{}')
                    info = zipfile.ZipInfo(name)
                    if symlink:
                        info.external_attr = 0o120777 << 16
                    output.writestr(info, 'unsafe')
                destination = root / 'inputs'
                with self.assertRaises(v3.GateError):
                    v3.unpack_inputs(archive, destination)
                self.assertFalse(destination.exists())

    def test_input_extraction_checks_crc_and_keeps_relative_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'input.zip'
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr('sources/1-source.md', 'source')
                output.writestr('manifest.json', '{}')
            destination = root / 'inputs'
            v3.unpack_inputs(archive, destination)
            self.assertEqual((destination / 'sources/1-source.md').read_text(), 'source')
            with self.assertRaises(v3.GateError):
                v3.unpack_inputs(archive, destination)

    def test_archive_excludes_git_and_working_files(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'state'
            (state / '.git').mkdir(parents=True)
            (state / '.git/config').write_text('do not upload')
            (state / 'chapters').mkdir()
            (state / 'chapters/1-safe.mp3').write_bytes(b'audio')
            (state / 'chapters/working.wav').write_bytes(b'temporary')
            destination = Path(directory) / 'recovery.zip'
            v3.archive_state(state, destination)
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(archive.namelist(), ['chapters/1-safe.mp3'])


if __name__ == '__main__':
    unittest.main()
