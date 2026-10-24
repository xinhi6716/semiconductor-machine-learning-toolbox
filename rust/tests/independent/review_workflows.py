#!/usr/bin/env python3
"""Independent actual-CLI workflow parity and fail-closed output audit."""
import copy,csv,hashlib,io,json,os,pathlib,random,shutil,stat,struct,subprocess,sys,tempfile
from review_cli import diff
PROJECT=pathlib.Path(os.environ.get('SMLT_PROJECT_ROOT',pathlib.Path.cwd())).resolve()
ROOT=pathlib.Path(os.environ.get('SMLT_REVIEW_OUTPUT',PROJECT/'validation/independent')).resolve();ROOT.mkdir(parents=True,exist_ok=True)
REF=pathlib.Path(os.environ.get('SMLT_PYTHON_REFERENCE',PROJECT/'reference/python')).resolve()
BIN=pathlib.Path(os.environ.get('SMLT_BINARY',PROJECT/'target/release/smlt')).resolve()
SOURCES=pathlib.Path(os.environ.get('SMLT_SOURCE_ROOT',PROJECT)).resolve();RNG=random.Random(3529345)
OUT=ROOT/'workflow-independent';OUT.mkdir(exist_ok=True)
def csvbytes(header,rows):
 s=io.StringIO(newline='');w=csv.writer(s,lineterminator='\n');w.writerow(header.split(','));w.writerows(rows);return s.getvalue().encode()
def rram_data(n=2,cycles=2,identity='d',target='1'):
 manifest=[];iv=[]
 for d in range(n):
  for c in range(cycles):
   for state,g in [('HRS',RNG.randint(1,10)),('LRS',RNG.randint(20,100))]:
    name=f'{identity}{d}';manifest.append(['batch','wafer',name,str(c),str(c),state,state,'c'])
    for v in [0,1,2]:iv.append([name,str(c),state,str(v),str(v*g)])
 return {'manifest.csv':csvbytes('batch,wafer,device,cycle,replicate,branch,state,condition',manifest),'conditions.csv':csvbytes('condition,read_voltage_V,temperature_K,read_delay_ms,protocol',[['c',target,'300','1','protocol']]),'iv_sweeps.csv':csvbytes('device,cycle,branch,voltage,current',iv)}
def fet_data(groups=2,identity='d',mutate=None):
 rows=[]
 for g in range(groups):
  for i in range(5):rows.append(['l','w',f'{identity}{g}','c','r','b','300','.1','0',str(i),str(10**i)])
 if mutate:mutate(rows)
 return {'input.csv':csvbytes('lot,wafer,device,condition,replicate,branch,temperature_K,drain_voltage,body_voltage,gate_voltage,drain_current',rows)}
CASES=[]
def add(name,kind,data,extra=None,expect=None):CASES.append((name,kind,data,extra or [],expect))
for k in range(20):
 add(f'rram-random-{k}','rram',rram_data(RNG.randint(1,5),RNG.randint(1,5),target='1.5' if k%2 else '1'))
 add(f'fet-random-{k}','fet',fet_data(RNG.randint(1,8),mutate=lambda rows:[row.__setitem__(10,str(RNG.choice([1,2,3,5])*10**int(row[9]))) for row in rows]))
for identity in ['a,b|c','器件','double"quote','colon:slash/']:
 add('rram-id-'+str(len(CASES)),'rram',rram_data(identity=identity));add('fet-id-'+str(len(CASES)),'fet',fet_data(identity=identity))
