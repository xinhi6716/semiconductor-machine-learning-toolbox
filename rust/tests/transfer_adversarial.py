#!/usr/bin/env python3
"""Original deterministic synthetic, actual-CLI differential checks for transfer ports.
No network, private measurements, physical certification, or runtime Python dependency.
"""
from __future__ import annotations
import csv, io, json, math, os, pathlib, random, subprocess, sys, time
from decimal import Decimal, localcontext

ROOT=pathlib.Path(__file__).resolve().parents[1]
REF=pathlib.Path(os.environ.get('SMLT_PYTHON_REFERENCE',str((ROOT/'reference'/'python' if (ROOT/'reference'/'python').is_dir() else ROOT.parent))))
OUT=ROOT/'validation'/'transfer-adversarial';OUT.mkdir(parents=True,exist_ok=True)
BIN=pathlib.Path(os.environ.get('SMLT_BINARY',str(ROOT/'target'/'release'/'smlt')))
HEADER=['lot','wafer','device','condition','replicate','branch','temperature_K','drain_voltage','body_voltage','gate_voltage','drain_current']
rng=random.Random(20261024)
CASES=[]

def rows(gates,currents,device='d',**kw):
    return [[kw.get('lot','l'),kw.get('wafer','w'),device,kw.get('condition','c'),kw.get('replicate','r'),kw.get('branch','up'),kw.get('temperature','300'),kw.get('drain','0.1'),kw.get('body','0'),str(x),str(y)] for x,y in zip(gates,currents)]
def csv_bytes(records,header=HEADER,line='\n'):
    stream=io.StringIO(newline='');writer=csv.writer(stream,lineterminator=line);writer.writerow(header);writer.writerows(records);return stream.getvalue().encode()
def add(name,tool,records,extra=(),raw=None,expected=None):
    args=['--voltage-unit','V','--current-unit','A','--current-polarity','positive']
    if tool=='transfer-log-slope':args+=['--window-min=-1e150','--window-max=1e150','--accept-log-transfer-assumptions']
    else:args+=['--criterion-current','1','--criterion-current-unit','A','--accept-transfer-assumptions']
    CASES.append((name,tool,csv_bytes(records) if raw is None else raw,args+list(extra),expected))

