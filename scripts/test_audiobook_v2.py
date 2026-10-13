import contextlib,io,json,unittest,tempfile,subprocess
from pathlib import Path
from urllib.error import HTTPError
from export_audiobook_v2 import KeyPool,RunDone,point_tav,approved_plan,scrub,tag_chapter,safe_http_error
class Response:
 status=200
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def read(self):return b'{}'
class Tests(unittest.TestCase):
 def test_rotation_backoff_cap_reset_and_deadline(self):
  clock=[0];waits=[];keys=[];responses=[429]*33+[200]+[429]*3+[200]
  def sleep(t):waits.append(t);clock[0]+=t
  def open(req,timeout):
   keys.append(req.get_header('X-goog-api-key'));status=responses.pop(0)
   if status!=200:raise HTTPError(req.full_url,status,'limited',{},io.BytesIO(b'{"error":"limited"}'))
   return Response()
  pool=KeyPool(['one','two','three'],100000,clock=lambda:clock[0],sleeper=sleep,opener=open)
  with contextlib.redirect_stdout(io.StringIO()):pool.request({},{});pool.request({},{})
  self.assertEqual(keys[:6],['one','two','three']*2);self.assertEqual(waits[:4],[10,20,40,80]);self.assertEqual(max(waits),1800);self.assertEqual(waits[-1],10)
  pool.deadline=clock[0]
  with self.assertRaises(RunDone):pool.request({},{})
 def test_secret_trailing_whitespace(self):
  headers=[]
  def open(req,timeout):headers.append(req.get_header('X-goog-api-key'));return Response()
  p=KeyPool(['one\n',' two\r\n','three '],100,clock=lambda:0,opener=open)
  with contextlib.redirect_stdout(io.StringIO()):p.request({},{})
  self.assertEqual(p.keys,['one','two','three']);self.assertEqual(headers,['one'])
  with self.assertRaises(RuntimeError):KeyPool(['one','t\nwo','three'],100)
 def test_finish_current_call(self):
  clock=[0]
  def open(req,timeout):clock[0]=200;return Response()
  p=KeyPool(['a','b','c'],100,clock=lambda:clock[0],opener=open)
  with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(p.request({},{}),{})
  with self.assertRaises(RunDone):p.request({},{})
 def test_nikud(self):
  self.assertEqual(point_tav('תו התו לתו בתו ותו תווים תָּו'),'תָּו התָּו לתָּו בתָּו ותָּו תווים תָּו')
 def test_plan(self):
  chapters,sel=approved_plan();self.assertEqual(len(chapters),125);self.assertEqual(sel['voice'],'Iapetus');self.assertEqual(len(sel['excluded_chapters']),1)
  for c in chapters:self.assertTrue(c['title'].startswith(f'{c["chapter"]:03d} —'));self.assertEqual(point_tav(c['transcript']),c['transcript'])
 def test_numbered_id3(self):
  from mutagen.id3 import ID3
  chapters,sel=approved_plan()
  with tempfile.TemporaryDirectory() as folder:
   path=Path(folder)/'chapter.mp3'
   subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=1','-c:a','libmp3lame',str(path)],check=True)
   tag_chapter(path,chapters[0],sel,'פתיח קבוע\n\nמלל הפרק')
   tags=ID3(path);self.assertEqual(str(tags['TIT2']),chapters[0]['title']);self.assertEqual(str(tags['TRCK']),'1/126');self.assertIn('פתיח קבוע',tags.getall('USLT')[0].text)
 def test_secret_scrub(self):self.assertNotIn('secret',scrub('key secret token=foo',['secret']))
 def test_escaped_header_secret(self):
  key='AQ.'+'x'*48
  error="Invalid header value "+repr((key+'\n').encode())
  self.assertNotIn(key,scrub(error,[key+'\n']))
  self.assertNotIn(key,scrub(error,[]))
 def test_provider_errors_allowlist(self):
  key='AQ.'+'x'*48
  raw=json.dumps({'error':{'code':429,'status':'RESOURCE_EXHAUSTED','message':key,'details':[key]}}).encode()
  self.assertEqual(safe_http_error(raw),{'code':429,'status':'RESOURCE_EXHAUSTED'})
  self.assertNotIn(key,str(safe_http_error(key.encode())))
if __name__=='__main__':unittest.main()
