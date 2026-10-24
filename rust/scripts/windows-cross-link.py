#!/usr/bin/env python3
"""Optional Linux -> Windows build-time adapter; never invoked by smlt.exe.
Requires official Zig 0.12.1 extracted at $SMLT_ZIG_ROOT and Python3.
Select Rust's compiler_builtins rather than adding duplicate Zig compiler-rt;
link Zig's Windows CRT and LLVM unwind implementation explicitly.
"""
import os, pathlib, subprocess, sys, tempfile
zig_root=pathlib.Path(os.environ['SMLT_ZIG_ROOT']).resolve()
os.environ.setdefault('ZIG_GLOBAL_CACHE_DIR',str(pathlib.Path(__file__).resolve().parents[1]/'.build-cache'/'zig'))
args=sys.argv[1:]; output=None; objects=[]; libraries=[]; directories=[]
with tempfile.TemporaryDirectory(prefix='smlt-windows-link-') as temporary:
    index=0
    while index<len(args):
        arg=args[index]
        if arg=='-o':
            index+=1;output=args[index]
        elif arg.endswith('.rlib'):
            archive=pathlib.Path(temporary)/(pathlib.Path(arg).name+'.a')
            archive.symlink_to(pathlib.Path(arg).resolve());objects.append(str(archive))
        elif arg.endswith(('.o','.a','.lib','.obj')) and not arg.startswith('-'):
            objects.append(arg)
        elif arg.startswith('-l') and arg not in ['-lgcc','-lgcc_eh','-l:libpthread.a','-lmsvcrt','-lmingwex','-lmingw32']:
            libraries.append(arg)
        elif arg.startswith('-L'):
            directories.append(arg)
        index+=1
    if output is None:raise SystemExit('Missing compiler output destination')
    command=[str(zig_root/'zig'),'build-exe','-target','x86_64-windows-gnu',
             '-fno-compiler-rt','-lc','-lunwind','-O','ReleaseFast','-fstrip',
             '-femit-bin='+output,*objects,*directories,*libraries]
    raise SystemExit(subprocess.call(command))
