#!/usr/bin/env python3
"""Approved narration only; serial rotating three-project TTS and durable segments."""
import argparse, base64, hashlib, io, json, os, re, subprocess, time, wave
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from export_audiobook import ROOT, atomic, digest, save_json, split_text, ffmpeg

BRANCH='audiobook-v2-data'
ENDPOINT='https://generativelanguage.googleapis.com/v1beta/interactions'
MODEL='gemini-3.8-flash-tts'
READY='text-reviewed-awaiting-in-context-pronunciation-check'
HEBREW_WORD=re.compile(r'[א-ת\u0591-\u05c7]+')

def point_tav(text):
    def word(m):
        bare=re.sub(r'[\u0591-\u05c7]','',m[0])
        if re.fullmatch(r'[ובכלמהש]{0,3}תו',bare):
            return bare[:-2]+'תָּו'
        return m[0]
    return HEBREW_WORD.sub(word,text)

def scrub(text,keys):
    text=str(text)
    for key in keys:
        key=key.strip()
        if key:
            for form in (key,repr(key)[1:-1],json.dumps(key)[1:-1]):
                text=text.replace(form,'[REDACTED]')
    text=re.sub(r'(?:AIza|AQ\.)[A-Za-z0-9_\-]+','[REDACTED]',text)
    text=re.sub(r'(?i)(api[_-]?key|authorization|access_token|token)\s*[=:]\s*[^\s,;"}]+',r'\1=[REDACTED]',text)
    return text[:16000]

def safe_http_error(raw):
    # Provider messages may echo headers, credentials or request data.
    # Retain only a numeric code and a known symbolic status.
    try:
        error=json.loads(raw).get('error',{})
        statuses={'INVALID_ARGUMENT','UNAUTHENTICATED','PERMISSION_DENIED','NOT_FOUND',
                  'RESOURCE_EXHAUSTED','INTERNAL','UNAVAILABLE','DEADLINE_EXCEEDED'}
        return {'code':error.get('code') if type(error.get('code')) is int else None,
                'status':error.get('status') if error.get('status') in statuses else None}
    except (ValueError,TypeError,AttributeError):
        return {'format':'unstructured-response'}

def log(event,**fields):
    print(json.dumps({'time':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'event':event,**fields},ensure_ascii=False),flush=True)

class RunDone(Exception): pass
class KeyPool:
    def __init__(self,keys,deadline,clock=time.monotonic,sleeper=time.sleep,opener=urlopen):
        self.keys=[key.strip() for key in keys]
        if len(self.keys)!=3 or any(not key or any(ch.isspace() for ch in key) for key in self.keys):raise RuntimeError('Three non-empty API keys without internal whitespace are required.')
        self.deadline=deadline;self.clock=clock;self.sleep=sleeper;self.opener=opener
        self.index=0;self.delay=10;self.limited=set();self.disabled=set();self.attempts=0;self.successes=0
    def request(self,payload,context):
        while True:
            if self.clock()>=self.deadline:raise RunDone('5.5_hour_budget_reached')
            usable=[i for i in range(3) if i not in self.disabled]
            if not usable:raise RuntimeError('All three API keys rejected authentication/permissions.')
            if self.index in self.disabled:self.index=next(i for i in usable)
            index=self.index;key=self.keys[index];self.attempts+=1
            log('request-start',key_number=index+1,attempt=self.attempts,**context)
            req=Request(ENDPOINT,data=json.dumps(payload).encode(),method='POST',headers={'Content-Type':'application/json','x-goog-api-key':key})
            status=None;raw=b''
            try:
                with self.opener(req,timeout=300) as response:
                    status=response.status;raw=response.read()
            except HTTPError as e:
                status=e.code;raw=e.read(65536)
            except (URLError,TimeoutError,OSError) as e:
                log('transport-error',key_number=index+1,error_type=type(e).__name__,**context)
                status=0
            if status==200:
                self.delay=10;self.limited.clear();self.successes+=1
                log('request-success',key_number=index+1,attempt=self.attempts,backoff_reset_seconds=10,**context)
                return json.loads(raw)
            log('http-non-200',key_number=index+1,http_status=status,details=safe_http_error(raw),**context)
            if status in (401,403):self.disabled.add(index)
            elif status not in (0,429,500,502,503,504):raise RuntimeError('Non-retryable Gemini HTTP '+str(status))
            self.limited.add(index)
            self.index=(index+1)%3
            log('switch-key',from_key=index+1,to_key=self.index+1,reason_http=status,**context)
            usable=set(range(3))-self.disabled
            if usable<=self.limited:
                remaining=self.deadline-self.clock()
                if remaining<=0:raise RunDone('5.5_hour_budget_reached')
                wait=min(self.delay,remaining)
                log('all-projects-limited',wait_seconds=round(wait,2),next_backoff_seconds=min(self.delay*2,1800),**context)
                self.sleep(wait);self.delay=min(self.delay*2,1800);self.limited.clear()

