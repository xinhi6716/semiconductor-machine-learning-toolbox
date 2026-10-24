#!/usr/bin/env python3
"""Capture real reference-test API calls as immutable CLI differential fixtures.
Python is test infrastructure only, never required by the Rust executable.
"""
import argparse, copy, os, stat, functools, hashlib, importlib.machinery, inspect, io, json, pathlib, sys, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
REF=pathlib.Path(os.environ.get('SMLT_PYTHON_REFERENCE', ROOT/'reference'/'python' if (ROOT/'reference'/'python').is_dir() else ROOT.parent)).resolve()
OUT=ROOT/'validation'/'differential'; (OUT/'inputs').mkdir(parents=True,exist_ok=True)
CASES=[];ACTIVE=''
def save(data,suffix='csv'):
 digest=hashlib.sha256(data).hexdigest(); p=OUT/'inputs'/(digest+'.'+suffix)
 if not p.exists():p.write_bytes(data)
 return str(p.relative_to(OUT))
def command(tool,params):
 args=[]
 for k,v in params.items():
  if k in ('path','stream','csv_bytes','metadata_bytes','blob','source'):continue
  if k=='assumptions_accepted':
   name={'tlm-extract':'accept-linear-tlm-assumptions','hall-response':'accept-hall-assumptions','vth-constant-current':'accept-transfer-assumptions','transfer-log-slope':'accept-log-transfer-assumptions'}[tool]
   if v is True:args.append('--'+name)
  elif k=='accept_supplied_path_assumptions':
   if v is True:args.append('--accept-supplied-path-assumptions')
  elif v is not None:args+=['--'+k.replace('_','-'),str(v)]
 return args
ORIG=importlib.machinery.SourceFileLoader.exec_module
def exec_module(loader,module):
 ORIG(loader,module)
 try:p=pathlib.Path(loader.path)
 except:return
 if p.parent.parent!=REF/'tools':return
 tool=p.parent.name
 original_limits={k:v for k,v in vars(module).items() if k.startswith('MAX_') or k=='LOG_PRECISION'}
 original_math={k:getattr(module,k) for k in ('root_fraction','log10_fraction') if hasattr(module,k)}
 for name in ('evaluate','evaluate_csv','evaluate_stream','audit'):
  fn=getattr(module,name,None)
  if fn is None:continue
  def wrap(fn,name):
   @functools.wraps(fn)
   def call(*args,**kwargs):
    params=dict(inspect.signature(fn).bind(*args,**kwargs).arguments)
    if 'kwargs' in params:params.update(params.pop('kwargs'))
    try:
     if name=='audit':
      raw=params.get('csv_bytes',params.get('blob'));meta=params.get('metadata_bytes')
     elif name=='evaluate_stream':
      stream=params.get('stream');pos=stream.tell();raw=stream.read();stream.seek(pos);meta=None
      if isinstance(raw,str):raw=raw.encode('utf-8')
     else:
      path=params.get('path',args[0] if args else None);path=pathlib.Path(path)
      if not stat.S_ISREG(path.stat().st_mode):raise ValueError('not regular')
      raw=path.read_bytes();meta=None
     case={'tool':tool,'test':ACTIVE,'input':save(raw),'args':command(tool,params)}
     altered=[k for k,v in original_limits.items() if getattr(module,k)!=v]
     if altered:case['exclude_reason']='Python-only monkeypatched limits: '+','.join(altered)
     altered_functions=[k for k,v in original_math.items() if getattr(module,k)is not v]
     if altered_functions:case['exclude_reason']='Python-only monkeypatched arithmetic: '+','.join(altered_functions)
     if name=='audit' and type(raw)is not bytes:case['exclude_reason']='Python API non-bytes input cannot occur on CLI'
     if any(v is not None and not isinstance(v,(str,bool,pathlib.Path,io.IOBase,bytes)) for k,v in params.items() if k not in ('stream',)):case['exclude_reason']='Python API non-string argument type cannot occur on CLI'
     if meta is not None:case['metadata']=save(meta,'json')
    except Exception:case=None
    try:r=fn(*args,**kwargs)
    except Exception as e:
     if case is not None:case.update(expected_exit=2,error_type=type(e).__name__);CASES.append(case)
     raise
    if case is not None:
     case['expected']=copy.deepcopy(r)
     case['expected_exit']=int(bool(r.get('counts',{}).get('rejected_groups',0) or r.get('counts',{}).get('rejected_loops',0) or r.get('status')=='GRID_FINDINGS' or (tool=='rram-window' and r.get('counts',{}).get('pairs_invalid',0))))
     if tool=='rram-window':case['expected_exit']=int(bool(r['counts']['excluded_groups']))
     CASES.append(case)
    return r
   return call
  setattr(module,name,wrap(fn,name))
importlib.machinery.SourceFileLoader.exec_module=exec_module
class Result(unittest.TextTestResult):
 def startTest(self,test):
  global ACTIVE
  ACTIVE=test.id();super().startTest(test)
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--tool');opts=parser.parse_args()
 suite=unittest.TestSuite()
 for p in sorted((REF/'tools').glob('*/tests')):
  if opts.tool and p.parent.name!=opts.tool:continue
  suite.addTests(unittest.defaultTestLoader.discover(str(p),top_level_dir=str(p)))
 result=unittest.TextTestRunner(verbosity=1,resultclass=Result).run(suite)
 if result.testsRun==0 or not CASES:
  print('No reference CLI cases were captured; check the reference path and tool selection.',file=sys.stderr)
  sys.exit(2)
 (OUT/('cases-'+opts.tool+'.json' if opts.tool else 'cases.json')).write_text(json.dumps(CASES,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'captured_cases':len(CASES)}))
 sys.exit(not result.wasSuccessful())
