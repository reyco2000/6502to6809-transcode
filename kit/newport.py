"""Scaffold a new port:  python3 -m kit.newport NAME [--dir DIR] [--disk DSK] [--rom ROM] [--prom PROM]

Creates DIR (default ~/proyectos/NAME-coco3) holding:
  run.sh                 ./run.sh <module> [args]  = python3 -m NAME.<module> with the kit on PYTHONPATH
  NAME/port.toml         paths (disk, ROMs, build dir) and the default RAM target
  NAME/spec.py           the port spec: images, natives, SMC, code ranges, build hooks (docs/SPEC.md)
  NAME/snapshots.py      when to take each image's snapshot (boot hand-over, level loads)
  NAME/gen.py build512.py build128.py test_engine.py    thin wrappers around the kit
  NAME/src/natives.asm   the port's native routines (start from rt/natives/apple2/*)
  NAME/src/t512/start.asm, NAME/src/t128/start.asm      game start, level loader glue, reboot
  NAME/scenarios/play.json   host-Apple profile scenario (kit/host/profile.py) for the coverage + survey
  NAME/mame/play.txt     MAME input script (kit/mame/input.lua)
Nothing derived from the game is written into the kit or the port source: build/ only.
"""
import argparse
import os
import stat

from .paths import REPO

FILES = {}

FILES['run.sh'] = '''#!/bin/sh
# ./run.sh MODULE [ARGS]: python3 -m {name}.MODULE with the 6502to6809 kit on the path.
#   ./run.sh snapshots          host-Apple snapshots of every image   -> build/snap
#   ./run.sh profile            coverage profile of the scenarios     -> build/coverage.json
#   ./run.sh survey             what to transcode / replace            -> build/survey.txt, build/draft_spec.py
#   ./run.sh gen                generate the 6809 source              -> build/src
#   ./run.sh test_engine -n 300 differential test of the generated code
#   ./run.sh build512 | build128                                      -> build/{name}_512k.dsk / _128k.dsk
KIT={kit}
HERE=$(cd "$(dirname "$0")" && pwd)
export PYTHONPATH="$KIT:$HERE${{PYTHONPATH:+:$PYTHONPATH}}"
cd "$HERE"
case "$1" in
  profile) shift; mkdir -p build
           exec python3 -m kit.host.profile "$(python3 -c 'from kit import portcfg; p=portcfg.load("{name}"); print(p.disk)')" \\
                --rom "$(python3 -c 'from kit import portcfg; print(portcfg.load("{name}").rom)')" \\
                --prom "$(python3 -c 'from kit import portcfg; print(portcfg.load("{name}").prom)')" \\
                --scenario {name}/scenarios/play.json --coverage build/coverage.json --out build/profile "$@" ;;
  survey)  shift; mkdir -p build
           python3 -m kit.survey {name} --spec-out build/draft_spec.py "$@" | tee build/survey.txt ;;
  *)       m=$1; shift; exec python3 -m {name}.$m "$@" ;;
esac
'''

FILES['NAME/__init__.py'] = ''

FILES['NAME/port.toml'] = '''# {name}: CoCo 3 port by static 6502 -> 6809 transcoding (6502to6809 kit).
# Inputs are the user's files; nothing derived from them is committed (build/ is ignored).
[port]
name     = "{name}"
disk     = "{disk}"
rom      = "{rom}"
prom     = "{prom}"
build    = "../build"
coverage = "../build/coverage.json"
ram      = 512
loader   = "{loader}"
'''

