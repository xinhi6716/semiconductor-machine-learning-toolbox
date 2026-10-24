#!/usr/bin/env python3
"""Independent immutable-binary safety and boundary checks, synthetic inputs only."""
import hashlib,json,os,pathlib,resource,stat,subprocess,sys,tempfile,time
from review_cli import ROOT,PROJECT,HEAD,ARGS,rawcsv
BIN=pathlib.Path(os.environ.get('SMLT_BINARY',PROJECT/'target/release/smlt'))
TOOLS={
'iv-readpoint':(HEAD['iv-readpoint'],[['d','1','b','1','2']],ARGS['iv-readpoint']),
'rram-window':(HEAD['rram-window'],[['d','1','H','read','1','10'],['d','1','L','read','1','1']],ARGS['rram-window']),
'sweep-segments':(HEAD['sweep-segments'],[['l','w','d','c','s','0','0'],['l','w','d','c','s','1','1']],ARGS['sweep-segments']),
'iv-grid-audit':(HEAD['iv-grid-audit'],[['l','w','d','c','s','b','0','1']],ARGS['iv-grid-audit']),
'iv-loop-integral':(HEAD['iv-loop-integral'],[['l','w','d','c','1','r','0','up','0','0'],['l','w','d','c','1','r','1','down','1','1'],['l','w','d','c','1','r','2','','0','0']],ARGS['iv-loop-integral']),
'bosch-identity-audit':(HEAD['bosch-identity-audit'],[['2024-02-29_01','l','1','0','opaque','opaque','opaque','opaque','opaque','opaque','opaque']],ARGS['bosch-identity-audit']),
'hall-response':('lot,wafer,device,condition,replicate,branch,temperature_K,bias_current,magnetic_field,transverse_voltage',[['l','w','d','c','r','b','300','1',str(i),str(2*i+1)] for i in [-1,0,1]],['--field-unit','T','--voltage-unit','V','--current-unit','A','--accept-hall-assumptions']),
 'tlm-extract':('lot,wafer,structure,condition,replicate,pair,spacing,width,resistance',[['l','w','s','c','r',str(i),str(i),'1',str(2*i+1)] for i in [1,2,3]],['--length-unit','m','--resistance-unit','ohm','--accept-linear-tlm-assumptions'])}
for t,accept,more in [('vth-constant-current','--accept-transfer-assumptions',['--criterion-current','10','--criterion-current-unit','A']),('transfer-log-slope','--accept-log-transfer-assumptions',['--window-min','0','--window-max','2'])]:
 TOOLS[t]=('lot,wafer,device,condition,replicate,branch,temperature_K,drain_voltage,body_voltage,gate_voltage,drain_current',[['l','w','d','c','r','b','300','.1','0',str(i),str(10**i)] for i in range(3)],['--voltage-unit','V','--current-unit','A','--current-polarity','positive',accept]+more)
checks=[]
def record(tool,name,ok,detail=None):
 checks.append({'tool':tool,'name':name,'passed':bool(ok),'detail':detail});
 if not ok:print(json.dumps(checks[-1]),flush=True)
def run(args,**kw):return subprocess.run([str(BIN),*args],capture_output=True,timeout=10,**kw)
with tempfile.TemporaryDirectory(prefix='independent-rust-safety-',dir=ROOT) as td:
 d=pathlib.Path(td)
 for tool,(header,rows,options) in TOOLS.items():
  src=d/(tool+'.csv');raw=rawcsv(tool,rows,header=header);src.write_bytes(raw);args=[tool,str(src),*options]
  out=d/(tool+'.json');r=run(args+['--output',str(out)]);record(tool,'create-new',r.returncode==0 and not r.stdout and out.exists(),r.returncode)
  record(tool,'mode-0600',stat.S_IMODE(out.stat().st_mode)==0o600)
  before=out.read_bytes();record(tool,'json-complete-and-finite',json.loads(before) is not None)
  r=run(args+['--output',str(out)]);record(tool,'no-overwrite',r.returncode==2 and not r.stdout and out.read_bytes()==before)
  r=run(args+['--output',str(src)]);record(tool,'no-input-overwrite',r.returncode==2 and not r.stdout and src.read_bytes()==raw)
  for kind in ['live','dangling']:
   target=d/(tool+'-'+kind+'-target');link=d/(tool+'-'+kind+'-link')
   if kind=='live':target.write_bytes(b'UNMODIFIED')
   link.symlink_to(target);r=run(args+['--output',str(link)]);record(tool,kind+'-symlink-output',r.returncode==2 and link.is_symlink() and (target.read_bytes()==b'UNMODIFIED' if kind=='live' else not target.exists()))
  alias=d/(tool+'-input-link');alias.symlink_to(src);r=run([tool,str(alias),*options,'--stdout']);record(tool,'input-regular-symlink',r.returncode==0)
  fifo=d/(tool+'.fifo');os.mkfifo(fifo);r=run([tool,str(fifo),*options,'--stdout']);record(tool,'nonblocking-fifo-rejection',r.returncode==2 and not r.stdout)
  r=run([tool,str(d),*options,'--stdout']);record(tool,'directory-rejection',r.returncode==2 and not r.stdout)
  r=run(args);record(tool,'stdout-opt-in-required',r.returncode==2 and not r.stdout)
  missing=d/(tool+'-both.json');r=run(args+['--stdout','--output',str(missing)]);record(tool,'destinations-exclusive',r.returncode==2 and not r.stdout and not missing.exists())
  private=b'\xffSECRET_ARGV_SENTINEL';r=subprocess.run([os.fsencode(BIN),tool.encode(),private],capture_output=True,timeout=10);record(tool,'non-UTF8-argv-private',r.returncode==2 and not r.stdout and b'SECRET_ARGV_SENTINEL' not in r.stderr and b'panic' not in r.stderr,r.returncode)
  r=run([tool,'--help']);record(tool,'help-all-tools',r.returncode==0 and tool.encode() in r.stdout and not r.stderr)
  r=run([tool,str(d/'SECRET_PATH'),*options,'--stdout']);record(tool,'missing-path-private',r.returncode==2 and not r.stdout and b'SECRET_PATH' not in r.stderr)
  r=run(args+['--PRIVATE_OPTION=SECRET_VALUE','--stdout']);record(tool,'unknown-option-private',r.returncode==2 and not r.stdout and b'SECRET_VALUE' not in r.stderr and b'PRIVATE_OPTION' not in r.stderr)
  record(tool,'input-unmodified-final',src.read_bytes()==raw)
 # Early bounded malformed input allocation under a child-only256MiB address-space cap.
 def limits():resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
 for tool in ['sweep-segments','iv-grid-audit','iv-loop-integral','bosch-identity-audit','tlm-extract','hall-response','vth-constant-current','transfer-log-slope']:
  _,_,options=TOOLS[tool]
  for name,payload in [('commas',b','*(8*1024*1024)),('blank-lines',b'\n'*(8*1024*1024)),('too-large',b'x'*(8*1024*1024+1))]:
   src=d/(tool+'-'+name);src.write_bytes(payload);r=run([tool,str(src),*options,'--stdout'],preexec_fn=limits);record(tool,'early-resource-'+name,r.returncode==2 and not r.stdout and b'memory allocation' not in r.stderr,r.returncode)
report={'binary_filename':BIN.name,'binary_sha256':hashlib.sha256(BIN.read_bytes()).hexdigest(),'checked':len(checks),'failed':sum(not c['passed'] for c in checks),'checks':checks};(ROOT/'review-safety-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='checks'}));sys.exit(bool(report['failed']))
