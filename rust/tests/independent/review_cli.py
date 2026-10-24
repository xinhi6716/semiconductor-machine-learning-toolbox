#!/usr/bin/env python3
"""Independent actual-CLI cross-check; source unchanged; synthetic inputs only."""
import csv,io,json,math,os,pathlib,random,struct,subprocess,sys,time,hashlib
PROJECT=pathlib.Path(os.environ.get('SMLT_PROJECT_ROOT',pathlib.Path.cwd())).resolve()
ROOT=pathlib.Path(os.environ.get('SMLT_REVIEW_OUTPUT',PROJECT/'validation/independent')).resolve();ROOT.mkdir(parents=True,exist_ok=True)
BASE=PROJECT.parent
REF=pathlib.Path(os.environ.get('SMLT_PYTHON_REFERENCE',PROJECT/'reference/python')).resolve()/'tools'
BIN=pathlib.Path(os.environ.get('SMLT_BINARY',PROJECT/'target/release/smlt')).resolve();RNG=random.Random(9724321)
(ROOT/'inputs').mkdir(exist_ok=True); (ROOT/'failures').mkdir(exist_ok=True)
CASES=[]
HEAD={
'sweep-segments':'lot,wafer,device,condition,sequence,sample_id,coordinate',
'iv-grid-audit':'lot,wafer,device,condition,sweep,branch,sample_id,voltage',
'iv-readpoint':'device,cycle,branch,voltage,current',
'iv-loop-integral':'lot,wafer,device,condition,cycle,replicate,sample_id,branch_to_next,voltage,current',
'rram-window':'device,cycle,state,read_condition,read_voltage,resistance',
'bosch-identity-audit':'experiment_key,lot_number,wafer_number,loc_id,X,Y,preox_thickness,postox_thickness,stepheight,oxide_etch,si_etch'}
ARGS={'sweep-segments':['--axis','voltage','--coordinate-unit','V'],'iv-grid-audit':['--voltage-unit','V'],'iv-readpoint':['--voltage-unit','V','--current-unit','A','--read-voltage','1','--read-voltage-unit','V'],'iv-loop-integral':['--voltage-unit','V','--current-unit','A','--accept-supplied-path-assumptions'],'rram-window':['--voltage-unit','V','--resistance-unit','ohm','--hrs-label','H','--lrs-label','L']}
labels=['ch'+str(i) for i in range(31)];metadata={'schema_version':'bosch-channel-metadata-v1','process_file_sha256':'0'*64,'canonical_labels':labels,'groups':[{'group_path':'/Day_2024_02_29_Wafer_01','feature_labels':labels}]};mp=ROOT/'metadata.json';mp.write_text(json.dumps(metadata));ARGS['bosch-identity-audit']=['--channel-metadata',str(mp)]
def rawcsv(tool,rows,header=None,ending='\n'):
 s=io.StringIO(newline='');w=csv.writer(s,lineterminator=ending);w.writerow((header or HEAD[tool]).split(','));w.writerows(rows);return s.getvalue().encode()
def add(tool,name,rows=None,raw=None,args=None,expected=None):
 CASES.append((tool,name,raw if raw is not None else rawcsv(tool,rows),ARGS[tool]+(args or []),expected))
def diff(a,b,p='$'):
 if isinstance(a,bool) or isinstance(b,bool):
  return None if a is b else [p,a,b,'boolean/type mismatch']
 if isinstance(a,dict) and isinstance(b,dict):
  if set(a)!=set(b):return[p+'.keys',sorted(a),sorted(b)]
  for k in a:
   d=diff(a[k],b[k],p+'.'+k)
   if d:return d
 elif isinstance(a,list) and isinstance(b,list):
  if len(a)!=len(b):return[p+'.length',len(a),len(b)]
  for i,(x,y) in enumerate(zip(a,b)):
   d=diff(x,y,p+f'[{i}]')
   if d:return d
 elif isinstance(a,float) and isinstance(b,float):
  if struct.pack('!d',a)!=struct.pack('!d',b):return[p,a,b,'binary64 mismatch']
 elif a!=b:return[p,a,b]
 return None
# Exact-decimal ordering, chronology, duplicate IDs and plateau transitions.
for k in range(45):
 coords=[str(RNG.randint(-8,8))+('0' if k%7==0 else '') for _ in range(RNG.randint(1,60))]
 if k%5==0:coords=['1.000000000000000000000000000000'+str(i%10) for i in range(len(coords))]
 rows=[['l','w','d','c','s',str(i),v] for i,v in enumerate(coords)]
 add('sweep-segments',f'random-{k}',rows)