FILES['NAME/spec.py'] = '''"""{name} transcoding spec (addresses only, no game bytes).  See the kit's docs/SPEC.md and docs/DECIDE.md.

Fill it in this order: IMAGES/STAGES (snapshots.py), then run ./run.sh profile and ./run.sh survey, and move the
candidates of build/draft_spec.py you confirm (against a disassembly) into NATIVE / NATIVE_RANGES / SMC_*.
"""
import os
import sys

from kit import portcfg, specbase
from kit.xc65 import Native, FLAGS, ALL, A_, X_, Y_  # noqa: F401
from kit.hw import Idiom, Ignore, Latch, Runtime  # noqa: F401

PORT = portcfg.load(os.path.dirname(os.path.abspath(__file__)))
SNAP, COVERAGE = PORT.snap, PORT.coverage

# image name -> snapshot file stem (snapshots.py makes snap_<name>.bin/.json[/.mask])
BASE = 'cold'                       # the image the boot hands over to (engine = code shared by all images)
STAGES = []                         # overlay images (levels / events loaded from disk), in loader-number order
IMAGES = {{n: 'snap_' + n for n in [BASE] + STAGES}}

# replaced code: 6502 entry -> native symbol (src/natives.asm); any other jump into a replaced range is an error
NATIVE = {{}}
# (lo, hi) inclusive: the bodies of the replaced routines (never translated)
NATIVE_RANGES = []
# translated addresses a native continues into (jmp E<addr>): extra roots in every image
NATIVE_EXITS = []
# accepted hardware idioms: copy them from build/draft_spec.py (survey section 7) once checked; kit/hw/lib.py
HW_IDIOMS = []
# opcode SMC bytes (differ between images but belong to shared code; handled by natives)
SMC_BYTES = []
# operand SMC sites: the translated instruction reads its operand from memory
SMC_IMM = []
# where translated code may live (not zero page / stack / hi-res pages)
CODE_RANGES = [(0x0200, 0x1FFF), (0x6000, 0xBFFF)]

# ---- generator hooks (kit/gen.py)
IO_MEM = {{0xC000: 'kb_kbd', 0xC061: 'btn0', 0xC062: 'btn1'}}   # I/O reads = plain loads of runtime latches
HW_OVERRIDES = {{}}                   # {{addr: Ignore() | Latch(sym) | Runtime()}}: accept a trapped switch (kit/hw/apple2.py)
ROMCONST = {{}}                       # ROM bytes the game reads as data, e.g. {{0xFBB3: 0x06}} (IIe ID)
# natives with no 6502 body (ROM entries) or that never return; the others get their contract from the 6502 code
NATIVE_CONTRACTS = {{
    # 'nt_wait': Native('nt_wait', use=A_, defs=A_ | FLAGS),           # monitor WAIT ($FCA8)
    # 'nt_load': Native('nt_load', use=0, defs=ALL, noreturn=True),    # the game's level loader
}}

# ---- build hooks (kit/build512.py, kit/build128.py)
START_LABEL = 'game_start'          # src/t*/start.asm
BLOCKB_RESERVE = 0                  # 128K: bytes kept at the top of code block B for natives (equate BLOCKB_TOP)
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src')


def NATIVE_SRC(ram):
    return [os.path.join(_SRC, 'natives.asm'), os.path.join(_SRC, f't{{ram}}', 'start.asm')]


def title(ram):
    """loading screen, 40x24 text: (row, text, attribute[, column]); attribute bits 5-3 = foreground 8-15"""
    return [(1, '{title}', 0x08),
            (2, f'for the Color Computer 3 ({{ram}}k)', 0x00),
            (4, 'Ported with the 6502to6809 kit', 0x10),
            (18, 'Loading...', 0x00)]


entry_state, entries, load_images, profile_roots = specbase.helpers(sys.modules[__name__])
'''

FILES['NAME/snapshots.py'] = '''"""{name} snapshots:  ./run.sh snapshots [--only NAME]      (kit/snapshot.py does the work)

For each image, stop the host Apple at the first instruction that will be translated:
  cold   boot the disk and stop at the hand-over: the first PC after the boot loader / relocator / decompressor
         that is game code.  Find it with the a2-disassemble skill, or by profiling the boot (kit/host/profile.py
         --phase) and looking at the first jump into the main code.
  levels (STAGES) set the game's level variable, enter its loader, and stop at the first translatable PC after
         it (record_writes marks the pages the loader writes: only those are restored by the native loader).
"""
from kit import snapshot as K
from . import spec

HAND_OVER = None        # TODO: 6502 address of the boot hand-over


def cold(rom, prom, dsk):
    if HAND_OVER is None:
        raise SystemExit('snapshots.py: set HAND_OVER first')
    return K.boot(rom, prom, dsk, until=lambda pc: pc == HAND_OVER)


def level(n):
    def take(rom, prom, dsk):
        m = cold(rom, prom, dsk)
        K.record_writes(m)
        # TODO: m.ram[<level variable>] = n; m.cpu.pc = <loader entry>
        return K.run_until(m, lambda pc: K.in_ranges(spec.CODE_RANGES, pc) and pc >= 0x0800, 8000, f'level {{n}}')
    return take


TAKERS = {{'cold': cold}}
for _n, _name in enumerate(spec.STAGES, 1):
    TAKERS[_name] = level(_n)

if __name__ == '__main__':
    K.main(spec, TAKERS)
'''

