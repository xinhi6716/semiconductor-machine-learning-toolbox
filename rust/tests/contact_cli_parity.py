#!/usr/bin/env python3
"""Independent synthetic, real-CLI parity and output safety checks for TLM/Hall.

No private data, no network, no Python runtime dependency in the Rust binary.
Uses strict JSON semantic equality and reports any unequal float; it never
relaxes statuses, warnings, lineage, keys, source bytes, or null/number distinctions.
"""
import argparse, collections, csv, hashlib, io, json, math, os, pathlib, random, stat, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_REFERENCE = (ROOT/'reference'/'python' if (ROOT/'reference'/'python').is_dir() else ROOT.parent)
TOOLS = {
    'tlm-extract': ('tlm_extract.py', ['lot','wafer','structure','condition','replicate','pair','spacing','width','resistance'], ['--length-unit','m','--resistance-unit','ohm','--accept-linear-tlm-assumptions']),
    'hall-response': ('hall_response.py', ['lot','wafer','device','condition','replicate','branch','temperature_K','bias_current','magnetic_field','transverse_voltage'], ['--field-unit','T','--voltage-unit','V','--current-unit','A','--accept-hall-assumptions']),
}

def csv_bytes(header, records, *, reverse=False, bom=False, line='\n', blanks=False):
    stream=io.StringIO(newline=''); writer=csv.writer(stream,lineterminator=line)
    writer.writerow(header[::-1] if reverse else header)
    for row in records:
        if blanks: writer.writerow([])
        writer.writerow(row[::-1] if reverse else row)
    return ('\ufeff' if bom else '').encode() + stream.getvalue().encode()

def differences(a,b,path='$'):
    if isinstance(a,bool) or isinstance(b,bool): return [] if a is b else [(path,a,b)]
    if isinstance(a,(float,int)) and isinstance(b,(float,int)):
        return [] if a==b and (a!=0 or math.copysign(1,a)==math.copysign(1,b)) else [(path,a,b)]
    if type(a)!=type(b): return [(path,a,b)]
    if isinstance(a,dict):
        if set(a)!=set(b): return [(path+'.keys',sorted(a),sorted(b))]
        return [d for k in a for d in differences(a[k],b[k],path+'.'+k)]
    if isinstance(a,list):
        if len(a)!=len(b): return [(path+'.length',len(a),len(b))]
        return [d for i,(x,y) in enumerate(zip(a,b)) for d in differences(x,y,f'{path}[{i}]')]
    return [] if a==b else [(path,a,b)]

