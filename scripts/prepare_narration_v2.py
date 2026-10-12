from pathlib import Path
import json,re,hashlib,copy,yaml,markdown
from bs4 import BeautifulSoup,Tag,NavigableString,Comment
ROOT=Path(__file__).resolve().parents[1]
INV=json.loads((ROOT/'docs/narration-inventory.json').read_text())
DESCS=json.loads((ROOT/'docs/narration-visual-descriptions.json').read_text())
STYLE='Read the exact transcript verbatim in clear natural Israeli Hebrew. Switch smoothly to English where written. Do not translate, transliterate, summarize, add words, or read instructions. Keep all musical letter names as written. No music or sound effects. Fluent moderately brisk educational narration, clear consonants, compact pauses, stable vocal tone, without theatrical acting.'
NAV={'לתוכן העניינים','למקור באנגלית','מקור באנגלית'}
notes=[];chapters=[];issues=[]
def normalize(s):
 s=re.sub('[\u200e\u200f\u202a-\u202e\u2066-\u2069]','',s)
 return re.sub(r'\s+',' ',s).strip()
def prose_text(el):
 cloned=copy.deepcopy(el)
 for a in cloned.find_all('a'):
  if a.get('class') and ('footnote-backref' in a.get('class') or 'footnote-ref' in a.get('class')):a.decompose()
 return normalize(cloned.get_text('',strip=False))
def table_description(el,chapter,ix):
 rows=[[normalize(c.get_text('',strip=False)) for c in r.find_all(['td','th'],recursive=False)] for r in el.find_all('tr')]
 if chapter==12:
  headers=rows[0]; details=[]
  for row in rows[1:]:details.append(row[0]+': '+', '.join(headers[j]+' '+v for j,v in enumerate(row[1:],1) if v))
  text='טבלה המצליבה מספר חצאי טונים עם מספר המרווח הדיאטוני כדי לקבוע את איכותו. העמודות הן פרימה עד אוקטבה, והשורות i0 עד i12. אותו מרחק כרומטי עשוי לקבל כמה שמות לפי איות הצלילים. '+ '; '.join(details)+'.'
 elif chapter==26:text='טבלה המסכמת אילו דרגות יוצרות דיסוננס בכל משפחה תפקודית. בטוניקה ובתחליף טוניקה, T או Tx, הדרגה 7 דיסוננטית וגם 5 אם קיימת 6; בסובדומיננטה S הדרגה 3 וגם 1 אם קיימת 2; בדומיננטה D הדרגות 4 ו־6. זו תלות בתפקיד האקורד, ולא רק במרווח המבודד.'
 elif chapter==34 and ix==0:text='טבלה המבחינה בין צלילים מפעילים, נלווים ודיסוננטיים לפי תפקיד הרמוני. בטוניקה T המפעילים 1 ו־3, הנלווים 5 ו־6, והדיסוננסים 7 וגם 5 כאשר 6 נוכחת. בסובדומיננטה S המפעילים 4 ו־6, הנלווים 1 ו־2, והדיסוננסים 3 וגם 1 כאשר 2 נוכחת. בדומיננטה D המפעילים 5 ו־7, הצליל הנלווה 2, והדיסוננסים 4 ו־6.'
 elif chapter==34:text='אותה חלוקה תפקודית, הפעם בהברות דו נייד במז׳ור ובמינור. ב־T המפעילים do ו־mi או me, הנלווים sol ו־la או le; ti או te דיסוננטיים, וגם sol אם la או le נוכחת. ב־S המפעילים fa ו־la או le, הנלווים do ו־re; mi או me דיסוננטיים, וגם do אם re נוכחת. ב־D המפעילים sol ו־ti או te, הנלווה re, והדיסוננסים fa ו־la או le.'
 elif chapter==40:text='טבלה המסכמת לאיזה תפקיד הרמוני מובילה כרומטיקה באקורד שניוני. T ששונתה כרומטית מובילה לטוניקיזציה של S; S ששונתה כרומטית לטוניקיזציה של D; ו־D ששונתה כרומטית לטוניקיזציה של T. החצים מפרידים בין האקורד המוביל לבין האקורד שאליו הוא מכוון.'
 elif chapter==64:text='טבלה הקושרת בין חטיבות הרונדו לתפקידי צורת הסונטה. בתצוגה חטיבה חוזרת 1 ואפיזודה 1; אחריה חטיבה חוזרת 2. אפיזודה 2 מתפקדת כפיתוח או כנושא פנימי. ברפריזה חטיבה חוזרת 3 ואפיזודה 3, ובסיום חטיבה חוזרת 4. המספור מאפשר לזהות גם חזרות וגם תפקידים צורניים.'
 elif chapter==68:
  count=len(rows[0])-1
  text='טבלת שלבים של הסכמה, המשווה בין הקו המלודי, קו הבס והניתוח ההרמוני. יש '+str(count)+' שלבים לפי סדרם משמאל לימין. '
  for row in rows[1:]:text+=row[0].rstrip(':')+': '+', '.join(row[1:])+'. '
  text+='ההשוואה בין הקווים מראה כיצד אותם שלבים מקבלים גם תיאור של צלילים וגם ניתוח של האקורדים והתפקידים.'
 elif chapter==92:
  text='טבלת הרמוניות שכיחות לפי צליל הבס בפופ וברוק, עם עמודה למצב יסודי 5/3 או 7 ועמודה להיפוך ראשון 6/3 או 6/5. '
  for row in rows[1:]:text+='בבס '+row[0]+', במצב יסודי '+(row[1] or 'אין ערך מודפס')+', ובהיפוך ראשון '+(row[2] or 'אין ערך מודפס')+'. '
  text+='סוגריים מרובעים מציינים אפשרויות המופיעות מסויגות בטבלה, ואין להשוות אוטומטית בין צליל הבס לשורש האקורד.'
 else:raise RuntimeError((chapter,ix,'Unreviewed table'))
 return 'בגירסה הכתובה מופיעה כאן '+text,rows