FILES['NAME/gen.py'] = '''"""{name}: generate the 6809 source (kit/gen.py)."""
from kit import gen
from . import spec

gen.use(spec)
build, natives, IO, IO_MEM, ROMCONST = gen.build, gen.natives, gen.IO, gen.IO_MEM, gen.ROMCONST

if __name__ == '__main__':
    gen.main(spec)
'''

for _t in (512, 128):
    FILES[f'NAME/build{_t}.py'] = f'''"""{{name}}: build the {_t}K disk (kit/build{_t}.py)."""
from kit import build{_t}
from . import spec

if __name__ == '__main__':
    build{_t}.main(spec)
'''

FILES['NAME/test_engine.py'] = '''"""{name}: differential test of the generated code against the 6502 (kit/test_engine.py)."""
from kit import test_engine
from . import spec

if __name__ == '__main__':
    test_engine.main(spec)
'''

FILES['NAME/src/natives.asm'] = '''; {name} native routines (hand-written 6809).  Start from the kit's rt/natives/apple2/* templates.
; Convention: A = 6502 A, B = 6502 X, RY = 6502 Y, S = 6502 stack, DP = 0 (zero page = <$nn).
; Leave registers, flags and zero page exactly as the 6502 routine leaves them (report.txt: "must produce").
'''

_START = '''; {name}: game start and level loading ({t}K target).
; game_start: entered from rt_start after the runtime init.  Marks both hi-res pages dirty, shows the selected one
; and enters the BASE image's code.
; nt_load: B = level number (1..NSTAGES = the index in spec.STAGES): load that level's pages and code, then run it
; from its snapshot state.  The native that replaces the game's own loader entry reads the game's level
; variable and does:   ldb <level> / jmp nt_load
; nt_reboot: restart from the boot image (game over that reboots the disk).
game_start
\tclra
\ttfr\ta,dp
\tlda\t#$20
\tjsr\tvm_page
\tlda\t#$40
\tjsr\tvm_page
\tjsr\tv_redraw
'''

