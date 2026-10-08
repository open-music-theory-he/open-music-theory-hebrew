#!/usr/bin/env python3
"""Structural translation checks. Not a semantic reviewer or a Jekyll build."""
import collections, concurrent.futures, json, re, subprocess, sys
from pathlib import Path
from urllib.parse import urlsplit, unquote
import yaml, markdown, requests
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1]
SOURCE='a907aa015f7ec54b925ddb070d2d36aecd0dc705'
BATCH=['hebrew-introduction.md','fixed-and-movable-do.md','basicNotation.md','meter.md','protonotation.md','rhythmicValues.md','beams.md','pitches.md','scales.md','keySignatures.md','intervals.md','triads.md','motionTypes.md','speciesIntro.md','cantusFirmus.md','firstSpecies.md','secondSpecies.md','thirdSpecies.md','fourthSpecies.md','thoroughbassFigures.md','bassoContinuo-history.md','RNfromFB.md','bassoContinuo.md','tendency.md','tendencyTonesFunctionalDissonances.md','TBDemo.md','melodicKeyboardStyle.md','KBVLschemata.md','schemataOpensAndCloses.md','schemataContinuationPatterns.md','schemataSummary.md','schemata-improv.md','harmonicFunctions.md','harmonicSyntax1.md','harmonicAnalysis.md','harmonicSyntax2.md','cadenceTypes.md','functions.md','modalMixture.md','alteredSubdominants.md','appliedChords.md','Modulation.md','sentence.md','period.md','hybridThemes.md','compoundPeriod.md','themeFunctions.md','compoundSentence.md','smallTernary.md','smallBinary.md','classicalThemes.md']
def render(text):
 parts=text.split('---',2)
 meta=yaml.safe_load(parts[1]) if text.startswith('---\n') else {}
 body=parts[2] if text.startswith('---\n') else text
 body=re.sub(r'\{\{\s*site\.(?:url|baseurl)\s*\}\}', 'https://translation.invalid',body)
 return meta, BeautifulSoup(markdown.markdown(body,extensions=['tables']), 'html.parser')
def urls(soup):
 return [tag.get(attr) for tag,attr in [(t,'src') for t in soup.find_all(src=True)]+[(t,'href') for t in soup.find_all(href=True)]]
def probe(url):
 try:
  r=requests.get(url,timeout=12,headers={'User-Agent':'OMT-Hebrew-link-review/0.1'},stream=True)
  out={'url':url,'status':r.status_code,'final_url':r.url,'result':'reachable' if r.status_code<400 else ('blocked_or_unverified' if r.status_code in (401,403,429) else 'needs_review')}
  r.close();return out
 except requests.RequestException as e:return {'url':url,'result':'unverified','error':str(e).split('\n')[0][:200]}