add('fet-audit-zero-log-current','fet',fet_data(mutate=lambda r:r[0].__setitem__(10,'0')),expect=1)
add('fet-audit-nonmonotonic','fet',fet_data(mutate=lambda r:r[2].__setitem__(9,'-1')),expect=1)
add('fet-audit-inconsistent-bias','fet',fet_data(mutate=lambda r:r[1].__setitem__(7,'.2')),expect=1)
add('fet-spreadsheet-formula','fet',fet_data(identity='=PRIVATE_FORMULA'),expect=2)
add('fet-bad-numeric-private','fet',fet_data(mutate=lambda r:r[1].__setitem__(10,'PRIVATE_BAD_VALUE')),expect=2)
add('fet-invalid-unit','fet',fet_data(),['--voltage-unit','mystery'],expect=2)
add('fet-negative-polarity','fet',fet_data(mutate=lambda r:[row.__setitem__(10,'-'+row[10]) for row in r]),['--current-polarity','negative','--criterion-current=-10'])
add('fet-too-small-bracket','fet',fet_data(),['--criterion-current','50','--max-bracket-span','0.1'],expect=1)
add('fet-no-window-points','fet',fet_data(),['--window-min','9','--window-max','10'],expect=1)
for key in ['manifest.csv','conditions.csv','iv_sweeps.csv']:
 data=rram_data();data[key]=b'PRIVATE_BAD_HEADER\n';add('rram-invalid-'+key,'rram',data,expect=2)
add('rram-formula-id','rram',rram_data(identity='@PRIVATE_FORMULA'),expect=2)
data=rram_data();data['iv_sweeps.csv']=data['iv_sweeps.csv'].replace(b',2,',b',0,',1);add('rram-duplicate-voltage','rram',data,expect=2)

def normalize(name,v):
 v=copy.deepcopy(v)
 if name=='provenance.json':
  for k in ['schema_version','python_version','runtime','source_sha256','code_sha256','output_sha256']:v.pop(k,None)
  if 'invocation' in v:
   for k in ['vth','log_slope']:v['invocation'].pop(k,None)
 if name=='iv_readpoints.json':v.pop('invocation',None)
 return v

def command(kind,inputroot,out,extra,native):
 base=[str(BIN),kind+'-features'] if native else [sys.executable,str(REF/'examples'/('measurement-to-features' if kind=='rram' else 'fet-measurement-to-features')/'build_features.py')]
 if kind=='rram':args=['--input-dir',str(inputroot),'--output-dir',str(out),'--max-bracket-span-V','1.1','--accept-measurement-assumptions']
 else:args=[str(inputroot/'input.csv'),'--output-dir',str(out),'--voltage-unit','V','--current-unit','A','--criterion-current','10','--criterion-current-unit','A','--current-polarity','positive','--max-bracket-span','1.1','--window-min','0','--window-max','4','--accept-measurement-assumptions']
 return base+args+extra