def decode_audio(data):
    if data.get('status') not in ('completed',None):raise RuntimeError('Interaction output is not completed.')
    audio=[c for s in data.get('steps',[]) if s.get('type')=='model_output' for c in s.get('content',[]) if c.get('type')=='audio']
    if len(audio)!=1:raise RuntimeError('Expected exactly one audio output.')
    data=base64.b64decode(audio[0]['data'],validate=True)
    with wave.open(io.BytesIO(data),'rb') as w:
        if (w.getnchannels(),w.getsampwidth(),w.getframerate())!=(1,2,24000):raise RuntimeError('Unexpected WAV format.')
        frames=w.readframes(w.getnframes());seconds=w.getnframes()/24000
        if len(frames)!=w.getnframes()*2 or seconds<.5 or not any(frames):raise RuntimeError('Truncated, empty or silent WAV.')
    return data,seconds

def payload(text,selection):
    return {'model':MODEL,'input':[{'type':'user_input','content':[{'type':'text','text':text,'annotations':[{'type':'speech_metadata','style':selection['style']}]}]}],'response_format':{'type':'audio','mime_type':'audio/wav'},'generation_config':{'speech_config':[{'voice':selection['voice']}]},'store':False}

def approved_plan():
    progress=json.loads((ROOT/'narration/progress.json').read_text());selection=json.loads((ROOT/'docs/narration-selection.json').read_text())
    chapters=[]
    for entry in progress['chapters']:
        number=entry['chapter']
        if number not in selection['approved_chapters']:continue
        path=next((ROOT/'narration').glob(f'{number:03d}-*.json'));d=json.loads(path.read_text())
        if d['status']!=READY or d['review'].get('pending'):raise RuntimeError(f'Chapter {number} has unresolved review items.')
        if d['source_sha256']!=digest((ROOT/d['source']).read_bytes()):raise RuntimeError(f'Chapter {number} source changed; review required.')
        text=d['title']+'.\n\n'+'\n\n'.join(point_tav(b['spoken']) for b in d['blocks'] if b.get('spoken'))
        chunks=split_text(text,1200)
        assert ''.join(chunks)==text
        identity={'model':MODEL,'voice':selection['voice'],'style':selection['style'],'chunks':chunks,'opening_sha256':selection['opening_mp3_sha256'],'builder_version':2}
        d.update(transcript=text,chunks=chunks,id=f'{number:03d}-'+digest(json.dumps(identity,ensure_ascii=False,sort_keys=True).encode())[:16]);chapters.append(d)
    if len(chapters)!=116:raise RuntimeError('The approved first batch must contain exactly 116 chapters.')
    return chapters,selection

def checkpoint(state,event):
    if os.environ.get('AUDIOBOOK_COMMIT_CHECKPOINT')!='true':return
    subprocess.run(['git','-C',str(state),'add','--all','.'],check=True)
    if subprocess.run(['git','-C',str(state),'diff','--cached','--quiet']).returncode:
        subprocess.run(['git','-C',str(state),'commit','-m',event],check=True,stdout=subprocess.DEVNULL)
    for attempt in range(1,4):
        r=subprocess.run(['git','-C',str(state),'push','origin','HEAD:'+BRANCH],capture_output=True)
        if r.returncode==0:return
        log('checkpoint-push-retry',attempt=attempt)
        if attempt<3:time.sleep(2*attempt)
    raise RuntimeError('Checkpoint push failed; local state retained for recovery artifact.')

