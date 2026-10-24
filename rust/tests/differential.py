#!/usr/bin/env python3
"""Compare captured reference JSON/exit semantics against actual native Rust CLI."""
import argparse, collections, hashlib, json, math, pathlib, subprocess, sys
ROOT=pathlib.Path(__file__).resolve().parents[1];DATA=ROOT/'validation'/'differential'
STRICT=False
def mismatch(a,b,path='$'):
 if isinstance(a,bool) or isinstance(b,bool):return None if a is b else (path,a,b)
 if isinstance(a,(float,int)) and isinstance(b,(float,int)):
  if a==0 and b==0 and math.copysign(1,a)!=math.copysign(1,b):return(path,a,b)
  return None if a==b else (path,a,b)
 if type(a)!=type(b):return (path,a,b)
 if isinstance(a,dict):
  if set(a)!=set(b):return(path+'.keys',sorted(a),sorted(b))
  for k in a:
   m=mismatch(a[k],b[k],path+'.'+k)
   if m:return m
 elif isinstance(a,list):
  if len(a)!=len(b):return(path+'.length',len(a),len(b))
  for i,(x,y) in enumerate(zip(a,b)):
   m=mismatch(x,y,f'{path}[{i}]')
   if m:return m
 elif a!=b:return(path,a,b)
 return None
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--tool',action='append');p.add_argument('--binary',default=str(ROOT/'target'/'release'/'smlt'));p.add_argument('--strict',action='store_true');opts=p.parse_args();STRICT=opts.strict
 cases=json.loads((DATA/'cases.json').read_text());counts=collections.Counter();fails=[];excluded=[]
 for i,c in enumerate(cases):
  if opts.tool and c['tool'] not in opts.tool:continue
  if 'exclude_reason'in c:excluded.append({'index':i,'reason':c['exclude_reason'],'test':c['test']});continue
  args=[opts.binary,c['tool'],str(DATA/c['input']),*c['args'],'--stdout']
  if 'metadata'in c:args+=['--channel-metadata',str(DATA/c['metadata'])]
  try:r=subprocess.run(args,capture_output=True,timeout=30)
  except subprocess.TimeoutExpired:fails.append({'index':i,'test':c['test'],'reason':'timeout'});continue
  problem=None
  if c['expected_exit']is not None and r.returncode!=c['expected_exit']:problem=('exit',c['expected_exit'],r.returncode)
  if 'expected'in c:
   if c['tool']=='rram-window':c['expected']['input']['source']=str(DATA/c['input'])
   try:actual=json.loads(r.stdout)
   except Exception:problem=('not JSON',r.stderr.decode('utf-8','replace')[:100])
   else:
    comparison=mismatch(c['expected'],actual)
    if comparison:problem=comparison
  elif r.returncode!=2:problem=('expected_fatal',2,r.returncode)
  counts[c['tool']]+=1
  if problem:fails.append({'index':i,'test':c['test'],'tool':c['tool'],'problem':problem})
 if not counts:fails.append({'reason':'No applicable native CLI cases were selected; check the capture and tool filters.'})
 report={'binary_sha256':hashlib.sha256(pathlib.Path(opts.binary).read_bytes()).hexdigest(),'checked':sum(counts.values()),'per_tool':dict(counts),'failed':len(fails),'excluded_api_only':excluded,'failures':fails,'float_comparison':'exact, signed-zero-sensitive; no tolerance'}
 destination=ROOT/'validation'/('differential-'+('-'.join(opts.tool) if opts.tool else 'all')+('.strict'if opts.strict else '')+'.json');destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items()if k!='failures'},ensure_ascii=False));print(json.dumps(fails[:25],ensure_ascii=False,indent=2));sys.exit(bool(fails))