FILES['NAME/src/t512/start.asm'] = _START + '''\tlda\t#BLK_BANK0\t\t; the boot image's code bank
\tjsr\trt_bank
\tjmp\t$C000

; stg_tab (from the build): per level fcb data block / fdb offset / fcb code bank
nt_load
\torcc\t#$50
\tlds\t#rt_stack
\tdecb
\tcmpb\t#NSTAGES-1
\tbls\tntl1
\tjsr\tx_trap\t\t\t; not a level number
\tfdb\t0
ntl1\tlslb
\tlslb
\tldx\t#stg_tab
\tabx
\tpshs\tx
\tlda\t#VVPAGE
\ttfr\ta,dp
\tlda\t,x
\tldb\t#BLK_STG
\tldx\t1,x
\tjsr\tunlz\t\t\t; unpack: 256-byte header (count, page numbers), then the pages
\tclra
\ttfr\ta,dp
\tjsr\thr_on
\tlda\t#BLK_STG
\tsta\t$FFA6
\tlda\t$C000
\tsta\tntl_n
\tldx\t#$C001
\tldu\t#$C100
\tstu\tntl_src
\tlda\t#BLK_STG
\tsta\tntl_blk
ntl_pg\ttst\tntl_n
\tbeq\tntl_end
\tlda\t#BLK_STG
\tsta\t$FFA6
\tldb\t,x+
\tlda\tntl_blk
\tsta\t$FFA6
\ttfr\tb,a
\tclrb
\ttfr\td,y
\tldu\tntl_src
ntl_cp\tldd\t,u++
\tstd\t,y++
\ttfr\ty,d
\ttstb
\tbne\tntl_cp
\tcmpu\t#$E000
\tblo\tntl_nb
\tldu\t#$C000
\tinc\tntl_blk
ntl_nb\tstu\tntl_src
\tdec\tntl_n
\tbra\tntl_pg
ntl_end\tjsr\thr_off
\tlda\t#$20
\tjsr\tvm_page
\tlda\t#$40
\tjsr\tvm_page
\tjsr\tv_redraw
\tpuls\tx
\tlda\t3,x
\tjsr\trt_bank
\tjmp\t$C000
ntl_n\tfcb\t0
ntl_src\tfdb\t0
ntl_blk\tfcb\t0

; nt_ovl: B = stage number of a CALL stage (the game's loader returns to its caller): unpack its blob, copy the
; pages the loader wrote completely, then the byte runs (hi, lo, n, n bytes ... 0, 0, 0), select the stage's
; code bank and return.  The caller must be engine code (the code bank under it changes).  Preserves CC, DP, X,
; Y, U; A and B are changed.
nt_ovl\tpshs\tcc,dp,x,y,u
\torcc\t#$50
\tdecb
\tcmpb\t#NSTAGES-1
\tbls\tnto1
\tjsr\tx_trap
\tfdb\t0
nto1\tlslb
\tlslb
\tldx\t#stg_tab
\tabx
\tpshs\tx
\tlda\t#VVPAGE
\ttfr\ta,dp
\tlda\t,x
\tldb\t#BLK_STG
\tldx\t1,x
\tjsr\tunlz
\tclra
\ttfr\ta,dp
\tjsr\thr_on
\tlda\t#BLK_STG
\tsta\t$FFA6
\tlda\t$C000
\tsta\tntl_n
\tldx\t#$C001
\tldu\t#$C100
\tstu\tntl_src
\tlda\t#BLK_STG
\tsta\tntl_blk
nto_pg\ttst\tntl_n
\tbeq\tnto_run
\tlda\t#BLK_STG
\tsta\t$FFA6
\tldb\t,x+
\tlda\tntl_blk
\tsta\t$FFA6
\ttfr\tb,a
\tclrb
\ttfr\td,y
\tldu\tntl_src
nto_cp\tldd\t,u++
\tstd\t,y++
\ttfr\ty,d
\ttstb
\tbne\tnto_cp
\tcmpu\t#$E000
\tblo\tnto_nb
\tldu\t#$C000
\tinc\tntl_blk
nto_nb\tstu\tntl_src
\tdec\tntl_n
\tbra\tnto_pg
nto_run\tldu\tntl_src
\tbsr\tnto_get
\tsta\tnto_d
\tbsr\tnto_get
\tsta\tnto_d+1
\tbsr\tnto_get
\ttsta
\tbeq\tnto_end
\tsta\tntl_n
\tldy\tnto_d
nto_rb\tbsr\tnto_get
\tsta\t,y+
\tdec\tntl_n
\tbne\tnto_rb
\tbra\tnto_run
nto_get\tlda\tntl_blk
\tsta\t$FFA6
\tlda\t,u+
\tcmpu\t#$E000
\tblo\tntg1
\tldu\t#$C000
\tinc\tntl_blk
ntg1\trts
nto_end\tjsr\thr_off
\tlda\t#$20\t\t\t; the blob may have touched hi-res: reconvert both pages when shown
\tjsr\tvm_page
\tlda\t#$40
\tjsr\tvm_page
\tpuls\tx
\tlda\t3,x
\tjsr\trt_bank
\tpuls\tcc,dp,x,y,u,pc
nto_d\tfdb\t0


nt_reboot
\torcc\t#$50
\tjmp\trt_start
'''