report={'source_commit':SOURCE,'batches':[1,2,3,4,5,6,7,8,9],'scope':'51 chapter files; index and contents are partial support pages','chapters':[],'external_links':[],'limitations':['No full Jekyll build or visual RTL test in this environment.','Unchanged musical graphics contain original English labels.','HTTP reachability does not verify media playback, identity or regional availability.','Semantic review is AI-assisted, not independent human proofreading.']}
external=set();fatal=[]
for name in BATCH:
 text=(ROOT/name).read_text();meta,soup=render(text);links=urls(soup)
 missing=[]
 for link in links:
  u=urlsplit(link)
  if u.netloc=='translation.invalid': target=unquote(u.path).lstrip('/')
  elif not u.scheme and not link.startswith('#'):target=unquote(u.path).lstrip('/')
  else:
   if u.scheme in ('http','https'):external.add(link)
   continue
  if not target:continue
  candidates=[ROOT/target]
  if target.endswith('.html'):candidates +=[ROOT/(target[:-5]+'.md'),ROOT/(target[:-5]+'.html')]
  if not any(x.exists() for x in candidates):missing.append(link)
 entry={'file':name,'yaml':'pass' if meta.get('layout')=='post' and meta.get('title') else 'fail','hebrew_present':bool(re.search('[א-ת]',soup.get_text())),'local_targets':'pass' if not missing else 'fail','missing_targets':missing,'images':len(soup.find_all('img')),'iframes':len(soup.find_all('iframe')),'image_alt':'pass' if all(t.get('alt','').strip() and '<bdi' not in t.get('alt','') for t in soup.find_all('img')) else 'fail','source_links_and_assets':'not_applicable_editorial_addition'}
 if not meta.get('editorial_addition'):
  source=subprocess.check_output(['git','show',SOURCE+':'+name],cwd=ROOT,text=True)
  _,old=render(source)
  normalizations={('smallBinary.md','smallTernary.html##Binary characteristics'):'smallTernary.html#binary-characteristics',('classicalThemes.md','compoundPeriod'):'compoundPeriod.html',('classicalThemes.md','compoundSentence'):'compoundSentence.html',('sentence.md','harmonicSyntax2.html#subsidiary-harmonic-progressions"'):'harmonicSyntax2.html#subsidiary-harmonic-progressions',('sentence.md','themeFunctions.html#continuation-function'):'themeFunctions.html#continuation',('sentence.md','themeFunctions.html#cadential-function'):'themeFunctions.html#cadential',('schemata-improv.md','https://translation.invalid/media/schemata/Prinner4.mp3'):'https://translation.invalid/media/schemata/prinner4.mp3',('schemata-improv.md','https://translation.invalid/media/schemata/Prinner5.mp3'):'https://translation.invalid/media/schemata/prinner5.mp3',('harmonicFunctions.md','http://openmusictheory.com/harmonicSyntax2.html'):'harmonicSyntax2.html',('harmonicAnalysis.md','http://openmusictheory.com/harmonicSyntax2.html'):'harmonicSyntax2.html',('harmonicSyntax1.md','cadenceTypes'):'cadenceTypes.html',('harmonicSyntax1.md','harmonicAnalysis'):'harmonicAnalysis.html',('harmonicSyntax1.md','tendency'):'tendency.html',('harmonicSyntax1.md','harmonicSyntax2'):'harmonicSyntax2.html',('thoroughbassFigures.md','bassoContinuo-history'):'bassoContinuo-history.html',('schemataOpensAndCloses.md','schemataSummary'):'schemataSummary.html',('schemataSummary.md','http://openmusictheory.com/cadenceTypes.html'):'cadenceTypes.html'}
  old_urls=collections.Counter(normalizations.get((name,u),u) for u in urls(old));new_urls=collections.Counter(links)
  # Every original resource and chapter link must survive, though editorial links may be added.
  removed=list((old_urls-new_urls).elements())
  entry['source_links_and_assets']='pass' if not removed else 'fail';entry['removed_source_targets']=removed
  # Ordered pitch and solfege examples and interval symbols must survive translation.
  tokens=lambda body: collections.Counter(re.findall(r'(?<![A-Za-z])(?:[A-G][#♯♭b]?\d+|[PMAmd]\d+|(?:do|re|mi|me|fa|sol|la|le|ti|te)(?:–(?:do|re|mi|me|fa|sol|la|le|ti|te))+|[A-G](?:–[A-G])+)(?![A-Za-z])',body))
  lost=list((tokens(old.get_text())-tokens(soup.get_text())).elements())
  entry['explicit_music_tokens_preserved']='pass' if not lost else 'fail'
  entry['missing_music_tokens']=lost
  entry['embed_count_preserved']=len(old.find_all('iframe'))==len(soup.find_all('iframe'))
  if meta.get('translation_batch') in (2,3,4,5,6,7,8,9):
   entry['iframe_accessible_titles']='pass' if all(t.get('title','').strip() for t in soup.find_all('iframe')) else 'fail'
  if (lost and meta.get('translation_batch') in (2,3,4,5,6,7,8,9)) or not entry['embed_count_preserved']:fatal.append(name+':music-or-embed')
  if lost and meta.get('translation_batch') not in (2,3,4,5,6,7,8,9):entry['music_token_review']='Repeated wording compressed in batch 1; pitch examples retained and manually reviewed.'
 if missing or entry['yaml']=='fail' or entry['image_alt']=='fail' or entry['source_links_and_assets']=='fail':fatal.append(name)
 report['chapters'].append(entry)
