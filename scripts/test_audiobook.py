"""Offline checks for quota interruption, exact resume, and the Gemini 3.8 schema."""
import base64, io, json, os, sys, tempfile, unittest, wave
from pathlib import Path
from unittest.mock import patch
import export_audiobook as book


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer,'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(24000)
        wav.writeframes(b'\x00\x10'*24000)
    return buffer.getvalue()


class AudiobookChecks(unittest.TestCase):
    def test_untranslated_partial_and_unreviewed_chapters_are_excluded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'contents.md').write_text('[A](ready.html) [B](partial.html) [C](english.html)')
            for name, status in [('ready','language-reviewed'),('done','completed'),
                                 ('partial','partial'),('draft','translated'),
                                 ('open','translated-needs-editorial-review'),('english',None)]:
                front = 'layout: post\ntitle: "פרק"\n'
                if status:
                    front += 'translation_status: '+status+'\n'
                (root/(name+'.md')).write_text('---\n'+front+'---\nטקסט')
            with patch.object(book,'ROOT',root):
                self.assertEqual([p.name for p in book.chapter_order()],['ready.md','done.md'])

    def test_split_preserves_every_character(self):
        text = ('מוזיקה. סי דיאז.\n\nעברית English Ⅳ ♭. '*100)+'סיום'
        chunks = book.split_text(text,300)
        self.assertEqual(''.join(chunks),text)
        self.assertTrue(all(0<len(chunk)<=301 for chunk in chunks))

    def test_38_request_and_audio_validation(self):
        config = json.loads((book.ROOT/'scripts/audiobook-config.json').read_text())
        class Reply(io.BytesIO):
            pass
        response = {'status':'completed','steps':[{'type':'model_output','content':[
            {'type':'audio','data':base64.b64encode(wav_bytes()).decode()}]}]}
        with patch.object(book,'urlopen',return_value=Reply(json.dumps(response).encode())) as call:
            audio,seconds = book.synthesize('שלום',config,'test-placeholder')
        payload = json.loads(call.call_args.args[0].data)
        self.assertEqual(payload['model'],'gemini-3.8-flash-tts')
        self.assertEqual(payload['input'][0]['content'][0]['text'],'שלום')
        self.assertEqual(payload['input'][0]['content'][0]['annotations'][0]['style'],config['style'])
        self.assertEqual(payload['generation_config']['speech_config'],[{'voice':config['voice']}])
        self.assertFalse(payload['store'])
        self.assertEqual(audio,wav_bytes()); self.assertEqual(seconds,1)

    def test_quota_stop_then_resume_without_regenerating_saved_segment(self):
        chapter = {'id':'test','source':'test.md','title':'בדיקה','filename':'001 — בדיקה.mp3',
                   'source_sha256':'fixture','transcript':'ראשון שני','chunks':['ראשון','שני'],'notices':[]}
        with tempfile.TemporaryDirectory() as temp:
            args = ['export_audiobook.py','--state',temp+'/state','--output',temp+'/out']
            with patch.dict(os.environ,{'GEMINI_API_KEY':'test-placeholder','AUDIOBOOK_COMMIT_CHECKPOINT':'false','GITHUB_STEP_SUMMARY':''}), \
                 patch.object(sys,'argv',args), patch.object(book,'prepare',return_value=('test',{},[chapter])), \
                 patch.object(book.time,'sleep'), patch.object(book,'synthesize',side_effect=[(wav_bytes(),1),book.Paused('quota_or_rate_limit_429')]):
                book.main()
            report = json.loads(Path(temp+'/out/progress.json').read_text())
            self.assertEqual(report['status'],'paused'); self.assertEqual(report['chapters'][0]['segments_ready'],1)
            with patch.dict(os.environ,{'GEMINI_API_KEY':'test-placeholder','AUDIOBOOK_COMMIT_CHECKPOINT':'false','GITHUB_STEP_SUMMARY':''}), \
                 patch.object(sys,'argv',args), patch.object(book,'prepare',return_value=('test',{},[chapter])), \
                 patch.object(book.time,'sleep'), patch.object(book,'synthesize',return_value=(wav_bytes(),1)) as generate:
                book.main()
            self.assertEqual(generate.call_count,1)
            self.assertEqual(generate.call_args.args[0],'שני')
            report = json.loads(Path(temp+'/out/progress.json').read_text())
            self.assertEqual(report['status'],'complete')
            self.assertTrue(Path(temp+'/out/chapters/001 — בדיקה.mp3').is_file())
            with patch.dict(os.environ,{'GEMINI_API_KEY':'test-placeholder','AUDIOBOOK_COMMIT_CHECKPOINT':'false','GITHUB_STEP_SUMMARY':''}), \
                 patch.object(sys,'argv',args), patch.object(book,'prepare',return_value=('test',{},[chapter])), \
                 patch.object(book,'synthesize') as generate:
                book.main()
            generate.assert_not_called()
            # Changing written source identity must not resynthesize completed audio.
            import subprocess
            from mutagen.id3 import ID3
            audio = Path(temp+'/state/test/test/chapter.mp3')
            def decoded(path):
                return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-f','s16le','-'])
            before = decoded(audio)
            changed = dict(chapter,id='changed',source_sha256='changed-written-source')
            with patch.dict(os.environ,{'GEMINI_API_KEY':'','AUDIOBOOK_COMMIT_CHECKPOINT':'false','GITHUB_STEP_SUMMARY':''}), \
                 patch.object(sys,'argv',args+['--tags-only']), \
                 patch.object(book,'prepare',return_value=('test',{},[changed])), \
                 patch.object(book,'synthesize') as generate:
                book.main()
            generate.assert_not_called()
            self.assertEqual(before,decoded(audio))
            self.assertEqual(book.existing_completed(Path(temp+'/state/test'),changed),audio.parent)
            tags = ID3(audio)
            self.assertTrue(tags.getall('APIC'))
            self.assertEqual(tags.getall('USLT')[0].text,chapter['transcript'])
            self.assertTrue((audio.parent/'chapter-original.mp3').exists())



if __name__ == '__main__':
    unittest.main()