for num,name in enumerate(INV['files'],1):
 path=ROOT/name;raw=path.read_text();parts=raw.split('---',2);meta=yaml.safe_load(parts[1]);body=parts[2]
 numbered=f'{num:03d} — {meta["title"]}'
 if num<=6:
  p=next((ROOT/'narration').glob(f'{num:03d}-*.json'));d=json.loads(p.read_text());d['voice']='Iapetus';d['status']='text-reviewed-awaiting-in-context-pronunciation-check';d['voice_style']=STYLE;d['voice_selection']='approved:09-Iapetus-fluent';d['review']['pronunciation_pending']=True
  p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');chapters.append({'chapter':num,'source':name,'blocks':len(d['blocks']),'status':d['status'],'visual_explanations':sum(b['kind']=='visual-explanation' for b in d['blocks'])});continue
 rendered=markdown.markdown(re.sub(r'\{\{\s*site.url\s*\}\}','',body),extensions=['tables','fenced_code','footnotes'])
 soup=BeautifulSoup(rendered,'html.parser');blocks=[];pending=[];omissions=[];links=[];assets=[];ti=[0]; media=[]
 for a in soup.find_all('a',href=True):links.append({'label':prose_text(a),'target':a['href']})
 for a in list(soup.find_all('a')):
  if prose_text(a) in NAV:omissions.append({'reason':'site-navigation','text':prose_text(a)});a.decompose()
 def walk(el):
  if isinstance(el,Comment):return
  if isinstance(el,NavigableString):
   t=normalize(str(el))
   if t:blocks.append({'kind':'verbatim','source_text':t,'spoken':t})
   return
  if el.name in ('script','style'):omissions.append({'reason':'non-spoken-code','html':str(el)});return
  if el.name=='img':
   asset=el.get('src','').lstrip('/');assets.append(asset)
   if asset in DESCS:text=DESCS[asset]['spoken'];reviewed=True
   else:text='בגירסה הכתובה מופיע כאן איור שמקורו אינו זמין בקובצי הספר. אין עדיין תיאור מאומת לאיור זה; יש להשלים את בדיקתו לפני ההקלטה.';reviewed=False;pending.append({'kind':'missing-image','asset':asset})
   blocks.append({'kind':'visual-explanation','source':str(el),'asset':asset,'spoken':text,'reviewed':reviewed});return
  if el.name=='table':
   text,rows=table_description(el,num,ti[0]);ti[0]+=1;blocks.append({'kind':'table-explanation','source':str(el),'rows':rows,'spoken':text,'reviewed':True});return
  if el.name in ('iframe','audio','video','object','embed'):
   src=el.get('src',el.get('data',''));title=el.get('title','');host='Spotify' if 'spotify' in src else 'Vimeo' if 'vimeo' in src else 'Trinket' if 'trinket' in src else 'YouTube' if 'youtube' in src else 'מדיה חיצונית'
   if host=='Trinket':text='בגירסה הכתובה מופיע כאן רכיב אינטראקטיבי של Trinket, המאפשר להציג ולבצע דוגמה מוזיקלית. '+('כותרת הרכיב: '+title+'. ' if title else '')+'תוכן התווים שבתוך הרכיב החיצוני טרם נבדק, ולכן אין כאן תיאור מוזיקלי מאומת שלו.';pending.append({'kind':'external-score-unreviewed','target':src})
   else:text='בגירסה הכתובה מופיע כאן נגן '+host+' לצפייה או להאזנה למדיה המקושרת. '+('כותרת הנגן: '+title+'. ' if title else '')+'הנגן והקישור אינם מושמעים בקריינות; הכיתוב המלווה נשמר בטקסט, ולא נטען שתוכן ההקלטה נבדק בהאזנה.'
   blocks.append({'kind':'media-explanation','source':str(el),'target':src,'spoken':text,'external_content_reviewed':False});media.append(src);return
  if el.name in ('p','li','h1','h2','h3','h4','h5','h6','pre','blockquote') and not el.find(['img','table','iframe','audio','video','object','embed','p','li','h1','h2','h3','h4','h5','h6','pre','blockquote']):
   t=prose_text(el)
   if t:blocks.append({'kind':'verbatim','source_html':str(el),'source_text':t,'spoken':t})
   return
  for child in el.children:walk(child)
 for child in soup.children:walk(child)
 # Independent comparison: remove only non-prose resources from the rendered source.
 reference=copy.deepcopy(soup)
 for node in list(reference.find_all(['img','table','iframe','audio','video','object','embed','script','style'])):node.decompose()
 for a in list(reference.find_all('a')):
  if 'footnote-backref' in a.get('class',[]) or 'footnote-ref' in a.get('class',[]):a.decompose()
 expected=normalize(reference.get_text('',strip=False));actual=normalize(' '.join(b['spoken'] for b in blocks if b['kind']=='verbatim'))
 # DOM paragraph boundaries need word spacing but never change text inside them.
 compact=lambda x:re.sub(r'\s+','',x)
 if compact(expected)!=compact(actual):
  import difflib
  a=compact(expected);b=compact(actual);matches=list(difflib.SequenceMatcher(None,a,b).get_opcodes());print([(op,a[i:j][:150],b[k:l][:150]) for op,i,j,k,l in matches if op!='equal']);raise RuntimeError((num,name,'prose sequence differs'))
 issue=[]
 # Chapter 10 rechecked: B natural and C sharp; no inconsistency.
 pending+=issue
 status='prepared-awaiting-media-review' if pending else 'text-reviewed-awaiting-in-context-pronunciation-check'
 d={'chapter':num,'source':name,'title':numbered,'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),'status':status,'opening_asset':'approved:00-introduction.mp3','voice':'Iapetus','voice_style':STYLE,'voice_selection':'approved:09-Iapetus-fluent','blocks':blocks,'review':{'prose_preserved':True,'comparison':'non-whitespace character sequence of rendered source equals verbatim blocks; inline wording preserved','no_transliteration':True,'visuals_reviewed':not any(x['kind']=='missing-image' for x in pending),'visual_assets':assets,'tables_reviewed':ti[0],'external_media_reviewed':False,'external_media':media,'pronunciation_pending':True,'pending':pending,'omissions':omissions,'links':links}}
 stem=path.stem;file=ROOT/'narration'/f'{num:03d}-{stem}.json';file.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');file.with_suffix('.txt').write_text(numbered+'\n\n'+'\n\n'.join(b['spoken'] for b in blocks)+'\n')
 chapters.append({'chapter':num,'source':name,'blocks':len(blocks),'visual_explanations':len(assets),'tables':ti[0],'media':len(media),'status':status,'pending':len(pending)});issues.extend({'chapter':num,'source':name,**p} for p in pending)
progress={'prepared_chapters':126,'total_chapters':126,'audio_production_enabled':False,'voice':'Iapetus','voice_style':STYLE,'voice_selection':'approved:09-Iapetus-fluent','opening_asset':'approved:00-introduction.mp3','chapters':chapters,'review_complete_chapters':sum(not c.get('pending') for c in chapters),'pending':issues,'notes':['All chapter prose preserved without transliteration or new global pointing.','The first five chapters retain their earlier provisional pointing pending an in-context decision.','External scores in Trinket have only verified embed metadata, not inspected score content; affected chapters are not marked ready.','Local image and table explanations are manually authored after visual/source inspection.','The old GEMINI_API_KEY must not be used by the future production runner.']}
progress['audio_production_enabled']=bool(json.loads((ROOT/'docs/narration-selection.json').read_text()).get('production_enabled')) if (ROOT/'docs/narration-selection.json').exists() else False
progress['notes']=['All prose preserved; singular musical תו is pointed תָּו in spoken text.', 'Unresolved external scores and source inconsistencies remain excluded from production.']
(ROOT/'narration/progress.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2)+'\n')
(ROOT/'docs/narration-selection-draft.json').write_text(json.dumps({'approved_at':'2026-10-12','sample':'09-Iapetus-fluent','voice':'Iapetus','style':STYLE,'opening_asset':'00-introduction.mp3','pronunciation_observation':'User heard correct תו in all auditions; earlier book chapters had errors. Context-dependent review is still required.','production_enabled':False,'api_key_secret_names':['GEMINI_API_KEY_1','GEMINI_API_KEY_2','GEMINI_API_KEY_3'],'legacy_api_key_enabled':False},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'chapters':len(chapters),'ready_for_pronunciation_check':progress['review_complete_chapters'],'pending_media_items':len(issues),'visual_descriptions':len(DESCS)},ensure_ascii=False))

# Apply the approved pronunciation policy without changing source wording.
from export_audiobook_v2 import point_tav
for file in (ROOT/'narration').glob('[0-9]*.json'):
 d=json.loads(file.read_text())
 for block in d['blocks']:
  if block.get('spoken'):block['spoken']=point_tav(block['spoken'])
 d['review']['singular_tav_pointed']=True
 file.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
 file.with_suffix('.txt').write_text(d['title']+'\n\n'+'\n\n'.join(b['spoken'] for b in d['blocks'] if b.get('spoken'))+'\n')