for value in ['-0','0e-200','1e200','1e201','1e-201','1e0000','1e00000',' 1','1\n','1.0000000000000000000000000000000','1.00000000000000000000000000000000','秘密','NaN','1_0','0x10']:
 add('sweep-segments','numeric-'+str(len(CASES)),[['l','w','d','c','s','0',value],['l','w','d','c','s','1','1']])
# Mixed logical groups, non-ASCII identity, exact grid near-collisions, opaque invalid UTF-8/NUL.
for k in range(35):
 rows=[]
 for g in range(RNG.randint(1,15)):
  for i in range(RNG.randint(1,15)):
   v=RNG.choice(['0','-0','1','1.00','1e0','1.0000000000000000000000000000001','-1e-200','1e200'])
   rows.append(['l','w',f'd{g}','c'+str(g%3),'s','b',str(i),v])
 RNG.shuffle(rows);add('iv-grid-audit',f'random-{k}',rows)
for byte in [b'\xff',b'\x00',b'NaN',b'"a\rb\nc"',b'"secret,\xff,more"']:
 add('iv-grid-audit',f'opaque-{len(CASES)}',raw=(HEAD['iv-grid-audit']+',response\n').encode()+b'l,w,d,c,s,b,0,1,'+byte+b'\n')
# Floating I-V readpoint including signed-zero and binary64 tie/subnormal boundaries.
for k in range(65):
 exp=RNG.randint(-290,290);scale=10.0**exp;n=RNG.randint(2,14);xs=sorted(RNG.sample(range(-50,51),n));ys=[f'{RNG.randint(-99,99)}e{exp}' for _ in xs];target=RNG.choice(xs) if k%3==0 else RNG.uniform(xs[0],xs[-1]);target=target or 0.5
 if k%4==0:xs.reverse();ys.reverse()
 add('iv-readpoint',f'random-{k}',[['d','1','b',x,y] for x,y in zip(xs,ys)],args=['--read-voltage',str(target)])
for value in ['-0','-0e20001','0e20001','0e-999999','0e99999999999999999999999999','2.4703282292062327e-324','2.4703282292062328e-324','4.9406564584124654e-324','1.7976931348623157e308','1.7976931348623159e308','1.00000000000000011102230246251565404236316680908203125']:
 add('iv-readpoint',f'number-{len(CASES)}',[['d','1','b','1',value]])
# Closed-loop exact arithmetic, cancellation, nonclosed supplied path and branch audits.
for k in range(45):
 xs=[0]+sorted(RNG.sample(range(1,100),RNG.randint(1,20)))+[0];ex=RNG.randint(-140,140);ys=[f'{RNG.randint(-30,30)}e{ex}' for _ in xs]
 if k%5==0:xs[-1]=1
 rows=[['l','w','d','c','1','r',str(i),'up' if i<len(xs)-2 else ('down' if i<len(xs)-1 else ''),x,y] for i,(x,y) in enumerate(zip(xs,ys))]
 add('iv-loop-integral',f'random-{k}',rows)
# RRAM ratio summary exact fsum behavior and decimal spelling.
for k in range(40):
 rows=[]
 for c in range(RNG.randint(1,55)):
  ex=RNG.randint(-140,140);r1=f'{RNG.randint(1,9999999)}e{ex}';r2=f'{RNG.randint(1,9999999)}e{RNG.randint(-140,140)}';v=RNG.choice(['.1','0.10','1e-1','-0.1'])
  rows.extend([['d',str(c),'H','read',v,r1],['d',str(c),'L','read',v,r2]])
 RNG.shuffle(rows);add('rram-window',f'random-{k}',rows)
for value in ['-0','0e20001','0e-20001','1e99999999999999999999999999','-1e99999999999999999999999999','1e20001','1e-20001','0.0000000','100.00','000.0000']:
 add('rram-window',f'number-{len(CASES)}',[['d','1','H','read',value,'10'],['d','1','L','read',value,'1']])
# BOSCH response bytes remain opaque, identity exact text/null/duplicates/date candidates.
for k in range(20):
 rows=[]
 for i in range(RNG.randint(1,35)):
  rows.append([RNG.choice(['2024-02-29_01','2023-02-29_01','2024-02-29_00','2024-02-29_99','']),RNG.choice(['0','00',' lot ','批次','']),RNG.choice(['1','01','99','']),RNG.choice(['','1','0','01']),*[str(RNG.random())]*7])
 add('bosch-identity-audit',f'random-{k}',rows)
for byte in [b'\xff',b'\x00',b'NaN',b'"a\rb\nc"',b'"secret,\xff,more"']:
 add('bosch-identity-audit',f'opaque-{len(CASES)}',raw=HEAD['bosch-identity-audit'].encode()+b'\n2024-02-29_01,l,1,0,'+byte+b',x,x,x,x,x,x\n')
