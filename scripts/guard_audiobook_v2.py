"""Skip hourly/manual duplicates before entering the serialized producer job."""
import json,os
from urllib.request import Request,urlopen

def main():
 repo=os.environ['GITHUB_REPOSITORY'];current=int(os.environ['GITHUB_RUN_ID']);number=int(os.environ['GITHUB_RUN_NUMBER']);active=[]
 for status in ('in_progress','queued','waiting','pending','requested'):
  url=f'https://api.github.com/repos/{repo}/actions/workflows/export-audiobook-v2.yml/runs?status={status}&per_page=100'
  with urlopen(Request(url,headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json'}),timeout=30) as r:data=json.load(r)
  active += [x['id'] for x in data['workflow_runs'] if x['id']!=current and x['run_number']<number]
 enabled=not active
 print('Start producer' if enabled else 'Skip duplicate; earlier active runs: '+str(active),flush=True)
 with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('run='+str(enabled).lower()+'\n')
if __name__=='__main__':main()
