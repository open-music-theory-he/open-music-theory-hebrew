#!/usr/bin/env python3
"""Manual auditions only. Never synthesize book chapters or alter production state."""
import hashlib, html, json, os, re, subprocess, time
from pathlib import Path
from export_audiobook import ROOT, atomic, save_json, synthesize, Paused, ffmpeg

DISCLAIMER = 'קריינות שנוצרה בבינה מלאכותית. יתכנו אי דיוקים בהגיית מונחים מוזיקליים מסויימים. תוכן ויזואלי, כולל איורים, תווים ותרגילים מופיעים בגירסה הכתובה. יתכנו שינויים קלים מהגירסה הכתובה. יתכנו שינויים פתאומיים בגוון הצליל ובשטף ההקראה.'
BASE_STYLE = 'Read the exact transcript verbatim in clear natural Israeli Hebrew. Switch smoothly to English where written. Do not translate, transliterate, summarize, add words, or read instructions. Keep all musical letter names as written. No music or sound effects.'

def point_tav(text):
    # Only the singular word and its common Hebrew prefixes; never substrings or plurals.
    return re.sub(r'(?<![א-ת])([ובכלמהש]{0,3})תו(?![א-ת\u0591-\u05c7])', lambda m: m[1]+'תָּו', text)

def sample_plan():
    source = (ROOT/'basicNotation.md').read_text()
    paragraph = next(p for p in source.split('\n\n') if p.startswith('כשהוא כתוב'))
    paragraph = paragraph.replace('*', '')
    spelling = next(p for p in source.split('\n\n') if p.startswith('להלן התו C4'))
    text = paragraph+'\n\n'+spelling
    pointed = point_tav(text)
    samples = [{'id':'00-introduction','label':'פתיח קבוע — מהיר וברור','voice':'Charon','text':DISCLAIMER,
                'style':BASE_STYLE+' Read briskly but distinctly, like the brief disclaimer at the end of a radio advertisement. Articulate every word clearly; short natural pauses at sentence boundaries. Do not rush beyond intelligibility.'}]
    for number, variant, style in [(1,text,BASE_STYLE),(2,pointed,BASE_STYLE),(3,pointed,BASE_STYLE+' Pronounce the musical Hebrew word תָּו as tav, with an a vowel, never tov or tu. This is a pronunciation instruction, not spoken content.')]:
        samples.append({'id':f'0{number}-pronunciation','label':f'בדיקת הגייה {number} — '+['ללא ניקוד','עם ניקוד','ניקוד והוראת הגייה'][number-1], 'voice':'Charon','text':variant,'style':style})
    number=4
    for voice in ('Charon','Orus','Iapetus'):
        for name, direction in [('measured','Measured educational audiobook narration, calm, steady and conversational, natural pauses, without theatrical acting.'),('fluent','Fluent moderately brisk educational narration, clear consonants, compact pauses, stable vocal tone, without theatrical acting.')]:
            samples.append({'id':f'{number:02d}-{voice}-{name}','label':f'קול {voice} — '+('מדוד' if name=='measured' else 'זורם'), 'voice':voice,'text':pointed,'style':BASE_STYLE+' '+direction});number+=1
    return samples

def checkpoint(output):
    if os.environ.get('AUDITION_CHECKPOINT')!='true': return
    subprocess.run(['git','-C',str(output),'add','--all','.'],check=True)
    if subprocess.run(['git','-C',str(output),'diff','--cached','--quiet']).returncode:
        subprocess.run(['git','-C',str(output),'commit','-m','Save audiobook audition samples'],check=True)
    subprocess.run(['git','-C',str(output),'push','origin','HEAD:audiobook-auditions'],check=True)

def main():
    output=ROOT/'dist/audiobook-auditions';output.mkdir(parents=True,exist_ok=True)
    samples=sample_plan();report={'status':'running','completed':0,'total':len(samples),'samples':samples,'requests':0}
    api_key=os.environ.get('GEMINI_API_KEY','')
    if not api_key: raise SystemExit('GEMINI_API_KEY is missing; no request was sent.')
    config={'model':'gemini-3.8-flash-tts','request_timeout_seconds':300}
    for sample in samples:
        sample['sha256']=hashlib.sha256(json.dumps({**sample,'model':config['model']},ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        receipt=output/(sample['id']+'.json');wav=output/(sample['id']+'.wav');mp3=output/(sample['id']+'.mp3')
        if receipt.exists() and wav.exists() and mp3.exists() and json.loads(receipt.read_text()).get('sha256')==sample['sha256']:
            report['completed']+=1;continue
        config.update(voice=sample['voice'],style=sample['style'])
        try:
            report['requests']+=1
            print(f'Audition {report["completed"]+1}/{len(samples)}: {sample["id"]}',flush=True)
            audio,seconds=synthesize(sample['text'],config,api_key)
        except Paused as pause:
            report.update(status='paused',reason=str(pause));print('Stopped: '+str(pause)+'; all completed samples preserved.',flush=True);break
        atomic(wav,audio)
        ffmpeg(['-i',str(wav),'-codec:a','libmp3lame','-q:a','2','-metadata','title='+sample['label'],str(mp3)])
        sample['seconds']=seconds;save_json(receipt,sample);report['completed']+=1
        save_json(output/'progress.json',report);checkpoint(output)
        time.sleep(20)
    else:report.update(status='completed',reason='all_auditions_ready')
    save_json(output/'progress.json',report)
    cards=''.join('<section><h2>'+html.escape(s['label'])+'</h2>'+('<audio controls preload="none" src="'+s['id']+'.mp3"></audio><p><a href="'+s['id']+'.mp3" download>הורדת MP3</a></p>' if (output/(s['id']+'.mp3')).exists() else '<p>ממתין להפקה</p>')+'<details><summary>הטקסט והוראות ההקראה</summary><p dir="auto">'+html.escape(s['text'])+'</p><p dir="ltr">'+html.escape(s['style'])+'</p></details></section>' for s in samples)
    atomic(output/'index.html',('<!doctype html><html lang="he" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>דוגמאות קריינות לאישור</title><style>body{font-family:system-ui;max-width:850px;margin:3rem auto;padding:1rem;background:#f6f8ff;color:#17213b}section{padding:1rem;background:white;border-radius:12px;margin:1rem 0}audio{width:100%}p{white-space:pre-wrap}</style><h1>דוגמאות קריינות לאישור</h1><p>דוגמאות בלבד. הפקת הספר מושבתת. אין תעתיק של מלל באנגלית.</p>'+cards+'</html>').encode())
    checkpoint(output)
    print(f'Final: {report["status"]}; {report["completed"]}/{len(samples)} samples; requests={report["requests"]}',flush=True)

if __name__=='__main__': main()
