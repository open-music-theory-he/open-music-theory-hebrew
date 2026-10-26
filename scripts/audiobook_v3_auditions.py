#!/usr/bin/env python3
"""Fresh, manual v3 auditions only; preserve every original WAV and receipt."""
import argparse, hashlib, html, json, os, re, subprocess, time, wave, zipfile
from datetime import datetime, timezone
from pathlib import Path
from export_audiobook import ROOT, atomic, save_json, synthesize, ffmpeg, Paused

MODEL = 'gemini-3.8-flash-tts'
RUN_DEADLINE = time.monotonic() + 2100
OPENING = 'קריינות שנוצרה בבינה מלאכותית. יתכנו אי דיוקים בהגיית מונחים מוזיקליים מסויימים. תוכן ויזואלי, כולל איורים, תווים ותרגילים מופיעים בגירסה הכתובה. יתכנו שינויים קלים מהגירסה הכתובה. יתכנו שינויים פתאומיים בגוון הצליל ובשטף ההקראה.'
OPENING_SOURCE = 'audiobook-auditions/00-introduction.json'
BASE = 'Read the exact transcript verbatim in clear natural Israeli Hebrew. Switch smoothly to English where written. Do not translate, transliterate, summarize, add words, or read instructions. Keep all musical letter names as written. No music or sound effects.'
OPENING_STYLE = BASE + ' Read briskly but distinctly, like the brief disclaimer at the end of a radio advertisement. Articulate every word clearly; short natural pauses at sentence boundaries. Do not rush beyond intelligibility.'
STYLES = [('measured','מדוד','Measured educational audiobook narration, calm, steady and conversational, natural pauses, without theatrical acting.'),('fluent','זורם','Fluent moderately brisk educational narration, clear consonants, compact pauses, stable vocal tone, without theatrical acting.')]
if os.environ.get('AUDITION_STYLE_SET', 'standard') == 'news':
    STYLES = [
        ('radio-news', 'חדשות רדיו — סמכותי וכריזמטי', 'Deliver like a compelling professional Israeli radio news anchor: authoritative, charismatic, resonant and engaged. Use confident sentence endings, purposeful emphasis on important terms, rhythmic phrasing and crisp diction. Moderately brisk pace with short deliberate pauses. Convey intellectual excitement while preserving the exact meaning. No shouting, sensationalism, exaggerated emotion or impersonation of any real presenter.'),
        ('tv-news', 'חדשות טלוויזיה — דרמטי ומודגש', 'Deliver like a charismatic professional Israeli television news anchor presenting an important explainer: more dramatic, vivid and energetic than ordinary educational narration. Use controlled dynamic contrast, strong but natural emphasis, confident projection, meaningful pauses and a poised broadcast cadence. Keep every word intelligible. Preserve the exact meaning. No shouting, invented urgency, added words, music or impersonation of any real presenter.')
    ]