FILES['NAME/src/t128/start.asm'] = _START + '''\tjmp\tOVORG

; ds_stg (from the build): fdb file of level 1..NSTAGES (streamed from the disk)
nt_load
\torcc\t#$50
\tlds\t#rt_stack
\tdecb
\tcmpb\t#NSTAGES-1
\tbls\tntl1
\tjsr\tx_trap\t\t\t; not a level number
\tfdb\t0
ntl1\tlslb
\tldx\t#ds_stg
\tldx\tb,x
\tjsr\tds_load
\tjsr\tv_inval
\tjsr\tv_redraw
\tjmp\tOVORG

nt_reboot
\torcc\t#$50
\tlds\t#rt_stack
\tldx\t#ds_cold
\tjsr\tds_load
\tjmp\trt_start
'''

FILES['NAME/scenarios/play.json'] = '''{{
  "name": "play",
  "frames": 6000,
  "keys": "600:A0",
  "joy": "1200:255:128:0,1800:128:128:0",
  "phases": "boot:0,title:600,play:1200"
}}
'''

FILES['NAME/mame/play.txt'] = '''# {name}: MAME input script (kit/mame/input.lua).  T = frames after the game start.
#   T key TEXT | T joyx V | T joyy V | T1-T2/N keys CHARS | T1-T2/N joyx A,B,C
600 key SPACE
1200 joyx 1023
1800 joyx 512
'''

FILES['README.md'] = '''# {name} for the CoCo 3

A static 6502 to 6809 port made with the 6502to6809 kit ({kit}). The kit's `SKILL.md` describes the method.

```
./run.sh snapshots      # after setting HAND_OVER in {name}/snapshots.py
./run.sh profile        # coverage of {name}/scenarios/play.json
./run.sh survey         # build/survey.txt + build/draft_spec.py: what to transcode, what to replace
./run.sh gen            # build/src
./run.sh test_engine -n 300
./run.sh build512       # build/{name}_512k.dsk   (LOADM"{loader}":EXEC)
./run.sh build128       # build/{name}_128k.dsk
```

Nothing derived from the game disk is committed: `build/` is ignored.
'''

FILES['.gitignore'] = 'build/\n__pycache__/\n*.pyc\n*.dsk\n*.DSK\n*.woz\n*.nib\n*.bin\n'


def scaffold(name, dir_, disk='', rom='', prom='', loader=None, title=None):
    assert name.isidentifier(), 'NAME must be a Python identifier'
    loader = (loader or name[:8]).upper()
    title = title or name.upper()
    if os.path.exists(os.path.join(dir_, name, 'spec.py')):
        raise SystemExit(f'{dir_}/{name} already exists')
    vals = dict(name=name, kit=REPO, disk=disk, rom=rom, prom=prom, loader=loader, title=title)
    for rel, text in FILES.items():
        p = os.path.join(dir_, rel.replace('NAME', name))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'w').write(text.format(**vals, t=512 if 't512' in rel else 128))
    os.chmod(os.path.join(dir_, 'run.sh'), os.stat(os.path.join(dir_, 'run.sh')).st_mode | stat.S_IXUSR)
    return dir_


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('name')
    ap.add_argument('--dir')
    ap.add_argument('--disk', default='')
    ap.add_argument('--rom', default='~/proyectos/mame/roms/apple2e/APPLE2E.ROM')
    ap.add_argument('--prom', default='~/proyectos/mame/roms/apple2e/341-0027-a.p5')
    ap.add_argument('--loader', help='disk file name (1-8 letters): LOADM"<loader>":EXEC')
    ap.add_argument('--title', help='loading screen title')
    a = ap.parse_args(argv)
    d = os.path.abspath(os.path.expanduser(a.dir or f'~/proyectos/{a.name}-coco3'))
    scaffold(a.name, d, a.disk, a.rom, a.prom, a.loader, a.title)
    print(f'port scaffolded in {d}\nnext: set disk in {a.name}/port.toml, HAND_OVER in {a.name}/snapshots.py, '
          f'then ./run.sh snapshots / profile / survey (see the kit SKILL.md)')


if __name__ == '__main__':
    main()
