#!/usr/bin/env python3
"""Resumable Hebrew audiobook. One fixed model/voice/style; no model fallback."""
from pathlib import Path
from urllib.parse import urlsplit, unquote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import argparse, base64, hashlib, io, json, os, re, subprocess, time, wave, zipfile
import markdown
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/interactions'
BUILDER_VERSION = 1


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_bytes(data)
    temporary.replace(path)


def save_json(path, value):
    atomic(path, (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode())


def source(path):
    text = path.read_text(encoding='utf-8')
    if text.startswith('---\n'):
        parts = text.split('---', 2)
        return yaml.safe_load(parts[1]) or {}, parts[2]
    return {}, text


def chapter_order():
    candidates = {p for p in ROOT.glob('*.md') if source(p)[0].get('translation_status')
                  and p.name not in ('index.md','contents.md','contents-hidden.md','README.md')}
    first = [ROOT/'hebrew-introduction.md', ROOT/'fixed-and-movable-do.md']
    soup = BeautifulSoup(markdown.markdown((ROOT/'contents.md').read_text()), 'html.parser')
    for a in soup.find_all('a', href=True):
        u = urlsplit(a['href'])
        if not u.scheme and u.path.endswith('.html'):
            first.append(ROOT/(unquote(u.path)[:-5]+'.md'))
    return list(dict.fromkeys(p for p in first if p in candidates)) + sorted(candidates-set(first))


def transcript(path):
    meta, body = source(path)
    soup = BeautifulSoup(markdown.markdown(body, extensions=['tables','fenced_code']), 'html.parser')
    notices = []
    for tag in soup.find_all(['script','style','iframe','audio','video','object','embed','pre']):
        if tag.name not in ('script','style'):
            tag.replace_with(' תוכן מצורף או דוגמה שאינם מושמעים בקריינות; ראו את הפרק הכתוב. ')
            notices.append('visual_or_interactive_content')
        else:
            tag.decompose()
    for image in soup.find_all('img'):
        alt = image.get('alt','').strip()
        image.replace_with(' איור בפרק הכתוב'+(': '+alt if alt else '')+'. ')
        notices.append('image_alt_only')
    for table in soup.find_all('table'):
        rows = []
        for row in table.find_all('tr'):
            rows.append('; '.join(cell.get_text(' ',strip=True) for cell in row.find_all(['th','td'])))
        table.replace_with(' טבלה בפרק הכתוב. '+' . '.join(rows)+'. ')
        notices.append('table_linearized_needs_listening_review')
    # Remove site navigation only; keep link labels and the chapter's prose.
    for a in soup.find_all('a', href=True):
        if a.get_text(strip=True) in ('לתוכן העניינים','למקור באנגלית','מקור באנגלית'):
            a.decompose()
    for br in soup.find_all('br'):
        br.replace_with('\n')
    for tag in soup.find_all(['p','li','h1','h2','h3','h4','h5','blockquote']):
        tag.append('\n\n')
    text = soup.get_text(' ',strip=False)
    text = re.sub(r'https?://\S+', 'קישור בפרק הכתוב', text)
    text = re.sub(r'\{\{.*?\}\}|\{%.*?%\}', '', text, flags=re.S)
    text = re.sub('[\u200e\u200f\u202a-\u202e\u2066-\u2069]', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip(' ·\n')
    # ABC stays letter names, rather than changing the book to fixed-do notation.
    letters = {'A':'אֵיי','B':'בִּי','C':'סִי','D':'דִּי','E':'אִי','F':'אֶף','G':'גִ׳י'}
    def note(match):
        letter, accidental, octave = match[1], match[2] or '', match[3]
        modifier = {'':'', '#':' דיאז','♯':' דיאז','b':' במול','♭':' במול','##':' דיאז כפול','bb':' במול כפול','♮':' בקר'}[accidental]
        return letters[letter]+modifier+(' '+octave if octave else '')
    text = re.sub(r'(?<![A-Za-z])([A-G])(##|bb|#|b|♯|♭|♮)?(\d)?(?![A-Za-z])',
                  note, text)
    for symbol, spoken in [('♯',' דיאז '),('♭',' במול '),('♮',' בקר '),('→',' אל '),('–',' — ')]:
        text = text.replace(symbol,spoken)
    if re.search(r'\$|\\(?:frac|hat|flat|sharp|text)|\b[ivIV]+[°ø+]?\d*\b', text):
        notices.append('musical_notation_needs_listening_review')
    intro = 'תאוריית המוזיקה הפתוחה, המהדורה העברית. קריינות שנוצרה בבינה מלאכותית. '
    if notices:
        intro += 'האיורים, התווים והתרגילים מופיעים בגרסה הכתובה; קריינות זו אינה מחליפה אותם. '
    return str(meta['title']), intro+str(meta['title'])+'.\n\n'+text, sorted(set(notices))


def split_text(text, limit):
    """Split at whitespace; retain every character and punctuation in exact order."""
    chunks = []
    while len(text) > limit:
        boundary = text.rfind('\n', 0, limit+1)
        if boundary < limit//3:
            boundary = text.rfind(' ', 0, limit+1)
        if boundary < 1:
            boundary = limit
        else:
            boundary += 1
        chunks.append(text[:boundary]); text = text[boundary:]
    if text:
        chunks.append(text)
    return chunks


def prepare(config):
    if config['model'] != 'gemini-3.8-flash-tts':
        raise ValueError('This edition is locked to gemini-3.8-flash-tts; no automatic model switching.')
    if not 300 <= config['chunk_characters'] <= 1800:
        raise ValueError('Chunk size outside conservative narration bounds')
    identity = {k:config[k] for k in ('model','voice','style','chunk_characters')}
    identity['builder_version'] = BUILDER_VERSION
    edition = digest(json.dumps(identity,sort_keys=True).encode())[:16]
    chapters = []
    for index, path in enumerate(chapter_order(),1):
        title, text, notices = transcript(path)
        chunks = split_text(text,config['chunk_characters'])
        safe_title = re.sub(r'[^א-תA-Za-z0-9 \-]', '',title).strip()[:90]
        chapters.append({'source':path.name,'title':title,'filename':f'{index:03d} — {safe_title}.mp3',
                         'source_sha256':digest(path.read_bytes()),'transcript':text,'chunks':chunks,
                         'id':path.stem+'-'+digest(path.read_bytes()+text.encode())[:16], 'notices':notices})
    return edition, identity, chapters


class Paused(Exception):
    pass


def synthesize(text, config, api_key):
    # 3.8 schema: instructions are metadata, not words read aloud in the transcript.
    payload = {'model':config['model'], 'input':[{'type':'user_input','content':[
        {'type':'text','text':text,'annotations':[{'type':'speech_metadata','style':config['style']}]}]}],
        'response_format':{'type':'audio','mime_type':'audio/wav'},
        'generation_config':{'speech_config':[{'voice':config['voice']}]}, 'store':False}
    request = Request(ENDPOINT, data=json.dumps(payload).encode(), method='POST',
                      headers={'Content-Type':'application/json','x-goog-api-key':api_key})
    try:
        with urlopen(request,timeout=config['request_timeout_seconds']) as response:
            data = json.load(response)
    except HTTPError as error:
        # Never echo remote bodies, request headers, or key-bearing exception details.
        if error.code == 429:
            raise Paused('quota_or_rate_limit_429') from None
        if error.code in (500,502,503,504):
            raise Paused('service_temporarily_unavailable_'+str(error.code)) from None
        raise RuntimeError('Gemini HTTP '+str(error.code)+'; no fallback model was used.') from None
    except (URLError, TimeoutError):
        raise Paused('network_or_timeout') from None
    if data.get('status') not in ('completed', None):
        raise RuntimeError('Gemini interaction did not complete; output was not accepted.')
    audio = [c for step in data.get('steps',[]) if step.get('type')=='model_output'
             for c in step.get('content',[]) if c.get('type')=='audio']
    if len(audio) != 1:
        raise RuntimeError('Expected one audio output block; refusing partial/ambiguous output.')
    pcm = base64.b64decode(audio[0]['data'], validate=True)
    with wave.open(io.BytesIO(pcm),'rb') as wav:
        if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate()) != (1,2,24000):
            raise RuntimeError('Unexpected audio format')
        duration = wav.getnframes()/wav.getframerate()
        frames = wav.readframes(wav.getnframes())
        if len(frames) != wav.getnframes()*wav.getnchannels()*wav.getsampwidth():
            raise RuntimeError('Truncated WAV output')
        if duration < .5 or not any(frames):
            raise RuntimeError('Empty or silent audio')
    return pcm, duration


def ffmpeg(args):
    result = subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y',*args],capture_output=True)
    if result.returncode:
        raise RuntimeError('Audio encoding/decoding validation failed')


def checkpoint(state, message):
    if os.environ.get('AUDIOBOOK_COMMIT_CHECKPOINT') != 'true':
        return
    subprocess.run(['git','add','--all','.'],cwd=state,check=True,stdout=subprocess.DEVNULL)
    changed = subprocess.run(['git','diff','--cached','--quiet'],cwd=state).returncode
    if changed == 1:
        subprocess.run(['git','commit','-m',message],cwd=state,check=True,stdout=subprocess.DEVNULL)
        subprocess.run(['git','push','origin','HEAD:audiobook-data'],cwd=state,check=True,stdout=subprocess.DEVNULL)
    elif changed != 0:
        raise RuntimeError('Cannot inspect checkpoint changes')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state',type=Path,default=ROOT/'dist/audiobook-state')
    parser.add_argument('--output',type=Path,default=ROOT/'dist/audiobook')
    parser.add_argument('--prepare-only',action='store_true')
    args = parser.parse_args()
    config = json.loads((ROOT/'scripts/audiobook-config.json').read_text())
    edition, identity, chapters = prepare(config)
    state = args.state.resolve(); state.mkdir(parents=True,exist_ok=True)
    output = args.output.resolve(); output.mkdir(parents=True,exist_ok=True)
    previous_plan = output/'narration-plan.json'
    if previous_plan.exists() and json.loads(previous_plan.read_text())['edition'] != edition:
        raise RuntimeError('Use a fresh output directory for the new narration edition.')
    work = state/edition; work.mkdir(exist_ok=True)
    save_json(work/'edition.json',identity)
    report = {'edition':edition, **identity, 'status':'prepared','chapters':[], 'requests_this_run':0}
    # Text is reviewable independently of the audio. No additional LLM rewrites it.
    for chapter in chapters:
        atomic(output/'transcripts'/Path(chapter['filename']).with_suffix('.txt'), chapter['transcript'].encode())
    save_json(output/'narration-plan.json', {'edition':edition,**identity,'chapters':[
        {k:v for k,v in ch.items() if k not in ('chunks','transcript')} | {'chunks':len(ch['chunks'])} for ch in chapters]})
    if args.prepare_only:
        print(f'Prepared {len(chapters)} Hebrew chapters / {sum(len(c["chunks"]) for c in chapters)} chunks; no API calls.')
        return
    api_key = os.environ.get('GEMINI_API_KEY','')
    if not api_key:
        raise RuntimeError('Missing repository secret GEMINI_API_KEY')
    begin = time.monotonic(); last_request = None; reason = None; fatal = None
    try:
        for chapter in chapters:
            folder = work/chapter['id']; folder.mkdir(exist_ok=True)
            completed = folder/'chapter.mp3'; receipt = folder/'receipt.json'
            if completed.exists() and receipt.exists():
                saved = json.loads(receipt.read_text())
                if digest(completed.read_bytes()) != saved['sha256']:
                    raise RuntimeError('Completed chapter checksum mismatch')
                continue
            seconds = 0
            for index, text in enumerate(chapter['chunks']):
                segment = folder/f'{index:04d}.mp3'; segment_receipt = folder/f'{index:04d}.json'
                if segment.exists() and segment_receipt.exists():
                    saved = json.loads(segment_receipt.read_text())
                    if saved['text_sha256'] != digest(text.encode()) or saved['sha256'] != digest(segment.read_bytes()):
                        raise RuntimeError('Saved segment checksum mismatch')
                    seconds += saved['seconds']; continue
                if report['requests_this_run'] >= config['max_requests_per_run'] or time.monotonic()-begin >= config['max_run_seconds']:
                    raise Paused('run_budget_reached')
                if last_request is not None:
                    time.sleep(max(0,config['request_interval_seconds']-(time.monotonic()-last_request)))
                last_request = time.monotonic(); report['requests_this_run'] += 1
                pcm, duration = synthesize(text,config,api_key)
                wav = folder/'working.wav'; wav.write_bytes(pcm)
                temporary = folder/'working.mp3'
                ffmpeg(['-i',str(wav),'-ac','1','-ar','24000','-c:a','libmp3lame','-b:a','64k',str(temporary)])
                ffmpeg(['-i',str(temporary),'-f','null','-'])
                temporary.replace(segment); wav.unlink()
                save_json(segment_receipt, {'text_sha256':digest(text.encode()),'sha256':digest(segment.read_bytes()),'seconds':duration})
                seconds += duration
                checkpoint(state,'Save audiobook segment '+chapter['source']+' '+str(index+1))
            listing = folder/'concat.txt'
            listing.write_text(''.join(f"file '{i:04d}.mp3'\n" for i in range(len(chapter['chunks']))))
            ffmpeg(['-f','concat','-safe','0','-i',str(listing),'-c','copy',str(completed)])
            ffmpeg(['-i',str(completed),'-f','null','-'])
            listing.unlink()
            save_json(receipt, {'title':chapter['title'],'source':chapter['source'],'source_sha256':chapter['source_sha256'],
                              'sha256':digest(completed.read_bytes()),'seconds':seconds,'chunks':len(chapter['chunks']),
                              'review_status':'generated_needs_listening_review'})
            checkpoint(state,'Complete audiobook chapter '+chapter['source'])
    except Paused as pause:
        reason = str(pause)
    except Exception as error:
        # Only safe fixed messages are printed; no raw network exception or response.
        fatal = str(error) if isinstance(error,RuntimeError) else type(error).__name__
    finally:
        for chapter in chapters:
            folder = work/chapter['id']; completed = folder/'chapter.mp3'; receipt = folder/'receipt.json'
            item = {k:chapter[k] for k in ('source','title','filename','notices')}
            item['segments_ready'] = len(list(folder.glob('[0-9][0-9][0-9][0-9].json'))) if folder.exists() else 0
            item['segments_total'] = len(chapter['chunks'])
            item['status'] = 'pending'
            if completed.exists() and receipt.exists():
                item.update(json.loads(receipt.read_text())); item['status']='generated'
                atomic(output/'chapters'/chapter['filename'], completed.read_bytes())
            report['chapters'].append(item)
        report['status'] = 'error' if fatal else 'paused' if reason else 'complete'
        report['reason'] = fatal or reason
        save_json(work/'progress.json',report); save_json(output/'progress.json',report)
        checkpoint(state,'Save audiobook progress '+report['status'])
        with zipfile.ZipFile(output/'ספר-שמע-בעברית.zip','w',zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(output.rglob('*')):
                if file.is_file() and file.suffix!='.zip':
                    archive.write(file,file.relative_to(output))
        summary = f"Audiobook: {sum(c['status']=='generated' for c in report['chapters'])}/{len(chapters)} chapters; {report['status']}; requests this run: {report['requests_this_run']}."
        print(summary)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as stream:
                stream.write(summary+'\n\nFixed model: '+config['model']+'; fixed voice: '+config['voice']+'.\n\nReason: '+str(report['reason'])+'\n')
    if fatal:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