results=[]
with tempfile.TemporaryDirectory(prefix='review-native-workflows-',dir=ROOT) as td:
 work=pathlib.Path(td);snapshots=work/'snapshots';snapshots.mkdir()
 for index,(name,kind,files,extra,expect) in enumerate(CASES):
  inp=work/f'input-{index}';inp.mkdir()
  for fn,data in files.items():(inp/fn).write_bytes(data)
  pyout=work/f'python-{index}';rsout=work/f'rust-{index}'
  runs=[]
  for native,out in [(False,pyout),(True,rsout)]:
   env={**os.environ,'TMPDIR':str(snapshots)}
   if native:env['PATH']=''
   runs.append(subprocess.run(command(kind,inp,out,extra,native),capture_output=True,timeout=40,env=env))
  p,r=runs;problem=None
  if p.returncode!=r.returncode:problem=['exit',p.returncode,r.returncode]
  if expect is not None and r.returncode!=expect:problem=problem or ['expected exit',expect,r.returncode]
  if b'PRIVATE' in r.stderr+r.stdout or str(work).encode() in r.stderr+r.stdout or b'panicked' in r.stderr:problem=problem or ['private data/panic in console']
  if list(snapshots.iterdir()):problem=problem or ['snapshot leftovers',[x.name for x in snapshots.iterdir()]]
  if r.returncode==2:
   if rsout.exists():problem=problem or ['fatal-created-output']
  else:
   actual={f.name:f.read_bytes() for f in rsout.iterdir()};expected={f.name:f.read_bytes() for f in pyout.iterdir()}
   if set(actual)!=set(expected):problem=problem or ['output names',sorted(expected),sorted(actual)]
   prov=json.loads(actual['provenance.json'])
   if prov['schema_version']!=2 or prov['runtime']['implementation']!='native Rust':problem=problem or ['wrong native provenance']
   codes=prov.get('source_sha256',prov.get('code_sha256'))
   for fname,h in codes.items():
    if hashlib.sha256((SOURCES/fname).read_bytes()).hexdigest()!=h:problem=problem or ['compiled source fingerprint mismatch',fname]
   if kind=='fet':
    if set(prov['output_sha256'])!=set(actual)-{'provenance.json'}:problem=problem or ['output hash set incomplete']
    for fname,h in prov['output_sha256'].items():
     if hashlib.sha256(actual[fname]).hexdigest()!=h:problem=problem or ['output checksum mismatch',fname]
    if r.returncode==1 and any(f in actual for f in ['features.csv','predictors.csv','feature_schema.json']):problem=problem or ['audit-only leaked partial predictors']
    if actual['transfer_input.csv']!=files['input.csv']:problem=problem or ['retained input altered']
   for fname in set(actual)&set(expected):
    d=diff(normalize(fname,json.loads(expected[fname])),normalize(fname,json.loads(actual[fname]))) if fname.endswith('.json') else (None if expected[fname]==actual[fname] else ['CSV bytes differ'])
    if d:problem=problem or [fname,d]
   if stat.S_IMODE(rsout.stat().st_mode)!=0o700 or any(stat.S_IMODE(f.stat().st_mode)!=0o600 for f in rsout.iterdir()):problem=problem or ['nonprivate output modes']
   saved=actual;r2=subprocess.run(command(kind,inp,rsout,extra,True),capture_output=True,timeout=40)
   if r2.returncode!=2 or saved!={f.name:f.read_bytes() for f in rsout.iterdir()}:problem=problem or ['overwrite allowed']
  for fn,data in files.items():
   if (inp/fn).read_bytes()!=data:problem=problem or ['input mutated',fn]
  item={'name':name,'kind':kind,'passed':problem is None,'problem':problem,'python_exit':p.returncode,'rust_exit':r.returncode};results.append(item)
  if problem:print(json.dumps(item),flush=True)
 # Output directory live/dangling symlink and existing-dir guards on each workflow.
 for kind in ['rram','fet']:
  files=rram_data() if kind=='rram' else fet_data();inp=work/('safety-'+kind);inp.mkdir()
  for fn,data in files.items():(inp/fn).write_bytes(data)
  for mode in ['live','dangling','existing']:
   target=work/(kind+'-target-'+mode);out=work/(kind+'-out-'+mode)
   if mode=='live':target.mkdir();(target/'marker').write_text('unchanged')
   if mode=='existing':out.mkdir()
   else:out.symlink_to(target,target_is_directory=True)
   rr=subprocess.run(command(kind,inp,out,[],True),capture_output=True,timeout=40);ok=rr.returncode==2 and (not target.exists() if mode=='dangling' else (list(out.iterdir())==[] if mode=='existing' else (target/'marker').read_text()=='unchanged' and len(list(target.iterdir()))==1))
   results.append({'name':kind+'-'+mode+'-output','kind':kind,'passed':ok,'problem':None if ok else ['output safety'],'rust_exit':rr.returncode})
report={'binary_filename':BIN.name,'binary_sha256':hashlib.sha256(BIN.read_bytes()).hexdigest(),'cases':len(results),'failed':sum(not r['passed'] for r in results),'comparison':'Actual Python and native Rust CLIs; exact scientific JSON/binary64 including signed zero, exact CSV bytes; only declared schema2 native provenance fields differ, their source/output hashes checked separately','results':results};(ROOT/'review-workflow-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='results'}));sys.exit(bool(report['failed']))