golden=[pair[0] for pair in json.loads((ROOT/'tests'/'transfer_log80_reference.json').read_text())['values']]
for negative in [False,True]:
    records=[]
    for g in range(220):
        n=rng.randint(3,15);gates=[str(i) for i in range(n)];current=rng.choices(golden,k=n)
        if g%11==0: current=[current[0]]*n
        if g%13==0:gates.reverse();current.reverse()
        if negative:current=['-'+v for v in current]
        if g%17==0:current[n//2]='0'
        records+=rows(gates,current,f'random-{g}',branch='down' if g%13==0 else 'up')
    add('random-negative' if negative else 'random-positive','transfer-log-slope',records,extra=['--current-polarity','negative'] if negative else [])

with localcontext() as ctx:
    ctx.prec=200
    gates=[str(Decimal(1)+i*Decimal('1e-31')) for i in range(8)]
    records=[]
    for g in range(40):
        current=['0']+[rng.choice(golden) for _ in range(5)]+['-1','0']
        records+=rows(gates,current,f'window-{g}')
    add('exact-window-and-excluded-domains','transfer-log-slope',records,['--window-min',gates[1],'--window-max',gates[5]])
    records=[]
    for g,base in enumerate([2,3,5,7,9]):
        for direction in [1,-1]:
            for shift in [-120,0,120]:
                current=[f'{base**i}e{shift}' for i in range(10)]
                gates=[str(direction*i) for i in range(10)]
                records+=rows(gates,current,f'log-rounding-{g}-{direction}-{shift}')
    records+=rows(['0','1e-150','2e-150'],['1','2','3'],'tiny-gates')
    records+=rows(['-1e150','0','1e150'],['1','1.0000000000000000000000000000001','1.0000000000000000000000000000002'],'tiny-slope')
    gates=[str(Decimal('9e149')+i*Decimal('1e119')) for i in range(3)]
    records+=rows(gates,['1','2','3'],'huge-offset-small-span')
    add('precision-and-cancellation','transfer-log-slope',records)

for negative in [False,True]:
    records=[]
    for g in range(250):
        n=rng.randint(2,20);gates=list(range(n));currents=[str(Decimal(rng.randint(0,2000))/1000) for _ in range(n)]
        if g%3==0:currents.sort(key=Decimal)
        if g%5==0:currents.sort(key=Decimal,reverse=True)
        if g%11==0:currents[n//2]='1'
        if g%13==0:gates.reverse();currents.reverse()
        if negative:currents=['-'+v for v in currents]
        records+=rows(gates,currents,f'vth-random-{g}')
    add('vth-random-negative' if negative else 'vth-random-positive','vth-constant-current',records,['--current-polarity','negative','--criterion-current=-1'] if negative else [])

normal=rows(['0','.1','.2'],['1','10','100'])
for tool in ['vth-constant-current','transfer-log-slope']:
    add(tool+'-numeric-controls',tool,rows(['\x1c0\x1f','\u00a0.1\u00a0','.2'],['1','10','100']), ['--criterion-current','\x1c1\x1f'] if tool.startswith('vth') else ['--window-min','\x1c0\x1f','--window-max','\u00a0.2\u00a0'])
    add(tool+'-multiline-crlf',tool,rows(['\r\n0','.1','.2'],['1','10','100']),raw=csv_bytes(rows(['\r\n0','.1','.2'],['1','10','100']),line='\r\n'))
    add(tool+'-max-group-count',tool,[r for g in range(1000) for r in rows(['0','.1','.2'],['1','10','100'],f'd{g}')])
    add(tool+'-too-many-groups',tool,[r for g in range(1001) for r in rows(['0'],['1'],f'd{g}')],expected=2)
    add(tool+'-too-many-group-rows',tool,rows(range(1001),['1']*1001),expected=2)
    add(tool+'-too-many-total-rows',tool,[r for g in range(11) for r in rows(range(910),['1']*910,f'd{g}')],expected=2)
    add(tool+'-byte-cap',tool,[],raw=b'X'*(8*1024*1024+1),expected=2)
    add(tool+'-invalid-excluded-number',tool,normal+rows(['.3'],['SECRET_NONFINITE'],branch='excluded'),['--branch','up'] if tool.startswith('vth') else ['--window-min','0','--window-max','.2'],expected=2)
    add(tool+'-nonfinite',tool,rows(['0','.1','.2'],['1','NaN','100']),expected=2)
    add(tool+'-extra-numeric-digits',tool,rows(['0','.1','.2'],['1','1.00000000000000000000000000000001','100']),expected=2)
    add(tool+'-bad-utf8',tool,[],raw=b'\xff',expected=2)
    add(tool+'-header-only',tool,[],expected=2)
    add(tool+'-max-group-rows',tool,rows(range(1000),['1']*1000))


for middle in ['1e-150', '1e26', '1e27']:
    add('derived-range-middle-' + middle, 'transfer-log-slope',
        rows(['-1e150', middle, '1e150'], ['1', '1.0000000000000000000000000000001', '1']))


def diff(a,b,path='$'):
    if isinstance(a,dict) and isinstance(b,dict):
        if set(a)!=set(b):return [path+'.keys',sorted(a),sorted(b)]
        for k in a:
            r=diff(a[k],b[k],path+'.'+k)
            if r:return r
    elif isinstance(a,list) and isinstance(b,list):
        if len(a)!=len(b):return[path+'.length',len(a),len(b)]
        for i,(x,y) in enumerate(zip(a,b)):
            r=diff(x,y,path+f'[{i}]')
            if r:return r
    elif isinstance(a,(int,float)) and isinstance(b,(int,float)) and not isinstance(a,bool) and not isinstance(b,bool):
        if a!=b or (a==0 and math.copysign(1,a)!=math.copysign(1,b)):return[path,a,b]
    elif type(a)!=type(b) or a!=b:return[path,a,b]
    return None

results=[]
for name,tool,payload,args,required_exit in CASES:
    path=OUT/(name+'.csv');path.write_bytes(payload)
    commands=[[sys.executable,str(REF/'tools'/tool/(tool.replace('-','_')+'.py')),str(path),*args,'--stdout'],[str(BIN),tool,str(path),*args,'--stdout']]
    runs=[]
    for command in commands:
        start=time.perf_counter();r=subprocess.run(command,capture_output=True,timeout=180);elapsed=time.perf_counter()-start
        runs.append((r,elapsed))
    (p,pt),(r,rt)=runs;problem=None
    if p.returncode!=r.returncode:problem=['exit',p.returncode,r.returncode]
    if required_exit is not None and r.returncode!=required_exit:problem=['required_exit',required_exit,r.returncode]
    if p.returncode!=2:
        try:problem=problem or diff(json.loads(p.stdout),json.loads(r.stdout))
        except Exception as e:problem=['JSON decode',type(e).__name__]
    elif r.stdout:problem=['fatal_stdout_not_empty']
    if b'SECRET_NONFINITE' in r.stderr:problem=['raw_marker_leaked']
    if problem:
        (OUT/(name+'.python.json')).write_bytes(p.stdout);(OUT/(name+'.rust.json')).write_bytes(r.stdout)
    results.append({'name':name,'tool':tool,'passed':problem is None,'problem':problem,'python_exit':p.returncode,'rust_exit':r.returncode,'python_seconds':pt,'rust_seconds':rt,'input_bytes':len(payload)})
    print(json.dumps(results[-1]),flush=True)
summary={'seed':20261024,'comparison':'exact parsed JSON equality, no floating tolerance; actual Python CLI against actual Rust CLI','cases':len(results),'passed':sum(r['passed'] for r in results),'failed':sum(not r['passed'] for r in results),'results':results}
(OUT/'report.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({k:v for k,v in summary.items()if k!='results'}));sys.exit(bool(summary['failed']))