def fixtures(tool, count):
    rng=random.Random(982491 if tool=='tlm-extract' else 1344997)
    _,header,_=TOOLS[tool]
    for k in range(count):
        n=rng.randrange(3,11); exponent=rng.choice([-130,-80,-20,-2,0,1,5,20,80,130])
        scale=lambda v: f'{v}e{exponent}'
        if tool=='tlm-extract':
            x=rng.sample(range(1,900),n); y=[rng.randrange(1,900) for _ in x]
            # Half the cases are useful fits; others cover arbitrary positive data.
            if k%2: y=[v*rng.randrange(1,3)+1000 for v in x]
            width=f'{rng.randrange(1,90)}e{rng.choice([-150,-80,-3,0,3,80,149])}'
            records=[['synthetic-lot','w','s','c','r',f'p{i}',scale(v),width,scale(z)] for i,(v,z) in enumerate(zip(x,y))]
            if k%11==0: records[1][6]=records[0][6]
            if k%13==0: records[1][5]=records[0][5]
            if k%17==0: records[1][7]='-1'
            if k%19==0: records[1][8]='0'
            if k%23==0: records[1][7]='2'
        else:
            x=sorted(rng.sample(range(-900,900),n),reverse=bool(k%2)); y=[rng.randrange(-900,900) for _ in x]
            current=f'{rng.choice([-1,1])*rng.randrange(1,90)}e{rng.choice([-149,-80,-3,0,3,80,149])}'
            records=[['synthetic-lot','w','d','c','r','branch','300',current,scale(v),scale(z)] for v,z in zip(x,y)]
            if k%11==0: records[1][8]=records[0][8]
            if k%13==0: records[1][8],records[2][8]=records[2][8],records[1][8]
            if k%17==0: records[1][6]='0'
            if k%19==0: records[1][7]='0'
            if k%23==0:
                for row in records: row[7]='0'
        yield f'random-{k}',csv_bytes(header,records,reverse=k%7==0,bom=k%9==0,line='\r\n' if k%3==0 else '\n',blanks=k%5==0)
    records=([['l','w','s','c','r',f'p{i}',str(i),'1',str(2*i+1)] for i in range(1,4)] if tool=='tlm-extract' else
             [['l','w','d','c','r','b','300','-1',str(i),str(2*i+1)] for i in range(-1,2)])
    yield 'ordinary',csv_bytes(header,records)
    for whitespace in [' ', '\t', '\n', '\r', '\r\n', '\u00a0', '\u2003', '\x1c', '\x1d', '\x1e', '\x1f']:
        decorated=[row[:-1]+[whitespace+row[-1]+whitespace] for row in records]
        yield f'numeric-whitespace-{ord(whitespace[0])}',csv_bytes(header,decorated)
    for illegal in ['NaN','inf','-Infinity','0e-201','1e151','1e-151','1e00000','9'*65,'1.123456789012345678901234567890123','0x10','secret-number']:
        modified=[row[:] for row in records];modified[1][-1]=illegal
        yield 'invalid-number-'+illegal,csv_bytes(header,modified)
    for invalid in ['', ' padded', 'padded ', 'a\x00b', 'a\x7fb', 'x'*129]:
        modified=[row[:] for row in records];modified[1][0]=invalid
        yield 'invalid-identifier-'+repr(invalid),csv_bytes(header,modified)
    for newline in ['\r','\r\n','\n']:
        yield 'line-ending-'+repr(newline),csv_bytes(header,records,line=newline)
    yield 'invalid-utf8', b'\xff'
    yield 'unclosed-quote', csv_bytes(header,records)+b'"'
    yield 'empty-input',b''
    yield 'header-only',csv_bytes(header,[])
    yield 'trailing-no-newline',csv_bytes(header,records).rstrip(b'\n')
    yield 'literal-unquoted-quote',csv_bytes(header,records).replace(b'l,w,',b'l"x,w,')
    # Production limits, not monkeypatched API-only caps.
    per_group=[records[0][:] for _ in range(1001)]
    yield 'production-group-row-limit',csv_bytes(header,per_group)
    yield 'production-group-row-boundary',csv_bytes(header,per_group[:1000])
    many_groups=[records[0][:] for _ in range(1001)]
    for i,row in enumerate(many_groups): row[0]=f'synthetic-lot-{i}'
    yield 'production-group-count-limit',csv_bytes(header,many_groups)
    yield 'production-group-count-boundary',csv_bytes(header,many_groups[:1000])
    total_rows=[records[0][:] for _ in range(10001)]
    for i,row in enumerate(total_rows): row[0]=f'synthetic-lot-{i//1000}'
    yield 'production-total-row-limit',csv_bytes(header,total_rows)
    yield 'production-total-row-boundary',csv_bytes(header,total_rows[:10000])
    for space in [' ', '\u00a0']:
        for size in [131072,131073]:
            padded=[row[:] for row in records]
            padded[0][-1]=space*(size-len(padded[0][-1]))+padded[0][-1]
            yield f'production-csv-field-size-{ord(space)}-{size}',csv_bytes(header,padded)
    yield 'production-file-byte-limit',b' '*(8*1024*1024+1)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--binary',type=pathlib.Path,default=ROOT/'target'/'release'/'smlt')
    parser.add_argument('--reference',type=pathlib.Path,default=DEFAULT_REFERENCE);parser.add_argument('--random-cases',type=int,default=100)
    args=parser.parse_args();failures=[];counts=collections.Counter();safety=[]
    with tempfile.TemporaryDirectory(prefix='synthetic-contact-parity-') as tmp:
        directory=pathlib.Path(tmp);path=directory/'input.csv'
        for tool,(script,header,options) in TOOLS.items():
            reference=[sys.executable,str(args.reference/'tools'/tool/script)]
            native=[str(args.binary),tool]
            def invoke(prefix,flags=None,input_path=None):
                return subprocess.run([*prefix,str(input_path or path),*options,*(['--stdout'] if flags is None else flags)],capture_output=True,timeout=30)
            for name,raw in fixtures(tool,args.random_cases):
                path.write_bytes(raw);before=hashlib.sha256(raw).hexdigest()
                py=invoke(reference);rs=invoke(native);counts[tool]+=1
                issues=[]
                if py.returncode!=rs.returncode: issues.append(('exit',py.returncode,rs.returncode))
                if py.returncode in (0,1) and rs.returncode in (0,1):
                    issues += differences(json.loads(py.stdout),json.loads(rs.stdout))
                elif rs.stdout: issues.append(('unexpected_stdout',len(rs.stdout),0))
                if rs.returncode==2 and any(secret in rs.stderr for secret in [b'secret-number',str(path).encode(),b'Traceback',b'panicked']):
                    issues.append(('unsafe_stderr',rs.stderr.decode(errors='replace'),None))
                if hashlib.sha256(path.read_bytes()).hexdigest()!=before: issues.append(('input_mutated',True,False))
                if issues:
                    failures.append({'tool':tool,'fixture':name,'differences':issues[:12]})
            ordinary=next(raw for name,raw in fixtures(tool,0) if name=='ordinary');path.write_bytes(ordinary)
            output=directory/(tool+'.json');result=invoke(native,['--output',str(output)])
            checks={'tool':tool,'exclusive_create':result.returncode==0 and not result.stdout and output.is_file()}
            before=output.read_bytes();checks['owner_only']=stat.S_IMODE(output.stat().st_mode)==0o600
            checks['no_overwrite']=invoke(native,['--output',str(output)]).returncode==2 and output.read_bytes()==before
            checks['input_not_overwritten']=invoke(native,['--output',str(path)]).returncode==2 and path.read_bytes()==ordinary
            for existing in (True,False):
                target=directory/(tool+('-exists' if existing else '-absent'));link=directory/(tool+('-live-link' if existing else '-dangling-link'))
                if existing:target.write_text('unchanged')
                link.symlink_to(target)
                checks['live_symlink' if existing else 'dangling_symlink']=invoke(native,['--output',str(link)]).returncode==2 and link.is_symlink() and (target.read_text()=='unchanged' if existing else not target.exists())
            fifo=directory/(tool+'.fifo');os.mkfifo(fifo)
            checks['fifo_rejected']=invoke(native,input_path=fifo).returncode==2
            checks['missing_destination']=invoke(native,[]).returncode==2
            checks['both_destinations']=invoke(native,['--stdout','--output',str(directory/'both.json')]).returncode==2
            for flag in ['--help','--version']:
                r=subprocess.run([*native,flag],capture_output=True,timeout=30)
                checks[flag]=r.returncode==0 and not r.stderr
            safety.append(checks)
            for key,value in checks.items():
                if value is False:failures.append({'tool':tool,'fixture':'safety-'+key})
    report={'description':'Original synthetic input, actual Python/Rust CLI differential, exact parsed JSON values and ordered decisions/lineage',
            'per_tool':dict(counts),'checked':sum(counts.values()),'failures':failures,'failed':len(failures),'safety_checks':safety,
            'binary_sha256':hashlib.sha256(args.binary.read_bytes()).hexdigest()}
    destination=ROOT/'validation'/'contact-cli-parity.json';destination.parent.mkdir(parents=True,exist_ok=True);destination.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2));return bool(failures)
if __name__=='__main__':sys.exit(main())