def tag_chapter(path,chapter,selection,text):
    from mutagen.id3 import ID3,TIT2,TALB,TPE1,TPE2,TRCK,TLAN,TCOP,APIC,USLT,TXXX
    try:tags=ID3(path)
    except Exception:tags=ID3()
    tags.add(TIT2(encoding=3,text=chapter['title']));tags.add(TRCK(encoding=3,text=f'{chapter["chapter"]}/126'))
    tags.add(TALB(encoding=3,text='תאוריית המוזיקה הפתוחה — מהדורת שמע 2'))
    tags.add(TPE1(encoding=3,text=['Kris Shaffer','Bryn Hughes','Brian Moseley']));tags.add(TPE2(encoding=3,text='Open Music Theory בעברית'))
    tags.add(TLAN(encoding=3,text='heb'));tags.add(TCOP(encoding=3,text='CC BY-SA 4.0; see individual source credits'))
    tags.add(USLT(encoding=3,lang='heb',desc='מלל הקריינות',text=text))
    for k,v in [('Voice',selection['voice']),('Model',MODEL),('Review','Generated; listening review pending')]:tags.add(TXXX(encoding=3,desc=k,text=v))
    cover=ROOT/'images/gramophone.jpg'
    if cover.exists():tags.add(APIC(encoding=3,mime='image/jpeg',type=3,desc='עטיפת הספר',data=cover.read_bytes()))
    tags.save(path,v2_version=3)