def sha(b): return hashlib.sha256(b).hexdigest()
def canonical(j): return json.dumps(j,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()

def request_sample(text,config,key):
    for attempt in range(6):
        try:
            return synthesize(text,config,key)
        except Paused as pause:
            reason=str(pause)
            safe_reason=reason if re.fullmatch(r'(?:daily_quota_exhausted|quota_or_rate_limit_429|network_or_timeout|service_temporarily_unavailable_(?:500|502|503|504))',reason) else 'provider_pause'
            if safe_reason in ('daily_quota_exhausted','provider_pause') or attempt==5:
                raise
            delay=max(pause.retry_after or 0, min(300,70*2**attempt) if safe_reason=='quota_or_rate_limit_429' else min(120,10*2**attempt))
            if time.monotonic()+delay+config['request_timeout_seconds']>=RUN_DEADLINE:
                raise Paused('audition_time_budget_reached') from None
            print(f'Sample request paused: {safe_reason}; retry in {delay:.0f}s; no key rotation',flush=True)
            time.sleep(delay)

def sample_text():
    # Exact existing paragraph, with only formatting removal and policy-approved niqqud.
    source=(ROOT/'basicNotation.md').read_text()
    text=next(p for p in source.split('\n\n') if p.startswith('כשהוא כתוב')).replace('*','')
    text=text.replace('חמשה','חַמְשָׁה')
    text=re.sub(r'(?<![א-ת])תו(?![א-ת\u0591-\u05c7])','תָּו',text)
    return text

def plan():
    return [{'number':n,'id':f'{n:02d}-{voice}-{style}','voice':voice,'style':BASE+' '+direction,'label':f'{voice} — {label}','text':sample_text(),'opening_style':OPENING_STYLE} for n,(voice,(style,label,direction)) in enumerate(((v,s) for v in ('Charon','Orus','Iapetus') for s in STYLES),1)]

def checkpoint(state):
    if os.environ.get('AUDITION_V3_CHECKPOINT')!='true': return
    def git(*args,check=True): return subprocess.run(['git','-C',str(state),*args],capture_output=True,check=check)
    git('add','--','v3')
    if git('diff','--cached','--quiet',check=False).returncode: git('commit','-m','Preserve v3 audition raw segments and metadata')
    for _ in range(3):
        git('fetch','--no-tags','origin','audiobook-auditions')
        if git('rebase','FETCH_HEAD',check=False).returncode:
            git('rebase','--abort',check=False)
            raise RuntimeError('Concurrent audition state conflict; local recovery retained')
        if git('push','origin','HEAD:refs/heads/audiobook-auditions',check=False).returncode==0: return
    raise RuntimeError('Audition checkpoint push failed; local recovery retained')

def record(folder,text,voice,style,key):
    folder.mkdir(parents=True,exist_ok=True)
    config={'model':MODEL,'voice':voice,'style':style,'request_timeout_seconds':300}
    identity={'model':MODEL,'voice':voice,'style':style,'text':text,'edition':3,'purpose':'audition-only'}
    identity_sha=sha(canonical(identity))
    wav=folder/'raw.wav'; mp3=folder/'preview.mp3'; receipt=folder/'metadata.json'
    if receipt.exists():
        j=json.loads(receipt.read_text())
        if j['identity_sha256']!=identity_sha or j['raw_wav_sha256']!=sha(wav.read_bytes()) or j['mp3_sha256']!=sha(mp3.read_bytes()): raise RuntimeError('Saved audition checksum mismatch')
        return j
    raw_receipt=folder/'raw-metadata.json'
    if wav.exists():
        saved=json.loads(raw_receipt.read_text())
        raw=wav.read_bytes()
        if saved['identity_sha256']!=identity_sha or saved['raw_wav_sha256']!=sha(raw): raise RuntimeError('Unverified saved raw WAV; preserved for recovery')
        seconds=saved['seconds']
    else:
        raw,seconds=request_sample(text,config,key)
        atomic(wav,raw)  # Never overwrite an existing original provider result.
        save_json(raw_receipt,{**identity,'identity_sha256':identity_sha,'raw_wav_sha256':sha(raw),'seconds':seconds,'status':'raw-preserved-before-encoding'})
    ffmpeg(['-i',str(wav),'-c:a','libmp3lame','-q:a','2',str(mp3)])
    ffmpeg(['-i',str(mp3),'-f','null','-'])
    with wave.open(str(wav),'rb') as w: fmt={'channels':w.getnchannels(),'sample_rate':w.getframerate(),'sample_width':w.getsampwidth(),'frames':w.getnframes()}
    j={**identity,'identity_sha256':identity_sha,'text_sha256':sha(text.encode()),'raw_wav_sha256':sha(raw),'mp3_sha256':sha(mp3.read_bytes()),'seconds':seconds,'format':fmt,'recorded_utc':datetime.now(timezone.utc).isoformat(),'status':'recorded-awaiting-user-listening','opening_source':OPENING_SOURCE if text==OPENING else None,'provider_request_config':{'model':MODEL,'response_format':{'type':'audio','mime_type':'audio/wav'},'voice':voice,'speech_metadata_style':style,'store':False}}
    save_json(receipt,j); atomic(folder/'transcript.txt',text.encode())
    return j

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--prepare-only',action='store_true');args=parser.parse_args()
    samples=plan()
    if args.prepare_only:
        print(json.dumps({'samples':len(samples),'raw_segments':9,'book_chapters':0,'opening_text_sha256':sha(OPENING.encode())}));return
    batch=os.environ.get('AUDITION_RESUME_BATCH','').strip() or os.environ.get('GITHUB_RUN_ID','')
    if not re.fullmatch(r'[0-9]+',batch): raise RuntimeError('A GitHub audition run ID is required')
    key=os.environ.get('GEMINI_API_KEY','').strip()
    if not key:
        key=next((os.environ.get('GEMINI_API_KEY_'+str(i),'').strip() for i in range(1,4) if os.environ.get('GEMINI_API_KEY_'+str(i),'').strip()),'')
    if not key: key=next((k.strip() for k in os.environ.get('GEMINI_API_KAYS','').splitlines() if k.strip()),'')
    if not key or any(c.isspace() for c in key): raise RuntimeError('A valid runtime audition secret is missing')
    state=ROOT/'dist/audiobook-auditions';out=state/'v3'/batch;out.mkdir(parents=True,exist_ok=True)
    report={'edition':3,'purpose':'auditions-only','production_enabled':False,'book_chapters_recorded':0,'batch':batch,'generation_run_id':os.environ.get('GITHUB_RUN_ID'),'source_commit':os.environ.get('GITHUB_SHA'),'status':'running','completed':[],'opening_text':OPENING,'opening_source':OPENING_SOURCE,'opening_text_sha256':sha(OPENING.encode()),'samples':samples}
    try:
        for sample in samples:
            voice=sample['voice'];opening=out/'segments'/('opening-'+voice);narration=out/'segments'/sample['id']
            opening_meta=record(opening,OPENING,voice,OPENING_STYLE,key);save_json(out/'progress.json',report);checkpoint(state)
            narration_meta=record(narration,sample['text'],voice,sample['style'],key);save_json(out/'progress.json',report);checkpoint(state)
            combined=out/(sample['id']+'.mp3')
            # Two untouched WAV inputs; separate previews are retained as well.
            ffmpeg(['-i',str(opening/'raw.wav'),'-i',str(narration/'raw.wav'),'-filter_complex','[0:a][1:a]concat=n=2:v=0:a=1[a]','-map','[a]','-c:a','libmp3lame','-q:a','2','-metadata','title='+sample['label'],str(combined)])
            ffmpeg(['-i',str(combined),'-f','null','-'])
            save_json(out/(sample['id']+'.json'),{**sample,'model':MODEL,'combined_sha256':sha(combined.read_bytes()),'opening_segment':str(opening.relative_to(out)),'narration_segment':str(narration.relative_to(out)),'opening_identity_sha256':opening_meta['identity_sha256'],'narration_identity_sha256':narration_meta['identity_sha256'],'opening_text':OPENING,'opening_text_sha256':sha(OPENING.encode()),'status':'audition-awaiting-choice','production_enabled':False})
            report['completed'].append(sample['id']);save_json(out/'progress.json',report);checkpoint(state)
            print(f'Audition {sample["number"]}/6 preserved',flush=True)
        report['status']='completed-awaiting-choice'
    except Paused as pause:
        report['status']='paused-provider-quota-or-network'
        reason=str(pause)
        report['pause_reason']=reason if re.fullmatch(r'(?:daily_quota_exhausted|quota_or_rate_limit_429|network_or_timeout|service_temporarily_unavailable_(?:500|502|503|504)|audition_time_budget_reached)',reason) else 'provider_pause'
    finally:
        save_json(out/'progress.json',report)
        cards=''.join('<section><h2>'+str(s['number'])+' — '+html.escape(s['label'])+'</h2><audio controls src="'+s['id']+'.mp3"></audio><p><a href="'+s['id']+'.mp3" download>MP3</a></p></section>' for s in samples if s['id'] in report['completed'])
        atomic(out/'index.html',('<!doctype html><html lang="he" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>דוגמאות מהדורת שמע 3</title><style>body{font-family:system-ui;max-width:850px;margin:2rem auto;padding:1rem}section{padding:1rem;background:#eef3ff;margin:1rem 0}audio{width:100%}</style><h1>דוגמאות בלבד — הפקת הפרקים מושבתת</h1><p>הפתיח האחרון ללא שינוי במלל. WAV גולמיים ומטא־נתונים נשמרו לכל מקטע.</p>'+cards+'</html>').encode())
        with zipfile.ZipFile(ROOT/'dist/audiobook-v3-auditions.zip','w',zipfile.ZIP_DEFLATED) as z:
            for p in out.rglob('*'):
                if p.is_file() and not p.name.endswith('.tmp'): z.write(p,p.relative_to(out))
        checkpoint(state)
        print('Final: '+report['status']+'; samples='+str(len(report['completed']))+'; book chapters=0',flush=True)

if __name__=='__main__':
    try: main()
    except Exception as error:
        print('Audition stopped safely: '+type(error).__name__,flush=True)
        raise SystemExit(1)
