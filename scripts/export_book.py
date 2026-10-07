#!/usr/bin/env python3
"""Export all tracked static content; never fetch remote resources during PDF rendering."""
from pathlib import Path
from urllib.parse import urlsplit, unquote
import argparse, collections, hashlib, html, io, json, logging, re, subprocess, zipfile
import markdown, yaml
from bs4 import BeautifulSoup
from pypdf import PdfReader, PdfWriter
from weasyprint import HTML, default_url_fetcher

ROOT = Path(__file__).resolve().parents[1]
IMAGES = {'.png', '.jpg', '.jpeg', '.svg', '.gif', '.webp'}
SITE = 'https://open-music-theory-he.github.io/open-music-theory-hebrew'
CSS = '''
@page { size: A4; margin: 17mm 16mm 19mm; @bottom-center { content: counter(page); font: 9pt "DejaVu Sans"; } }
body { font-family: "Noto Sans Hebrew", "DejaVu Sans", sans-serif; font-size: 10.5pt; line-height: 1.55; color: #162536; }
h1 { font-size: 23pt; color: #153e64; } h2 { font-size: 17pt; } h3 { font-size: 13pt; }
h1,h2,h3,h4 { break-after: avoid; } a { color: #235f90; overflow-wrap: anywhere; }
section.chapter, section.asset { break-before: page; } img { max-width: 100%; height: auto; max-height: 235mm; object-fit: contain; }
table { border-collapse: collapse; width: 100%; font-size: 9pt; margin: 8pt 0; }
th,td { border: .5pt solid #bcc9d5; padding: 4pt; vertical-align: top; } th { background: #edf3f8; }
thead { display: table-header-group; } tr,figure { break-inside: avoid; } figure { margin: 8pt 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; direction: ltr; text-align: left; font-size: 8pt; }
code,bdi { direction: ltr; unicode-bidi: isolate; } blockquote { border-inline-start: 3pt solid #9db6cd; padding-inline-start: 8pt; margin-inline: 0; }
.media,.notice { border: .5pt solid #ccd8e3; background: #f3f6fa; padding: 7pt; font-size: 9pt; }
.path { font-size: 8pt; direction: ltr; text-align: left; color: #526879; overflow-wrap: anywhere; }
.cover { break-after: page; padding-top: 25mm; } .toc li { margin-bottom: 3pt; }
'''

def inventory():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    # These authoring files are included during local pre-commit validation too.
    names += ['scripts/export_book.py', 'scripts/requirements-export.txt',
              '.github/workflows/export-book.yml', 'docs/export-guide.md', 'scripts/export-file-names.json']
    result = []
    for name in sorted(set(filter(None, names))):
        p = ROOT / name
        if not p.is_file():
            raise RuntimeError(f'Tracked input is missing: {name}')
        if p.is_symlink() or not p.resolve().is_relative_to(ROOT):
            raise RuntimeError(f'Input outside repository: {name}')
        result.append(p)
    return result

def metadata(p):
    text = p.read_text(encoding='utf-8')
    if text.startswith('---\n'):
        parts = text.split('---', 2)
        return yaml.safe_load(parts[1]) or {}, parts[2]
    return {}, text

def key(p):
    return 'file-' + hashlib.sha256(p.relative_to(ROOT).as_posix().encode()).hexdigest()[:16]

def ordered_documents(files):
    docs = {p for p in files if p.suffix.lower() == '.md'}
    first = [ROOT/'README.md', ROOT/'hebrew-introduction.md', ROOT/'fixed-and-movable-do.md', ROOT/'contents.md']
    toc = (ROOT/'contents.md').read_text()
    # Parse Markdown, including paths such as pitch(Class).html.
    parsed = BeautifulSoup(markdown.markdown(toc, extensions=['tables']), 'html.parser')
    for a in parsed.find_all('a', href=True):
        path = unquote(urlsplit(a['href']).path)
        if path.endswith('.html') and not urlsplit(a['href']).scheme:
            first.append(ROOT/(path[:-5]+'.md'))
    return list(dict.fromkeys(p for p in first if p in docs)) + sorted(docs-set(first), key=lambda p:p.relative_to(ROOT).as_posix())