def main():
    p=argparse.ArgumentParser();p.add_argument('--state',type=Path,default=ROOT/'dist/audiobook-v2-state');p.add_argument('--prepare-only',action='store_true');a=p.parse_args()
    chapters,selection=approved_plan();
    if not selection.get('production_enabled') and not a.prepare_only:raise RuntimeError('Production not approved.')
    state=a.state.resolve();state.mkdir(parents=True,exist_ok=True)
    for c in chapters:atomic(state/'transcripts'/f'{c["chapter"]:03d}.txt',c['transcript'].encode())
    log('plan',eligible_chapters=len(chapters),skipped_chapters=selection['excluded_chapters'],segments_total=sum(len(c['chunks']) for c in chapters),voice=selection['voice'],model=MODEL)
    if a.prepare_only:return
    keys=[os.environ.get(f'GEMINI_API_KEY_{i}','').strip() for i in range(1,4)]
    if not all(keys):raise RuntimeError('Exactly three numbered repository secrets are required.')
    opening=ROOT/'dist/audition-source/00-introduction.mp3';opening_json=ROOT/'dist/audition-source/00-introduction.json'
    if digest(opening.read_bytes())!=selection['opening_mp3_sha256']:raise RuntimeError('Approved opening MP3 checksum mismatch.')
    opening_text=json.loads(opening_json.read_text())['text'];begin=time.monotonic();pool=KeyPool(keys,begin+19800)
    report={'status':'running','chapters_total':116,'segments_total':sum(len(c['chunks']) for c in chapters),'new_segments':0,'completed_chapters':0,'voice':selection['voice'],'model':MODEL,'chapters':[]};fatal=None;reason=None
    def save_progress():
        report.update(requests=pool.attempts,successes=pool.successes,elapsed_seconds=round(time.monotonic()-begin,2))
        report['chapters']=[]
        for c in chapters:
            folder=state/'segments'/c['id'];report['chapters'].append({'chapter':c['chapter'],'title':c['title'],'segments_ready':len(list(folder.glob('[0-9][0-9][0-9][0-9].json'))),'segments_total':len(c['chunks']),'complete':(folder/'receipt.json').exists()})
        report['completed_chapters']=sum(c['complete'] for c in report['chapters']);save_json(state/'progress.json',report)
    try:
        for c in chapters:
            folder=state/'segments'/c['id'];folder.mkdir(parents=True,exist_ok=True);complete=folder/'receipt.json'
            dest=state/'chapters'/f'{c["title"]}.mp3'
            if complete.exists() and dest.exists():
                saved=json.loads(complete.read_text())
                if digest(dest.read_bytes())!=saved['sha256']:raise RuntimeError('Completed chapter checksum mismatch.')
                log('skip-complete-chapter',chapter=c['chapter'],title=c['title']);continue
            for number,text in enumerate(c['chunks']):
                mp3=folder/f'{number:04d}.mp3';receipt=mp3.with_suffix('.json')
                context={'chapter':c['chapter'],'title':c['title'],'segment':number+1,'segments_in_chapter':len(c['chunks'])}
                if mp3.exists() and receipt.exists():
                    d=json.loads(receipt.read_text())
                    if d['text_sha256']!=digest(text.encode()) or d['sha256']!=digest(mp3.read_bytes()):raise RuntimeError('Saved segment checksum mismatch.')
                    log('skip-saved-segment',**context);continue
                pcm,seconds=decode_audio(pool.request(payload(text,selection),context))
                wav=folder/'working.wav';atomic(wav,pcm);temporary=folder/'working.mp3'
                ffmpeg(['-i',str(wav),'-ac','1','-ar','24000','-c:a','libmp3lame','-b:a','64k',str(temporary)])
                ffmpeg(['-i',str(temporary),'-f','null','-']);temporary.replace(mp3);wav.unlink()
                save_json(receipt,{'text_sha256':digest(text.encode()),'sha256':digest(mp3.read_bytes()),'seconds':seconds,'voice':selection['voice'],'model':MODEL})
                report['new_segments']+=1;save_progress();checkpoint(state,f'Save v2 chapter {c["chapter"]:03d} segment {number+1}')
                log('segment-saved',duration_seconds=seconds,completed_chapters=report['completed_chapters'],new_segments=report['new_segments'],requests=pool.attempts,elapsed_seconds=round(time.monotonic()-begin,2),**context)
            listing=folder/'concat.txt';listing.write_text("file '"+str(opening)+"'\n"+''.join("file '"+str(folder/f'{i:04d}.mp3')+"'\n" for i in range(len(c['chunks']))))
            dest.parent.mkdir(exist_ok=True,parents=True);tmp=folder/'working.mp3'
            ffmpeg(['-f','concat','-safe','0','-i',str(listing),'-ac','1','-ar','24000','-c:a','libmp3lame','-b:a','96k',str(tmp)])
            tag_chapter(tmp,c,selection,opening_text+'\n\n'+c['transcript']);ffmpeg(['-i',str(tmp),'-f','null','-']);tmp.replace(dest);listing.unlink()
            save_json(complete,{'chapter':c['chapter'],'title':c['title'],'sha256':digest(dest.read_bytes()),'opening_sha256':selection['opening_mp3_sha256'],'source_sha256':c['source_sha256'],'review_status':'generated-needs-listening-review'})
            save_progress();checkpoint(state,f'Complete v2 chapter {c["chapter"]:03d}');log('chapter-complete',chapter=c['chapter'],title=c['title'],completed=report['completed_chapters'],total=116)
    except RunDone as e:reason=str(e)
    except Exception as e:fatal='generation_error_'+type(e).__name__;log('fatal',error=fatal)
    finally:
        report['status']='error' if fatal else 'paused' if reason else 'complete';report['reason']=fatal or reason or 'all_approved_chapters_completed';save_progress();checkpoint(state,'Save v2 progress '+report['status']);log('run-final',status=report['status'],reason=report['reason'],completed_chapters=report['completed_chapters'],new_segments=report['new_segments'],requests=pool.attempts)
        summary=os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary,'a') as s:s.write(f'V2: {report["completed_chapters"]}/116 chapters; {report["status"]}; {pool.attempts} requests.\n\nReason: {report["reason"]}\n')
    if fatal:raise SystemExit(1)

if __name__=='__main__':main()