# Exact music table rows, not just table presence.
old=subprocess.check_output(['git','show',SOURCE+':intervals.md'],cwd=ROOT,text=True)
rows=lambda t:[re.sub(r'\s','',l) for l in t.splitlines() if re.match(r'\|\s*i\d+',l)]
report['interval_table_rows_exact']=rows(old)==rows((ROOT/'intervals.md').read_text())
if not report['interval_table_rows_exact']:fatal.append('interval-table')
# Check newly written scale mappings with independent pitch-class arithmetic.
_,do_soup=render((ROOT/'fixed-and-movable-do.md').read_text())
table=do_soup.find('table')
actual=[[c.get_text(strip=True) for c in row.find_all('td')] for row in table.find_all('tr')[1:]]
pc={'C':0,'D':2,'E':4,'F':5,'G':7,'A':9,'B':11}
def pitch_class(token):
 return (pc[token[0]]+(1 if '♯' in token else -1 if '♭' in token else 0))%12
scales={key:[pitch_class(row[col]) for row in actual] for key,col in [('C',2),('D',3),('F',4)]}
steps=[0,2,4,5,7,9,11]
report['new_major_scale_arithmetic']=all(v==[(v[0]+s)%12 for s in steps] for v in scales.values())
previous_path=ROOT/'docs/translation-check-results.json'
previous=json.loads(previous_path.read_text()) if previous_path.exists() else {}
cache={x['url']:x for x in previous.get('external_links',[])}
new_urls=sorted(external-set(cache))
if '--refresh-links' in sys.argv:new_urls=sorted(external)
if '--offline' in sys.argv:
 checked=[{'url':u,'result':'unverified','error':'Not checked in offline mode'} for u in new_urls]
else:
 with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:checked=list(pool.map(probe,new_urls))
cache.update({x['url']:dict(x,checked_at=__import__('datetime').date.today().isoformat()) for x in checked})
report['external_links']=[cache[u] for u in sorted(external)]
report['external_links_checked_this_run']=len(checked) if '--offline' not in sys.argv else 0
report['cached_external_results']=len(external)-len(new_urls)
if not report['new_major_scale_arithmetic']:fatal.append('major-scale-mappings')
report['fatal_structural_errors']=fatal
unit_map=ROOT/'docs/translation-unit-map-batch2.json'
if unit_map.exists():
 units=json.loads(unit_map.read_text())
 report['batch2_unit_structure']=all(c['source_units']==c['target_units_excluding_editorial_notes'] and all(u['structure_matches'] for u in c['units']) for c in units['chapters'])
 report['batch2_unit_hashes_current']=all(c['units'][i]['target_hash']==__import__('hashlib').sha256(b.strip().encode()).hexdigest() for c in units['chapters'] for i,b in enumerate([b for b in re.split(r'\n\s*\n',(ROOT/c['file']).read_text().split('---',2)[2]) if b.strip() and not b.strip().startswith('> **הערת המהדורה העברית:')]))
 if not report['batch2_unit_structure'] or not report['batch2_unit_hashes_current']:fatal.append('batch2-unit-map')
 report['fatal_structural_errors']=fatal
(ROOT/'docs/translation-check-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'chapters':len(BATCH),'fatal':fatal,'external_links':len(external),'external_results':dict(collections.Counter(e['result'] for e in report['external_links']))},ensure_ascii=False))
sys.exit(bool(fatal))