def local_target(url, current):
    url = html.unescape(url).strip()
    for prefix in (SITE, 'http://openmusictheory.com', 'https://openmusictheory.com', 'https://openmusictheory.github.io'):
        if url.startswith(prefix+'/'):
            return ROOT/unquote(url[len(prefix)+1:].split('#')[0].split('?')[0])
    parts = urlsplit(url)
    if parts.scheme or parts.netloc or url.startswith('#'):
        return None
    return ((ROOT/unquote(parts.path).lstrip('/')) if parts.path.startswith('/') else (current.parent/unquote(parts.path))).resolve()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT/'dist/export')
    args = parser.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    files = inventory(); documents = ordered_documents(files)
    images = [p for p in files if p.suffix.lower() in IMAGES]
    pdfs = [p for p in files if p.suffix.lower() == '.pdf']
    used_images = set(); external = set(); missing_links = []; rendered = []
    pdf_refs = []; failures = []; known_missing_images=[]; related = {}; titles = {}
    explicit_names=json.loads((ROOT/'scripts/export-file-names.json').read_text())
    aliases={}
    for i,p in enumerate(files,1):
        rel=p.relative_to(ROOT).as_posix()
        category='איור מוזיקלי' if p.suffix.lower() in IMAGES else 'הקלטה' if p.suffix.lower()=='.mp3' else 'מסמך מצורף' if p.suffix.lower()=='.pdf' else 'קובץ מקור'
        aliases[p]=explicit_names.get(rel,f'{category} {i:04d}{p.suffix}')
        if '/' in aliases[p] or '\\' in aliases[p] or not re.search('[א-ת]',aliases[p]): raise RuntimeError('Invalid Hebrew name: '+rel)
    if len(set(aliases.values()))!=len(aliases): raise RuntimeError('Duplicate Hebrew filenames')
    for p in documents:
        meta, body = metadata(p)
        related[p]=[]
        # Liquid site references become local paths; never load the live website.
        body = re.sub(r'\{\{\s*site\.(?:url|baseurl)\s*\}\}', SITE, body)
        soup = BeautifulSoup(markdown.markdown(body, extensions=['tables','fenced_code','footnotes','toc']), 'html.parser')
        for t in soup.find_all(['script','style']): t.decompose()
        for t in soup.find_all(['iframe','audio','video','object','embed']):
            urls = [t.get('src') or t.get('data')] + [s.get('src') for s in t.find_all('source')]
            note = soup.new_tag('div', attrs={'class':'media','dir':'rtl'})
            note.append('תוכן אינטראקטיבי או מדיה — אינו ניתן לניגון ב־PDF: ')
            for u in filter(None, urls):
                external.add(u)
                a=soup.new_tag('a', href=u, dir='ltr'); a.string=u; note.append(a); note.append(' ')
            t.replace_with(note)
        # Scope heading anchors per document, including internal fragment links.
        for t in soup.find_all(id=True): t['id']=key(p)+'-'+t['id']
        for t in soup.find_all('img'):
            target=local_target(t.get('src',''),p)
            if not target or not target.is_file() or not target.is_relative_to(ROOT):
                if p.name=='twelveToneOperations.md' and target==ROOT/'Graphics/postTonal/abstractedRowClass.png':
                    known_missing_images.append({'document':p.name,'image':'Graphics/postTonal/abstractedRowClass.png','reason':'Referenced in upstream text, absent from tracked repository'})
                    note=soup.new_tag('div',attrs={'class':'notice','dir':'rtl'}); note.string='איור חסר במקור: תרשים צורות שורה מופשטות. האיור המקושר אינו קיים במאגר ואינו נכלל ביצוא.'; t.replace_with(note)
                else: failures.append(f'{p.relative_to(ROOT)}: missing/nonlocal image {t.get("src")}')
                continue
            used_images.add(target); t['src']=target.as_uri()
        for a in soup.find_all('a',href=True):
            u=a['href']; target=local_target(u,p)
            if u.startswith('#'): a['href']='#'+key(p)+'-'+u[1:]; continue
            if target is None:
                if urlsplit(u).scheme in ('http','https'): external.add(u)
                continue
            if target.suffix=='.html': target=target.with_suffix('.md')
            if target in documents:
                fragment=urlsplit(u).fragment
                a['href']='#'+key(target)+('-'+fragment if fragment else '')
            elif target.is_file() and target.is_relative_to(ROOT):
                if target.suffix.lower()=='.pdf':
                    pdf_refs.append(target); related[p].append(target)
                    a['href']='export-document:'+key(p)+'-'+key(target)
                    a.clear(); a.string=aliases[target]
                else:
                    # Local files are readable Hebrew filenames, not online links.
                    label=soup.new_tag('span',dir='rtl')
                    label.string=aliases[target]
                    if a.find('img'):
                        a.unwrap()
                    else:
                        a.replace_with(label)
            else: missing_links.append({'document':p.relative_to(ROOT).as_posix(),'target':u})
        # Latin music expressions retain their reading direction inside Hebrew prose.
        for t in soup.find_all(['strong','em','code']):
            if not re.search('[א-ת]', t.get_text()): t['dir']='ltr'; t['style']='unicode-bidi: isolate'
        for t in soup.find_all('blockquote'):
            if not re.search('[א-ת]',t.get_text()): t['dir']='ltr'
        if p.name=='schemataSummary.md':
            for t in soup.find_all('table'): t['dir']='ltr'
        heading=soup.find('h1')
        title=str(meta.get('title') or (heading.get_text() if heading else p.stem)); titles[p]=title
        language='he' if meta.get('translation_status') or re.search('[א-ת]',title) else 'en'
        if not meta.get('title') and heading: heading.decompose()
        rendered.append(f'<section class="chapter" id="{key(p)}" lang="{language}" dir="{"rtl" if language=="he" else "ltr"}"><h1>{html.escape(title)}</h1><p class="path">{html.escape(aliases[p])}</p>{soup}</section>')
    if failures: raise RuntimeError('\n'.join(failures))
    # Unreferenced images are also static material: include them in an image appendix.
    orphan_images = sorted(set(images)-used_images)
    for p in orphan_images:
        rendered.append(f'<section class="asset" id="{key(p)}"><h2 dir="rtl">נספח תמונות</h2><p class="path">{html.escape(aliases[p])}</p><img src="{p.as_uri()}" alt="{html.escape(p.name)}"></section>')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    toc=''.join(f'<li><a href="#{key(p)}">{html.escape(titles[p])}</a></li>' for p in documents)
    cover=f'''<div class="cover" dir="rtl"><h1>תאוריית המוזיקה הפתוחה</h1><h2>יצוא סטטי מלא של המאגר</h2><p>היצוא כולל את כל פרקי הטקסט הקיימים, בעברית ובאנגלית, ותיעוד הפרויקט. תרגום הספר עדיין בתהליך.</p><p>תמונות משולבות בפרקים; תמונות נוספות ומסמכי PDF מצורפים מיד אחרי הפרקים המקושרים אליהם. מסמכים ללא הפניה מצורפים בסוף. וידאו ותרגילים מקוונים מופיעים כהפניות בלבד. אין הורדה של תוכן משירותי צד שלישי.</p><p>מקור: Open Music Theory. תרגום בסיוע AI. קרדיטים ורישיונות המקור נשמרים במאגר המצורף; טקסט הספר CC BY-SA 4.0.</p><p class="path">Commit: {commit}</p></div><section class="toc" dir="rtl"><h1>תוכן עניינים</h1><ol>{toc}</ol></section>'''
    rendered.append('<section class="asset" id="export-end"><h1 dir="rtl">סיום החומר הסטטי</h1></section>')
    document=f'<!doctype html><html lang="he"><meta charset="utf-8"><style>{CSS}</style><body>{cover}{"".join(rendered)}</body></html>'
    # Old English heading fragments may no longer exist after translation.
    # Keep these references internal and report their precise fallback explicitly.
    parsed_document=BeautifulSoup(document,'html.parser')
    ids={t['id'] for t in parsed_document.find_all(id=True)}
    fragment_fallbacks=[]
    for link in parsed_document.find_all('a',href=True):
        href=link['href']
        if href.startswith('#') and href[1:] not in ids:
            base=href[1:22]
            if base not in ids: raise RuntimeError('Unresolved internal link: '+href)
            fragment_fallbacks.append({'original':href,'destination':'#'+base})
            link['href']='#'+base
    document=str(parsed_document)
    fetch_errors=[]
    def fetch(url):
        parts=urlsplit(url)
        if parts.scheme=='data': return default_url_fetcher(url)
        if parts.scheme!='file':
            fetch_errors.append('Nonlocal resource blocked: '+url); raise ValueError(fetch_errors[-1])
        path=Path(unquote(parts.path)).resolve()
        if not path.is_relative_to(ROOT):
            fetch_errors.append('Resource outside repository: '+url); raise ValueError(fetch_errors[-1])
        return default_url_fetcher(url)
    class Errors(logging.Handler):
        def emit(self,record):
            if record.levelno>=logging.ERROR: fetch_errors.append(record.getMessage())
    logger=logging.getLogger('weasyprint'); handler=Errors(); logger.addHandler(handler)
    layout=HTML(string=document,base_url=ROOT.as_uri()+'/',url_fetcher=fetch).render()
    anchor_pages={anchor:i for i,page in enumerate(layout.pages) for anchor in page.anchors}
    book_bytes=layout.write_pdf()
    logger.removeHandler(handler)
    if fetch_errors: raise RuntimeError('\n'.join(fetch_errors))
    writer=PdfWriter(); writer.append(PdfReader(io.BytesIO(book_bytes)),import_outline=True)
    annex=[]; insertions=[]
    for index,p in enumerate(documents):
        following = documents[index+1] if index+1<len(documents) else (orphan_images[0] if orphan_images else None)
        position=anchor_pages[key(following)] if following else anchor_pages['export-end']
        for target in dict.fromkeys(related[p]):
            insertions.append((position,target,p))
    unlinked=sorted(set(pdfs)-set(pdf_refs))
    for target in unlinked: insertions.append((len(layout.pages),target,None))
    # Insert in forward order, retaining page objects so existing internal links/bookmarks remain valid.
    offset=0
    for position,target,chapter in sorted(insertions,key=lambda x:x[0]):
        reader=PdfReader(target)
        if reader.is_encrypted and not reader.decrypt(''): raise RuntimeError('Encrypted PDF: '+str(target))
        start=position+offset
        writer.merge(start,reader,outline_item=aliases[target],import_outline=True)
        destination=(key(chapter)+'-' if chapter else '')+key(target)
        writer.add_named_destination(destination,start)
        annex.append({'file':target.relative_to(ROOT).as_posix(),'hebrew_filename':aliases[target], 'after_chapter':chapter.relative_to(ROOT).as_posix() if chapter else None,'start_page':start+1,'pages':len(reader.pages),'destination':destination})
        offset+=len(reader.pages)
    # Convert PDF-resource placeholder URIs into genuine PDF GoTo actions.
    from pypdf.generic import NameObject, DictionaryObject, TextStringObject
    links_rewritten=0
    for page in writer.pages:
        for ref in page.get('/Annots',[]):
            annotation=ref.get_object(); action=annotation.get('/A')
            if action and str(action.get('/URI','')).startswith('export-document:'):
                destination=str(action['/URI']).removeprefix('export-document:')
                if destination not in {a['destination'] for a in annex}: raise RuntimeError('Unresolved document link: '+destination)
                annotation[NameObject('/A')]=DictionaryObject({NameObject('/S'):NameObject('/GoTo'),NameObject('/D'):TextStringObject(destination)})
                links_rewritten+=1
    writer.add_metadata({'/Title':'Open Music Theory — full static export','/Subject':'Current repository snapshot; mixed Hebrew/English','/Author':'Open Music Theory contributors; Hebrew edition contributors'})
    pdf_out=out/'open-music-theory-static.pdf'
    with pdf_out.open('wb') as stream: writer.write(stream)
    final=PdfReader(pdf_out)
    expected=len(PdfReader(io.BytesIO(book_bytes)).pages)+sum(a['pages'] for a in annex)
    assert len(final.pages)==expected
    manifest={'commit':commit,'markdown_documents':len(documents),'images':len(images),'unreferenced_images_appended':len(orphan_images),'pdf_annexes':annex,'internal_document_links':links_rewritten,'final_pdf_pages':len(final.pages),'missing_local_links':missing_links,'heading_links_redirected_to_chapter':fragment_fallbacks,'known_missing_images':known_missing_images,'external_references':sorted(external),'files':[]}
    for p in files:
        manifest['files'].append({'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'hebrew_filename':aliases[p],'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    manifest_bytes=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    (out/'export-manifest.json').write_bytes(manifest_bytes)
    archive=out/'open-music-theory-companion.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in files:
            z.write(p,'repository/'+p.relative_to(ROOT).as_posix())
            z.write(p,'קבצים/'+aliases[p])
        z.writestr('export-manifest.json',manifest_bytes)
        z.writestr('external-media-links.txt','\n'.join(sorted(external))+'\n')
        z.writestr('README.txt','Companion to open-music-theory-static.pdf.\nAll original tracked repository files are preserved, including media, notation source files, images and documents. Hebrew readable filenames are in the קבצים folder, with the original structure also preserved under repository.\nOnline videos and exercises are links, not downloaded media.\nSee export-manifest.json for coverage, unresolved links, hashes and PDF appendix pages.\n')
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for entry in manifest['files']:
            assert hashlib.sha256(z.read('repository/'+entry['path'])).hexdigest()==entry['sha256']
    print(json.dumps({k:manifest[k] for k in ('markdown_documents','images','final_pdf_pages','unreferenced_images_appended')},ensure_ascii=False))
    print(f'PDF: {pdf_out}\nZIP: {archive}\nMissing legacy links recorded: {len(missing_links)}')

if __name__=='__main__': main()