# True record-boundary tests not monkeypatched constants.
for tool in ['bosch-identity-audit','iv-grid-audit']:
 prefix=b'2024-02-29_01,l,1,0,' if tool.startswith('bosch') else b'l,w,d,c,s,b,0,1,';suffix=b',x,x,x,x,x,x' if tool.startswith('bosch') else b''
 header=HEAD[tool]+('' if tool.startswith('bosch') else ',response')
 for bodylength in [65534,65535,65536,65537]:
  for newline in [b'',b'\n',b'\r\n']:
   raw=header.encode()+b'\n'+prefix+b'x'*(bodylength-len(prefix)-len(suffix))+suffix+newline
   add(tool,f'record-{bodylength}-nl{len(newline)}',raw=raw)
# Independent mixed-group scientific cases across the four remaining tools.
HEAD.update({'tlm-extract':'lot,wafer,structure,condition,replicate,pair,spacing,width,resistance','hall-response':'lot,wafer,device,condition,replicate,branch,temperature_K,bias_current,magnetic_field,transverse_voltage'})
HEAD['vth-constant-current']=HEAD['transfer-log-slope']='lot,wafer,device,condition,replicate,branch,temperature_K,drain_voltage,body_voltage,gate_voltage,drain_current'
ARGS.update({'tlm-extract':['--length-unit','m','--resistance-unit','ohm','--accept-linear-tlm-assumptions'],'hall-response':['--field-unit','T','--voltage-unit','V','--current-unit','A','--accept-hall-assumptions'],'vth-constant-current':['--voltage-unit','V','--current-unit','A','--current-polarity','positive','--criterion-current','1','--criterion-current-unit','A','--accept-transfer-assumptions'],'transfer-log-slope':['--voltage-unit','V','--current-unit','A','--current-polarity','positive','--window-min=-1e150','--window-max=1e150','--accept-log-transfer-assumptions']})
for k in range(20):
 for tool in ['tlm-extract','hall-response','vth-constant-current','transfer-log-slope']:
  rows=[]
  for g in range(15):
   n=RNG.randint(3,12);xs=sorted(RNG.sample(range(-100,100),n));scale=RNG.choice([-140,-90,-20,0,20,90,140]);ys=[f'{RNG.randint(1,999)}e{scale}' for _ in xs]
   if tool=='tlm-extract':
    rows.extend([['l','w',str(g),'c','r',str(i),str(x+101),f'1e{scale}',y] for i,(x,y) in enumerate(zip(xs,ys))])
   elif tool=='hall-response':
    rows.extend([['l','w',str(g),'c','r','b','300','0' if g%5==0 else f'1e{-scale}',str(x),y] for x,y in zip(xs,ys)])
   else:
    if k%3==0:ys=sorted(ys,key=float)
    if g%5==0:ys[n//2]='1'
    if k%7==0:ys[n//2]='0'
    rows.extend([['l','w',str(g),'c','r','b','300','.1','0',str(x),y] for x,y in zip(xs,ys)])
  add(tool,f'independent-mixed-{k}',rows)

# Deliberate valid-input halfway binary64 adversaries exposed default-decimal-precision drift.
add('hall-response','actual-rmse-midpoint',[
    ['l','w','d','c','r','b','300','1','0','0'],
    ['l','w','d','c','r','b','300','1','4.9955899751546791257705801610827E-32','2.4494897427831783701452751768095'],
    ['l','w','d','c','r','b','300','1','1','0']])
add('tlm-extract','actual-rmse-midpoint',[
    ['l','w','s','c','r','p0','4.9955899751546791257705801610825E-32','1','1'],
    ['l','w','s','c','r','p1','9.9911799503093582515411603221650E-32','1','3.4494897427831783701452751768095'],
    ['l','w','s','c','r','p2','1','1','1']])

# Decimal constructor, sign-before-range and zero exponent boundaries.
for value in ['0e999999999999999999','0e1000000000000000000','0e9223372036854775807','0e-1999999999999999997','0e-1999999999999999998','0e-9223372036854775807']:
    add('iv-readpoint',f'zero-exponent-{len(CASES)}',[['d','1','b','1',value]])
for value in ['-1e20001','-1e-20001','-1e999999999999999999','-1e-1999999999999999997','0e999999999999999999']:
    for unit in ['ohm','Mohm']:
        add('rram-window',f'negative-extreme-{len(CASES)}',[['d','1','H','read','1',value],['d','1','L','read','1','1']],args=['--resistance-unit',unit])

# Real production output caps with bounded synthetic Unicode expansion, no monkeypatch.
gridrows=[['l','w','d','c','s','b','🧪'*124+f'{i:04d}','0'*64] for i in range(10000)]
add('iv-grid-audit','real-16MiB-report-cap',gridrows,expected=2)
caplabels=['é'*126+f'{i:04d}' for i in range(44)]
capmeta={'schema_version':'bosch-channel-metadata-v1','process_file_sha256':'0'*64,'canonical_labels':caplabels[:31],'groups':[{'group_path':f'/Day_2024_01_{1+i//99:02d}_Wafer_{1+i%99:02d}','feature_labels':caplabels} for i in range(128)]}
capmp=ROOT/'metadata-report-cap.json';capmp.write_text(json.dumps(capmeta,ensure_ascii=False))
caprows=[['é'*126+f'{i:04d}']*4+['opaque']*7 for i in range(2000)]
add('bosch-identity-audit','real-16MiB-report-cap',caprows,args=['--channel-metadata',str(capmp)],expected=2)

# Valid exact-byte-cap boundaries; +1 remains valid CSV except for total byte limit.
for tool in ['iv-grid-audit','bosch-identity-audit']:
    header=(HEAD[tool]+(',response' if tool=='iv-grid-audit' else '')+'\n').encode()
    data=bytearray(header);i=0
    while len(data)<8*1024*1024:
        prefix=(f'l,w,d,c,s,b,{i},1,' if tool=='iv-grid-audit' else f'2024-02-29_01,l,1,{i},').encode()
        suffix=b'\n' if tool=='iv-grid-audit' else b',x,x,x,x,x,x\n'
        size=min(60000,8*1024*1024-len(data))
        assert size>=len(prefix)+len(suffix)
        data.extend(prefix+b'x'*(size-len(prefix)-len(suffix))+suffix);i+=1
    add(tool,'valid-exact-8MiB',raw=bytes(data),expected=1 if tool=='iv-grid-audit' else 0)
    add(tool,'valid-8MiB-plus-one',raw=bytes(data[:-1])+b'x\n',expected=2)

# Core stdout privacy, malformed framing and BOM contracts per tool.
for tool in HEAD:
 for name,raw in [('empty',b''),('bad-utf8-header',b'\xff\n'),('unclosed',HEAD[tool].encode()+b'\n"secret\n'),('header-only',HEAD[tool].encode()+b'\n')]:add(tool,name,raw=raw,expected=2)

def main():
 out=[];start=time.time();binary_hash=hashlib.sha256(BIN.read_bytes()).hexdigest()
 for i,(tool,name,raw,args,expect) in enumerate(CASES):
  p=ROOT/'inputs'/f'{i:04d}-{tool}-{name}.csv';p.write_bytes(raw);runs=[]
  for cmd in [[sys.executable,str(REF/tool/(tool.replace('-','_')+'.py'))],[str(BIN),tool]]:
   t=time.monotonic()
   try:r=subprocess.run(cmd+[str(p)]+args+['--stdout'],capture_output=True,timeout=40);runs.append((r,time.monotonic()-t))
   except subprocess.TimeoutExpired:runs.append((None,time.monotonic()-t))
  (pyr,pt),(rs,rt)=runs;problem=None
  if pyr is None or rs is None:problem=['timeout',pyr is None,rs is None]
  elif pyr.returncode!=rs.returncode:problem=['exit',pyr.returncode,rs.returncode]
  elif expect is not None and rs.returncode!=expect:problem=['expected_exit',expect,rs.returncode]
  elif pyr.returncode!=2:
   try:problem=diff(json.loads(pyr.stdout),json.loads(rs.stdout))
   except Exception as e:problem=['json',str(e)]
  elif rs.stdout:problem=['fatal stdout not empty']
  if rs is not None and b'secret' in rs.stderr:problem=['stderr leak',rs.stderr.decode(errors='replace')]
  item={'index':i,'tool':tool,'name':name,'passed':problem is None,'problem':problem,'python_exit':None if pyr is None else pyr.returncode,'rust_exit':None if rs is None else rs.returncode,'python_seconds':pt,'rust_seconds':rt,'input_sha256':hashlib.sha256(raw).hexdigest()};out.append(item)
  if problem:
   print(json.dumps(item),flush=True)
   for suffix,r in [('python',pyr),('rust',rs)]:
    if r is not None:(ROOT/'failures'/f'{i:04d}-{suffix}.stdout').write_bytes(r.stdout);(ROOT/'failures'/f'{i:04d}-{suffix}.stderr').write_bytes(r.stderr)
 report={'binary_filename':BIN.name,'binary_sha256':binary_hash,'comparison':'Actual Python and Rust CLIs; exact parsed JSON plus binary64 sign-bit equality; no tolerance','seed':9724321,'count':len(out),'failed':sum(not r['passed'] for r in out),'elapsed_seconds':time.time()-start,'results':out};(ROOT/'review-cli-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='results'}));return bool(report['failed'])
if __name__=='__main__':sys.exit(main())
