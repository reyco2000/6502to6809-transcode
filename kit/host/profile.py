"""profile.py - execution profile and translator coverage of an Apple II program on the host reference machine
(milestone 4 input).

    python3 -m kit.host.profile [APPLE.dsk] --rom ROM --prom PROM
            [--scenario SCEN.json ...]                                      (one run per scenario file)
            [--frames N] [--keys F:HEX,...] [--joy F:X:Y:B,... | --joyfile FILE] [--phase NAME:FRAME ...]
            [--out DIR] [--coverage COV.json] [--merge OLD.json ...] [--bulk N] [--episode-gap FRAMES]

    python3 -m kit.host.profile --merge A.json --merge B.json --coverage AB.json      (merge only)

Generic: wraps host/apple2.py + host/cpu6502.py (instance-level hooks on write/io; their behaviour is unchanged).

Inputs.  A scenario is a JSON object (all keys optional except `frames`; file paths relative to the scenario file):
    {"name": "main", "frames": 14000,
     "keys": "3000:8D,3600:A0"            | {"3000": "8D"} | [[3000, "8D"], ...]      (frame -> key, hex)
     "joy": "4400:255:128:0,..."           | [[4400, 255, 128, 0], ...]                (frame -> paddle x, y, buttons)
     "joyfile": "joy3.txt",                (a file holding a `joy` string; merged after `joy`)
     "phases": [["boot", 0], ["intro", 627]] | "boot:0,intro:627" | {"boot": 0}    (named frame ranges)
     "disk": "x.dsk", "rom": "APPLE2E.ROM", "prom": "341-0027-a.p5",               (CLI arguments override)
     "pokes": "BF2B:02,0040:FF:1200",      (ADDR:VAL[:FROM_FRAME] held every frame: coverage cheats such as
                                           infinite lives, to reach later levels; written to RAM directly, so
                                           they never count as writers.  Use only for profiling, never snapshots)
     "stop_pc": "0300", "max_instr": 0, "bulk": 64, "episode_gap": 60}
Command-line --keys/--joy/--joyfile/--phase/--frames form one scenario when no --scenario is given.

Outputs.
  * --coverage FILE: the coverage file (schema: docs/COVERAGE.md, format "a2cc3-coverage" version 1): per code image
    the page bytes at first execution, executed instruction starts + counts, entry points by kind, SMC sites (opcode /
    operand) with the byte tuples seen at execution, per-site EA class masks, load episodes, bytes stored to as data
    while the image was live, min S, bulk writers / loaders.  Several scenarios and --merge files are merged (union).
  * --out DIR: DIR/profile.json (per-phase report: instruction mix, routines, EA classes, SMC, control flow, I/O,
    page flips) as before; with several scenarios DIR/profile_NAME.json.

Library (tests, tools):
    profile_machine(machine, schedule) -> Profile (.coverage: single-run coverage dict, .report: report dict)
    merge_coverage([cov, ...]) -> cov;  validate_coverage(cov);  summarize(cov) -> dict;  load_scenario(path)
    make_machine(rom_path, prom_path, dsk_path)
`machine` is a host Apple2 whose cpu.pc / RAM the caller has set up (the CLI calls machine.boot()).

Code images.  Per 256-byte page, the bytes executed (opcode / operand roles).  A page gets a new image when bytes of
its current image were changed by a "bulk" writer (a store PC that changed >= BULK distinct executed bytes in the
run: loaders, relocators) and code on the page runs again; changes by other writers are self-modifying code (SMC).
Images with the same code (see docs/COVERAGE.md, "image identity") are merged.  Speed: ~5-10x slower than plain runs.
"""
import argparse
import bisect
import collections
import json
import os
import sys
import time

from .apple2 import Apple2, CYCLES_PER_FRAME
from .cpu6502 import IllegalOpcode

# ---- 6502 opcode table: op -> (mnemonic, mode, length, access)  access: r w m(rmw) j(jump/flow) - (none)
_MODES_LEN = {'imp': 1, 'acc': 1, 'imm': 2, 'zp': 2, 'zpx': 2, 'zpy': 2, 'rel': 2, 'indx': 2, 'indy': 2,
              'abs': 3, 'absx': 3, 'absy': 3, 'ind': 3}
_OPS = """
00 brk imp j|01 ora indx r|05 ora zp r|06 asl zp m|08 php imp -|09 ora imm -|0a asl acc -|0d ora abs r|0e asl abs m
10 bpl rel j|11 ora indy r|15 ora zpx r|16 asl zpx m|18 clc imp -|19 ora absy r|1d ora absx r|1e asl absx m
20 jsr abs j|21 and indx r|24 bit zp r|25 and zp r|26 rol zp m|28 plp imp -|29 and imm -|2a rol acc -|2c bit abs r
2d and abs r|2e rol abs m|30 bmi rel j|31 and indy r|35 and zpx r|36 rol zpx m|38 sec imp -|39 and absy r
3d and absx r|3e rol absx m|40 rti imp j|41 eor indx r|45 eor zp r|46 lsr zp m|48 pha imp -|49 eor imm -|4a lsr acc -
4c jmp abs j|4d eor abs r|4e lsr abs m|50 bvc rel j|51 eor indy r|55 eor zpx r|56 lsr zpx m|58 cli imp -
59 eor absy r|5d eor absx r|5e lsr absx m|60 rts imp j|61 adc indx r|65 adc zp r|66 ror zp m|68 pla imp -
69 adc imm -|6a ror acc -|6c jmp ind j|6d adc abs r|6e ror abs m|70 bvs rel j|71 adc indy r|75 adc zpx r
76 ror zpx m|78 sei imp -|79 adc absy r|7d adc absx r|7e ror absx m|81 sta indx w|84 sty zp w|85 sta zp w
86 stx zp w|88 dey imp -|8a txa imp -|8c sty abs w|8d sta abs w|8e stx abs w|90 bcc rel j|91 sta indy w
94 sty zpx w|95 sta zpx w|96 stx zpy w|98 tya imp -|99 sta absy w|9a txs imp -|9d sta absx w|a0 ldy imm -
a1 lda indx r|a2 ldx imm -|a4 ldy zp r|a5 lda zp r|a6 ldx zp r|a8 tay imp -|a9 lda imm -|aa tax imp -
ac ldy abs r|ad lda abs r|ae ldx abs r|b0 bcs rel j|b1 lda indy r|b4 ldy zpx r|b5 lda zpx r|b6 ldx zpy r
b8 clv imp -|b9 lda absy r|ba tsx imp -|bc ldy absx r|bd lda absx r|be ldx absy r|c0 cpy imm -|c1 cmp indx r
c4 cpy zp r|c5 cmp zp r|c6 dec zp m|c8 iny imp -|c9 cmp imm -|ca dex imp -|cc cpy abs r|cd cmp abs r
ce dec abs m|d0 bne rel j|d1 cmp indy r|d5 cmp zpx r|d6 dec zpx m|d8 cld imp -|d9 cmp absy r|dd cmp absx r
de dec absx m|e0 cpx imm -|e1 sbc indx r|e4 cpx zp r|e5 sbc zp r|e6 inc zp m|e8 inx imp -|e9 sbc imm -
ea nop imp -|ec cpx abs r|ed sbc abs r|ee inc abs m|f0 beq rel j|f1 sbc indy r|f5 sbc zpx r|f6 inc zpx m
f8 sed imp -|f9 sbc absy r|fd sbc absx r|fe inc absx m
"""
OPINFO = [None] * 256
for _it in _OPS.replace('\n', '|').split('|'):
    if _it.strip():
        _h, _mn, _md, _ac = _it.split()
        OPINFO[int(_h, 16)] = (_mn, _md, _MODES_LEN[_md], _ac)
OPLEN = [i[2] if i else 1 for i in OPINFO]
MEMMODE = [bool(i) and i[1] in ('zp', 'zpx', 'zpy', 'abs', 'absx', 'absy', 'indx', 'indy') and i[3] in 'rwm'
           and i[0] != 'jsr' for i in OPINFO]
MIXKEY = [(i[0], i[1]) if i else ('ill', '') for i in OPINFO]
DECOPS = bytearray(1 if i and i[0] in ('adc', 'sbc') else 0 for i in OPINFO)

# effective-address classes (bit masks)
EA_NAMES = ['zp', 'stack', 'ram0', 'hires', 'ram6', 'io', 'rom']
EA_CLS = [0] * 256
for _p in range(256):
    EA_CLS[_p] = (1 if _p == 0 else 2 if _p == 1 else 4 if _p < 0x20 else 8 if _p < 0x60 else 16 if _p < 0xC0
                  else 32 if _p == 0xC0 else 64)

# per-site flag bits (coverage "flags")
FL_DEC, FL_ZPWRAP, FL_PULLRET, FL_TXS, FL_BRK, FL_RTI = 1, 2, 4, 8, 16, 32
FLAG_NAMES = ['decimal', 'zpwrap', 'pull_ret', 'txs', 'brk', 'rti']

ST_JSR, ST_PHA, ST_PHP, ST_BRK, ST_OTHER = 1, 2, 3, 4, 5
STKIND = bytearray(ST_JSR if o == 0x20 else ST_PHA if o == 0x48 else ST_PHP if o == 0x08 else ST_BRK if o == 0
                   else ST_OTHER for o in range(256))
BULK = 64           # a writer that changed >= BULK distinct executed bytes is a loader / relocator
EPISODE_GAP = 60    # frames: loader activity closer than this is one load episode

# control transfer kind of an instruction leaving ROM (RTS handled separately)
_ROMFLOW = {0x4C: 'jmp', 0x6C: 'jmpi', 0x40: 'rti', 0x00: 'brk', 0x20: 'jsr'}
for _o in (0x10, 0x30, 0x50, 0x70, 0x90, 0xB0, 0xD0, 0xF0):
    _ROMFLOW[_o] = 'br'
ENTRY_KINDS = ['start', 'jsr', 'jmp', 'jmpi', 'br', 'fall', 'rts', 'rts_skip', 'rts_pha', 'rts_other', 'rti', 'brk']
_RTSKIND = {'jsr-bytes': 'rts_skip', 'pha': 'rts_pha'}
ROLE_CH = '.oax'
Z256 = bytes(256)
FORMAT, VERSION = 'a2cc3-coverage', 1


class Phase:
    def __init__(self, name, f0):
        self.name, self.f0, self.f1 = name, f0, None
        self.instr = 0
        self.cycles = 0
        self.cnt = [0] * 65536          # executions per PC
        self.rout = collections.Counter()   # instructions per routine (innermost JSR target, 0 = none)
        self.mix = collections.Counter()    # (mnemonic, mode)
        self.pw = [0] * 256             # stores per page (all stores < $C000)
        self.codepw = 0                 # stores into pages holding executed code
        self.codebw = 0                 # stores into executed bytes (changed or not)
        self.io = collections.Counter()  # ('r'|'w', addr)
        self.flips = []                 # (frame, page, bytes, pairs)
        self.region = collections.Counter()  # instructions by code region
        self.dec = 0                    # ADC/SBC with D=1
        self.zpwrap = 0                 # zp,X / zp,Y index wrap, (zp),Y / (zp,X) pointer at $FF
        self.stores_ind = collections.Counter()  # store mode -> count of stores
        self.smin = 0xFF                # lowest 6502 S seen
        self.edges = collections.Counter()  # (kind, from, to): jsr / jmp / taken branch (static targets)
        self.ntaken = 0                 # taken branches


class RawImage:
    """one code image as recorded during a run (before identity merging)"""
    __slots__ = ('idx', 'page', 'f0', 'lastf', 'lastq', 'snap', 'val', 'lwf', 'lwp', 'role', 'ic', 'ea', 'ent',
                 'smc', 'smcw', 'smcn', 'vol', 'dw', 'flags')

    def __init__(self, idx, page, frame, ram):
        lo = page << 8
        self.idx, self.page, self.f0, self.lastf, self.lastq = idx, page, frame, frame, -1
        self.snap = bytes(ram[lo:lo + 256])     # page at the image's first execution
        self.val = [-1] * 256                   # value of each executed byte at its first execution
        self.lwf = [-1] * 256                   # frame of the last store to the byte before its first execution
        self.lwp = [-1] * 256                   # PC of that store
        self.role = None                        # bytes(256) roles, filled when the image is closed
        self.ic = [0] * 256                     # executions per instruction start
        self.ea = [0] * 256                     # EA class mask per memory instruction
        self.ent = collections.Counter()        # (target, kind, site, site raw image) -> count
        self.smc = {}                           # off -> Counter((len, b0, b1, b2))   watched SMC sites
        self.smcw = {}                          # off -> writer PCs of changed bytes executed there
        self.smcn = collections.Counter()       # off -> executions with a changed byte
        self.vol = set()                        # offsets executed after a (non-bulk) change
        self.dw = {}                            # off -> [stores, changed, first seq, writers]
        self.flags = {}                         # off -> FL_* mask


def peek_rom(m, a):
    """side-effect-free read of $C000-$FFFF (code fetch from ROM / LC)"""
    if a >= 0xD000:
        if m.lc_read:
            if a < 0xE000 and m.lc_b2:
                return m.lc_bank2[a - 0xD000]
            return m.lc[a - 0xD000]
        return m.rom[a - 0xD000]
    if a >= 0xC100 and m.cxrom is not None:
        pg = a >> 8
        if m.intcx or (pg == 0xC3 and not m.slotc3) or (pg >= 0xC8 and m.intc8):
            return m.cxrom[a - 0xC000]
    if 0xC600 <= a < 0xC700:
        return m.prom[a - 0xC600]
    return 0


def region_of(pc):
    if pc < 0x100: return 'zp'
    if pc < 0x200: return 'stack'
    if pc < 0x400: return '0200-03FF'
    if pc < 0x800: return 'text 0400-07FF'
    if pc < 0x2000: return '0800-1FFF'
    if pc < 0x6000: return 'hires 2000-5FFF'
    if pc < 0xC000: return '6000-BFFF'
    if 0xFCA8 <= pc <= 0xFCB3: return 'rom WAIT'
    return 'rom/io C000+'


# ------------------------------------------------------------------------------------------------- scenarios
def _ints(s):
    return int(s, 16) if isinstance(s, str) else int(s)


def parse_keys(v):
    out = {}
    if not v:
        return out
    items = ([s.split(':') for s in v.split(',') if s.strip()] if isinstance(v, str) else
             list(v.items()) if isinstance(v, dict) else v)
    for f, k in items:
        out[int(f)] = _ints(k)
    return out


def parse_joy(v):
    out = {}
    if not v:
        return out
    if isinstance(v, str):
        items = [[int(x) for x in s.split(':')] for s in v.replace('\n', ',').split(',') if s.strip()]
    elif isinstance(v, dict):
        items = [[int(f)] + list(t) for f, t in v.items()]
    else:
        items = v
    for f, x, y, b in items:
        out[int(f)] = (int(x), int(y), int(b))
    return out


def parse_phases(v):
    if not v:
        return []
    if isinstance(v, str):
        items = [s.split(':') for s in v.split(',') if s.strip()]
    elif isinstance(v, dict):
        items = list(v.items())
    else:
        items = [s.split(':') if isinstance(s, str) else s for s in v]
    return sorted(((str(n), int(f)) for n, f in items), key=lambda t: t[1])


def parse_pokes(v):
    """"ADDR:VAL[:FROM],..." | [[addr, val, from], ...] -> [(addr, val, from_frame)]"""
    if not v:
        return []
    items = [x.split(':') for x in v.split(',') if x.strip()] if isinstance(v, str) else v
    out = []
    for it in items:
        a, val = (int(x, 16) if isinstance(x, str) else x for x in it[:2])
        out.append((a, val, int(it[2]) if len(it) > 2 else 0))
    return out


def normalize_schedule(s, base_dir=None):
    """scenario / schedule dict -> normalized dict (keys/joy dicts, phases list, ints)"""
    base_dir = base_dir or s.get('_dir') or '.'
    joy = parse_joy(s.get('joy'))
    if s.get('joyfile'):
        jp = s['joyfile'] if os.path.isabs(s['joyfile']) else os.path.join(base_dir, s['joyfile'])
        with open(jp) as fh:
            joy.update(parse_joy(fh.read().strip()))
    phases = parse_phases(s.get('phases'))
    if not phases:
        phases = [('all', 0)]
    elif phases[0][1] > 0:
        phases.insert(0, ('pre', 0))
    sp = s.get('stop_pc')
    out = {'name': str(s.get('name', 'run')), 'frames': int(s.get('frames', 6000)), 'keys': parse_keys(s.get('keys')),
           'joy': joy, 'phases': phases, 'stop_pc': -1 if sp is None else _ints(sp),
           'max_instr': int(s.get('max_instr', 0) or 0), 'bulk': int(s.get('bulk', BULK)),
           'episode_gap': int(s.get('episode_gap', EPISODE_GAP)), 'pokes': parse_pokes(s.get('pokes')),
           '_norm': True}
    for k in ('disk', 'rom', 'prom'):
        if s.get(k):
            out[k] = s[k] if os.path.isabs(s[k]) else os.path.join(base_dir, s[k])
    return out


def load_scenario(path):
    with open(path) as fh:
        s = json.load(fh)
    return normalize_schedule(s, os.path.dirname(os.path.abspath(path)))


def make_machine(rom_path, prom_path, dsk_path):
    full = open(rom_path, 'rb').read()
    return Apple2(full[-0x3000:], open(prom_path, 'rb').read(), open(dsk_path, 'rb').read(),
                  cxrom=full[-0x4000:-0x3000] if len(full) >= 0x4000 else None)


# ------------------------------------------------------------------------------------------------- the run
class Profile:
    def __init__(self, coverage, report):
        self.coverage, self.report = coverage, report


def profile_machine(m, schedule, log=None):
    """Run machine `m` from its current state under `schedule` (scenario dict, see module doc) and return a
    Profile with .coverage (coverage dict of this run, already identity-merged) and .report (profile.json dict)."""
    sc = schedule if schedule.get('_norm') else normalize_schedule(schedule)
    bulk_n, gap = sc['bulk'], sc['episode_gap']
    stop_pc, max_instr, frames = sc['stop_pc'], sc['max_instr'], sc['frames']
    keys, joy, phase_def = sc['keys'], sc['joy'], sc['phases']
    pokes = sc.get('pokes', [])
    say = log or (lambda *a, **k: None)
    phases = []

    cpu = m.cpu
    ram = m.ram
    role = bytearray(65536)         # bit0 executed as opcode, bit1 as operand (current image of the page)
    chg = bytearray(65536)          # executed byte changed since it was last executed
    watched = bytearray(65536)      # instruction start recorded as SMC site (current image)
    wpc = [0] * 65536               # last writer of a changed executed byte
    lwf = [-1] * 65536              # frame of the last store to each byte < $C000
    lwp = [-1] * 65536              # PC of that store
    codepage = bytearray(256)       # page has a code image
    pdirty = bytearray(256)         # page has executed bytes changed since code last ran on it
    pchg = [[] for _ in range(256)]  # those bytes (capped)
    stksrc = bytearray(256)         # who wrote each stack byte last
    wset = collections.defaultdict(set)   # writer PC -> distinct executed bytes it changed (capped)
    wlf = {}                        # writer PC -> last frame it stored
    wint = {}                       # writer PC -> activity intervals [[f0, f1], ...] (gaps <= episode_gap merged)
    codew = collections.Counter()   # (addr, writer, role) -> changed stores into executed bytes
    smc_exec = collections.Counter()  # (addr, 'op'|'operand', writer, bulk) -> executions of a changed byte
    overlap = collections.Counter()   # (addr, now-role, pc) byte executed in both roles within one image
    images = []                     # RawImage
    img_cur = [-1] * 256
    jmpi = collections.Counter()    # (site, target)
    rts_odd = collections.Counter()  # (site, target, kind)
    pla_ret = collections.Counter()  # (site, op)
    txs = collections.Counter()
    brk_rti = collections.Counter()
    site_ea = {}                    # pc -> EA class mask (memory instructions)
    rom_ent = collections.Counter()  # (kind, site, site raw image, target) -> transfers into ROM / LC
    callstack = []                  # [(target, return address, S after push)]
    shown = {0: bytes(0x2000), 1: bytes(0x2000)}
    S = [0, ST_OTHER, 0, None, 0]   # writer pc, writer kind, frame, phase, instruction sequence number
    flip = {'page': None}

    had_write, had_io = 'write' in m.__dict__, 'io' in m.__dict__
    orig_write = m.write
    orig_io = m.io

    def new_image(bp):
        ii = img_cur[bp]
        lo = bp << 8
        if ii >= 0:
            images[ii].role = bytes(role[lo:lo + 256])
            role[lo:lo + 256] = Z256
            chg[lo:lo + 256] = Z256
            watched[lo:lo + 256] = Z256
        pdirty[bp] = 0
        pchg[bp] = []
        im = RawImage(len(images), bp, S[2], ram)
        images.append(im)
        img_cur[bp] = im.idx
        codepage[bp] = 1
        return im

    def check_page(bp):
        lst = pchg[bp]
        pdirty[bp] = 0
        pchg[bp] = []
        for a in lst:
            if chg[a] and len(wset[wpc[a]]) >= bulk_n:
                new_image(bp)
                return

    def write(addr, v):
        if addr < 0xC000:
            w = S[0]
            f = S[2]
            ph = S[3]
            p = addr >> 8
            ph.pw[p] += 1
            lwf[addr] = f
            lwp[addr] = w
            if wlf.get(w) != f:
                wlf[w] = f
                iv = wint.get(w)
                if iv is None:
                    wint[w] = [[f, f]]
                elif f - iv[-1][1] <= gap:
                    iv[-1][1] = f
                else:
                    iv.append([f, f])
            if codepage[p]:
                ph.codepw += 1
                old = ram[addr]
                r = role[addr]
                if r:
                    ph.codebw += 1
                    if old != v:
                        if pdirty[p]:
                            if len(pchg[p]) < 1024:
                                pchg[p].append(addr)
                        else:
                            pdirty[p] = 1
                            pchg[p] = [addr]
                        chg[addr] = 1
                        wpc[addr] = w
                        s = wset[w]
                        if len(s) < 4096:
                            s.add(addr)
                        codew[(addr, w, r)] += 1
                if p > 1:
                    dw = images[img_cur[p]].dw
                    o = addr & 0xFF
                    d = dw.get(o)
                    if d is None:
                        dw[o] = [1, old != v, S[4], {w}]
                    else:
                        d[0] += 1
                        if old != v:
                            d[1] = True
                        if len(d[3]) < 16:
                            d[3].add(w)
            if p == 1:
                stksrc[addr & 0xFF] = S[1]
        orig_write(addr, v)

    def io(addr, v):
        ph = S[3]
        ph.io[('r' if v is None else 'w', addr & 0xFFFF)] += 1
        lo = addr & 0xFF
        if lo in (0x54, 0x55):
            pg = lo - 0x54
            if flip['page'] != pg:
                flip['page'] = pg
                cur = bytes(ram[0x2000 + pg * 0x2000:0x4000 + pg * 0x2000])
                old = shown[pg]
                if cur != old:
                    diff = [i for i in range(0x2000) if cur[i] != old[i]]
                    nb, pairs = len(diff), len({i >> 1 for i in diff})
                else:
                    nb = pairs = 0
                ph.flips.append((S[2], pg, nb, pairs))
                shown[pg] = cur
        return orig_io(addr, v)

    m.write = write
    m.io = io

    t0 = time.time()
    pi = 0
    ph = None
    pend, pend_site, pend_img = 'start', -1, -1     # control transfer that leads to the next instruction
    seq = 0
    stop = None
    f = 0
    try:
        for f in range(frames):
            S[2] = f
            if pi < len(phase_def) and f >= phase_def[pi][1]:
                if ph:
                    ph.f1 = f
                ph = Phase(phase_def[pi][0], f)
                phases.append(ph)
                S[3] = ph
                pi += 1
                say(f'phase {ph.name} from frame {f} ({time.time() - t0:.0f} s)', flush=True)
            if f in keys:
                m.kbd = keys[f] | 0x80
            if f in joy:
                m.paddle[0], m.paddle[1], m.buttons = joy[f]
            for pa, pv, pf in pokes:
                if f >= pf:
                    m.ram[pa] = pv
            end = cpu.cycles + CYCLES_PER_FRAME
            c0 = cpu.cycles
            cnt = ph.cnt
            mix = ph.mix
            rout = ph.rout
            edges = ph.edges
            n = 0
            try:
                while cpu.cycles < end:
                    pc = cpu.pc
                    if pc == stop_pc:
                        stop = 'stop_pc'
                        break
                    n += 1
                    seq += 1
                    S[4] = seq
                    cnt[pc] += 1
                    if pc >= 0xC000:
                        op = peek_rom(m, pc)
                        S[0] = pc
                        S[1] = STKIND[op]
                        mix[MIXKEY[op]] += 1
                        rout[callstack[-1][0] if callstack else 0] += 1
                        if pend is not None:
                            rom_ent[(pend, pend_site, pend_img, pc)] += 1
                            pend = None
                        if op == 0x20:
                            ret = (pc + 2) & 0xFFFF
                            cpu.step()
                            callstack.append((cpu.pc, ret, cpu.s))
                            if len(callstack) > 256:
                                del callstack[:128]
                            k = 'jsr'
                        elif op == 0x60:
                            cpu.step()
                            tgt = (cpu.pc - 1) & 0xFFFF
                            while callstack and callstack[-1][1] != tgt and callstack[-1][2] < cpu.s:
                                callstack.pop()
                            if callstack and callstack[-1][1] == tgt:
                                callstack.pop()
                                k = 'rts'
                            else:
                                k = 'rts_other'
                        else:
                            cpu.step()
                            k = _ROMFLOW.get(op, 'fall')
                        if cpu.s < ph.smin:
                            ph.smin = cpu.s
                        if cpu.pc < 0xC000:
                            pend, pend_site, pend_img = k, pc, -1
                        continue
                    op = ram[pc]
                    info = OPINFO[op]
                    S[0] = pc
                    S[1] = STKIND[op]
                    if info is None:
                        mix[('ill', '')] += 1
                        cpu.step()      # raises IllegalOpcode
                        continue
                    L = OPLEN[op]
                    mix[MIXKEY[op]] += 1
                    rout[callstack[-1][0] if callstack else 0] += 1
                    # ---- code bytes: roles, SMC executions, images
                    p = pc >> 8
                    if img_cur[p] < 0:
                        new_image(p)
                    elif pdirty[p]:
                        check_page(p)
                    smcw = None
                    k = 0
                    while k < L:
                        b = pc + k
                        if b >= 0xC000:
                            break
                        bp = b >> 8
                        if bp != p:
                            if img_cur[bp] < 0:
                                new_image(bp)
                            elif pdirty[bp]:
                                check_page(bp)
                        new = 1 if k == 0 else 2
                        if chg[b]:
                            w = wpc[b]
                            bulk = len(wset[w]) >= bulk_n
                            smc_exec[(b, 'op' if k == 0 else 'operand', w, bulk)] += 1
                            chg[b] = 0
                            if bulk:
                                # reload: new image for this page (and redo this instruction's bytes on it)
                                new_image(bp)
                                if bp == p:
                                    k = 0
                                    smcw = None
                                    continue
                            else:
                                images[img_cur[bp]].vol.add(b & 0xFF)
                                if smcw is None:
                                    smcw = {w}
                                else:
                                    smcw.add(w)
                                r = role[b]
                                if not r & new:
                                    overlap[(b, new, pc)] += 1
                                    role[b] = r | new
                                k += 1
                                continue
                        r = role[b]
                        if not r:
                            bi = images[img_cur[bp]]
                            o = b & 0xFF
                            bi.val[o] = ram[b]
                            bi.lwf[o] = lwf[b]
                            bi.lwp[o] = lwp[b]
                            role[b] = new
                        elif not r & new:
                            overlap[(b, new, pc)] += 1
                            role[b] = r | new
                        k += 1
                    im = images[img_cur[p]]
                    off = pc & 0xFF
                    if pend is not None:
                        im.ent[(pc, pend, pend_site, pend_img)] += 1
                        pend = None
                    if smcw is not None:
                        im.smcn[off] += 1
                        sw = im.smcw.get(off)
                        if sw is None:
                            im.smcw[off] = smcw
                        else:
                            sw |= smcw
                        if not watched[pc]:
                            watched[pc] = 1
                            tc = collections.Counter()
                            if im.ic[off]:
                                tri = []
                                for kk in range(3):
                                    bb = (pc + kk) & 0xFFFF
                                    ix = img_cur[bb >> 8] if bb < 0xC000 else -1
                                    vv = images[ix].val[bb & 0xFF] if ix >= 0 else -1
                                    tri.append(vv if vv >= 0 else ram[bb])
                                tc[(OPLEN[tri[0]], tri[0], tri[1], tri[2])] = im.ic[off]
                            im.smc[off] = tc
                    if watched[pc]:
                        tc = im.smc.get(off)
                        if tc is not None:
                            tc[(L, op, ram[(pc + 1) & 0xFFFF], ram[(pc + 2) & 0xFFFF])] += 1
                    im.ic[off] += 1
                    im.lastq = seq
                    im.lastf = f
                    # ---- effective address of memory instructions (before executing)
                    if MEMMODE[op]:
                        md = info[1]
                        o1 = ram[pc + 1]
                        wrap = False
                        if md == 'zp':
                            ea = o1
                        elif md == 'zpx':
                            ea = o1 + cpu.x
                            if ea > 0xFF:
                                wrap = True
                                ea &= 0xFF
                        elif md == 'zpy':
                            ea = o1 + cpu.y
                            if ea > 0xFF:
                                wrap = True
                                ea &= 0xFF
                        elif md == 'abs':
                            ea = o1 | ram[pc + 2] << 8
                        elif md == 'absx':
                            ea = ((o1 | ram[pc + 2] << 8) + cpu.x) & 0xFFFF
                        elif md == 'absy':
                            ea = ((o1 | ram[pc + 2] << 8) + cpu.y) & 0xFFFF
                        elif md == 'indy':
                            if o1 == 0xFF:
                                wrap = True
                            ea = ((ram[o1] | ram[(o1 + 1) & 0xFF] << 8) + cpu.y) & 0xFFFF
                        else:   # indx
                            z = (o1 + cpu.x) & 0xFF
                            if z == 0xFF:
                                wrap = True
                            ea = ram[z] | ram[(z + 1) & 0xFF] << 8
                        cls = EA_CLS[ea >> 8]
                        site_ea[pc] = site_ea.get(pc, 0) | cls
                        im.ea[off] |= cls
                        if wrap:
                            ph.zpwrap += 1
                            im.flags[off] = im.flags.get(off, 0) | FL_ZPWRAP
                        if info[3] == 'w':
                            ph.stores_ind[md] += 1
                    if DECOPS[op] and cpu.p & 8:
                        ph.dec += 1
                        im.flags[off] = im.flags.get(off, 0) | FL_DEC
                    # ---- flow
                    if op == 0x20:
                        ret = (pc + 2) & 0xFFFF
                        cpu.step()
                        callstack.append((cpu.pc, ret, cpu.s))
                        edges[('jsr', pc, cpu.pc)] += 1
                        if cpu.s < ph.smin:
                            ph.smin = cpu.s
                        if len(callstack) > 256:
                            del callstack[:128]
                        pend, pend_site, pend_img = 'jsr', pc, im.idx
                    elif info[1] == 'rel':
                        cpu.step()
                        if cpu.pc != ((pc + 2) & 0xFFFF):
                            ph.ntaken += 1
                            edges[('br', pc, cpu.pc)] += 1
                            pend, pend_site, pend_img = 'br', pc, im.idx
                        elif cpu.pc >> 8 != p:
                            pend, pend_site, pend_img = 'fall', pc, im.idx
                    elif op == 0x4C:
                        cpu.step()
                        edges[('jmp', pc, cpu.pc)] += 1
                        pend, pend_site, pend_img = 'jmp', pc, im.idx
                    elif op == 0x48 or op == 0x08:
                        cpu.step()
                        if cpu.s < ph.smin:
                            ph.smin = cpu.s
                    elif op == 0x60:
                        s0 = cpu.s
                        src = (stksrc[(s0 + 1) & 0xFF], stksrc[(s0 + 2) & 0xFF])
                        cpu.step()
                        tgt = (cpu.pc - 1) & 0xFFFF
                        if callstack and callstack[-1][1] == tgt and src == (ST_JSR, ST_JSR):
                            callstack.pop()
                            pend = 'rts'
                        else:
                            kind = 'jsr-bytes' if src == (ST_JSR, ST_JSR) else \
                                'pha' if ST_PHA in src else 'other:%d%d' % src
                            rts_odd[(pc, cpu.pc, kind)] += 1
                            pend = _RTSKIND.get(kind, 'rts_other')
                            kk = len(callstack) - 1
                            while kk >= 0 and callstack[kk][1] != tgt:
                                kk -= 1
                            if kk >= 0:
                                del callstack[kk:]
                        pend_site, pend_img = pc, im.idx
                    elif op == 0x6C:
                        cpu.step()
                        jmpi[(pc, cpu.pc)] += 1
                        pend, pend_site, pend_img = 'jmpi', pc, im.idx
                    elif op == 0x68 or op == 0x28:
                        if stksrc[(cpu.s + 1) & 0xFF] == ST_JSR:
                            pla_ret[(pc, op)] += 1
                            im.flags[off] = im.flags.get(off, 0) | FL_PULLRET
                        cpu.step()
                    elif op == 0x9A:
                        txs[pc] += 1
                        im.flags[off] = im.flags.get(off, 0) | FL_TXS
                        cpu.step()
                        if cpu.s < ph.smin:
                            ph.smin = cpu.s
                    elif op == 0x00 or op == 0x40:
                        brk_rti[(pc, op)] += 1
                        im.flags[off] = im.flags.get(off, 0) | (FL_BRK if op == 0 else FL_RTI)
                        cpu.step()
                        if cpu.s < ph.smin:
                            ph.smin = cpu.s
                        pend, pend_site, pend_img = ('brk' if op == 0 else 'rti'), pc, im.idx
                    else:
                        cpu.step()
                        if off + L > 0xFF and cpu.pc >> 8 != p:
                            pend, pend_site, pend_img = 'fall', pc, im.idx
            except IllegalOpcode as e:
                stop = 'illegal'
                say(f'STOP {e} frame {f}')
            ph.instr += n
            ph.cycles += cpu.cycles - c0
            if stop:
                break
            if max_instr and seq >= max_instr:
                stop = 'max_instr'
                break
            if f % 1000 == 999:
                say(f'frame {f + 1} ({time.time() - t0:.0f} s, pc ${cpu.pc:04X})', flush=True)
    finally:
        for nm, had, orig in (('write', had_write, orig_write), ('io', had_io, orig_io)):
            if had:
                setattr(m, nm, orig)
            else:
                m.__dict__.pop(nm, None)
    frames_run = f + 1 if frames else 0
    ph.f1 = frames_run
    wall = time.time() - t0

    # ------------------------------------------------------------------------------------------ coverage
    for im in images:
        if im.role is None:
            lo = im.page << 8
            im.role = bytes(role[lo:lo + 256])
    raw = [im for im in images if any(im.role)]
    bulk_w = {w for w, s in wset.items() if len(s) >= bulk_n}
    loaded = collections.Counter()
    wfr = collections.defaultdict(set)
    for im in raw:
        for o in range(256):
            if im.role[o] and o not in im.vol and im.lwp[o] >= 0:
                loaded[im.lwp[o]] += 1
                wfr[im.lwp[o]].add(im.lwf[o])
    loaders = {w for w, c in loaded.items() if c >= bulk_n or w in bulk_w}
    kept = []
    for w in loaders:
        fr = sorted(wfr.get(w, ()))
        for a0, a1 in wint.get(w, []):
            i = bisect.bisect_left(fr, a0)
            if i < len(fr) and fr[i] <= a1:
                kept.append((a0, a1, w))
    kept.sort()
    bursts = []
    for a0, a1, w in kept:
        if bursts and a0 - bursts[-1][1] <= gap:
            bursts[-1][1] = max(bursts[-1][1], a1)
            bursts[-1][2].add(w)
        else:
            bursts.append([a0, a1, {w}])
    starts = [b[0] for b in bursts]
    eps = {}            # key -> episode dict
    raw_ep = {}

    def load_frame(im):
        # latest loader write (else any write) before first execution of the bytes, preferring writes up to the
        # image's first execution frame (later identical rewrites of a live image do not move its load)
        for lim in (im.f0, 1 << 30):
            best = anyf = -1
            for o in range(256):
                x = im.lwf[o]
                if im.role[o] and o not in im.vol and 0 <= x <= lim:
                    if x > anyf:
                        anyf = x
                    if im.lwp[o] in loaders and x > best:
                        best = x
            if anyf >= 0:
                return best if best >= 0 else anyf
        return -1

    raw_lf = {}
    for im in raw:
        lf = load_frame(im)
        raw_lf[im.idx] = lf
        if lf < 0:
            key = ('initial',)
            ep = eps.setdefault(key, {'kind': 'initial', 'frames': [-1, -1], 'writers': set(), 'images': []})
        else:
            i = bisect.bisect_right(starts, lf) - 1
            if i >= 0 and bursts[i][0] <= lf <= bursts[i][1]:
                key = ('burst', i)
                ep = eps.setdefault(key, {'kind': 'burst', 'frames': [bursts[i][0], bursts[i][1]],
                                          'writers': set(bursts[i][2]), 'images': []})
            else:
                key = ('single', lf)
                ep = eps.setdefault(key, {'kind': 'single', 'frames': [lf, lf], 'writers': set(), 'images': []})
                ep['writers'] |= {im.lwp[o] for o in range(256) if im.role[o] and im.lwf[o] == lf}
        ep['images'].append(im.idx)
        raw_ep[im.idx] = key
    ep_list = sorted(eps.items(), key=lambda kv: (kv[1]['frames'][0], kv[0]))
    ep_id = {k: i for i, (k, _) in enumerate(ep_list)}
    fid = {im.idx: i for i, im in enumerate(raw)}
    episodes = [{'id': i, 'run': 0, 'kind': e['kind'], 'frames': e['frames'],
                 'writers': [f'{w:04X}' for w in sorted(e['writers'])],
                 'images': sorted(fid[x] for x in e['images'])} for i, (k, e) in enumerate(ep_list)]
    cimgs = []
    for im in raw:
        base = im.page << 8
        by = bytearray(im.snap)
        for o in range(256):
            if im.val[o] >= 0:
                by[o] = im.val[o]
        cimgs.append({
            'id': fid[im.idx], 'page': im.page, 'bytes': by, 'role': bytearray(im.role),
            'instr': collections.Counter({base + o: c for o, c in enumerate(im.ic) if c}),
            'ea': {base + o: v for o, v in enumerate(im.ea) if v},
            'flags': {base + o: v for o, v in im.flags.items()},
            'entries': collections.Counter({(t, k, s, fid.get(si, -1)): c for (t, k, s, si), c in im.ent.items()}),
            'smc': {base + o: {'tuples': collections.Counter({(t[0], '%02X%02X%02X' % t[1:]): c
                                                               for t, c in tc.items()}),
                               'writers': set(im.smcw.get(o, ())), 'changed_execs': im.smcn[o]}
                    for o, tc in im.smc.items()},
            'volatile': {base + o for o in im.vol},
            'data_writes': {base + o: {'n': d[0], 'changed': bool(d[1]), 'writers': set(d[3])}
                            for o, d in im.dw.items() if d[2] <= im.lastq},
            'writers': collections.Counter(im.lwp[o] for o in range(256) if im.role[o] and im.lwp[o] >= 0),
            'loads': [{'run': 0, 'episode': ep_id[raw_ep[im.idx]], 'load_frame': raw_lf[im.idx],
                       'first_frame': im.f0, 'last_frame': im.lastf}],
            'count': sum(im.ic),
        })
    writers = []
    for w in sorted(set(loaded) | {w for w, s in wset.items() if s}):
        writers.append({'run': 0, 'pc': f'{w:04X}', 'changed_exec_bytes': len(wset[w]) if w in wset else 0,
                        'loaded_bytes': loaded[w], 'bulk': w in bulk_w, 'loader': w in loaders,
                        'active': wint.get(w, []) if w in loaders else []})
    rom_exec = collections.Counter()
    io_tot = collections.Counter()
    for p_ in phases:
        for pc in range(0xC000, 0x10000):
            if p_.cnt[pc]:
                rom_exec[pc] += p_.cnt[pc]
        io_tot.update(p_.io)
    smin = min(p_.smin for p_ in phases)
    run = {'index': 0, 'name': sc['name'], 'frames': frames, 'frames_run': frames_run,
           'stop': {'reason': stop or 'frames', 'pc': f'{cpu.pc:04X}', 'frame': f},
           'phases': [[p_.name, p_.f0, p_.f1] for p_ in phases],
           'instr': sum(p_.instr for p_ in phases), 'cycles': sum(p_.cycles for p_ in phases),
           'wall_s': round(wall, 2), 'min_s': smin, 'bulk': bulk_n, 'episode_gap': gap,
           'io': {f'{rw}:{a:04X}': c for (rw, a), c in sorted(io_tot.items())}}
    cov_int = {'runs': [run], 'episodes': episodes, 'images': cimgs, 'writers': writers,
               'rom_entries': collections.Counter({(k, s, fid.get(si, -1), t): c
                                                   for (k, s, si, t), c in rom_ent.items()}),
               'rom_exec': rom_exec}
    coverage = _merge_int([cov_int])

    # ------------------------------------------------------------------------------------------ report
    for p_ in phases:
        reg = collections.Counter()
        for pc in range(65536):
            c = p_.cnt[pc]
            if c:
                reg[region_of(pc)] += c
        p_.region = reg
    out = {'frames': frames_run, 'phases': [], 'bulk_writers': sorted(bulk_w)}
    for p_ in phases:
        fr = p_.f1 - p_.f0
        secs = fr / 60.0
        executed = [pc for pc in range(65536) if p_.cnt[pc]]
        pages = collections.Counter()
        for pc in executed:
            pages[pc >> 8] += p_.cnt[pc]
        hires = sum(p_.pw[0x20:0x60])
        stores = sum(p_.pw)
        io_rate = {f'{k[0]}:{k[1]:04X}': v for k, v in sorted(p_.io.items())}
        spk = sum(v for (rw, ad), v in p_.io.items() if 0xC030 <= ad <= 0xC03F)
        out['phases'].append({
            'name': p_.name, 'frames': [p_.f0, p_.f1], 'instr': p_.instr, 'cycles': p_.cycles,
            'instr_per_s': p_.instr / secs if secs else 0,
            'cyc_per_instr': p_.cycles / p_.instr if p_.instr else 0,
            'distinct_pcs': len(executed),
            'mix': [[mn, md, c] for (mn, md), c in p_.mix.most_common()],
            'routines': [[f'{r:04X}', c] for r, c in p_.rout.most_common(60)],
            'pages': [[f'{p:02X}', c] for p, c in pages.most_common(64)],
            'hot_pcs': [[f'{pc:04X}', p_.cnt[pc]] for pc in sorted(executed, key=lambda q: -p_.cnt[q])[:200]],
            'region': dict(p_.region),
            'stores': stores, 'hires_stores': hires, 'hires_stores_per_frame': hires / fr if fr else 0,
            'stores_per_s': stores / secs if secs else 0,
            'code_page_stores': p_.codepw, 'code_byte_stores': p_.codebw,
            'store_modes': dict(p_.stores_ind),
            'page_stores': {f'{p:02X}': c for p, c in enumerate(p_.pw) if c},
            'io': io_rate, 'speaker_per_s': spk / secs if secs else 0,
            'flips': p_.flips, 'flips_per_s': len(p_.flips) / secs if secs else 0,
            'dec_adc_sbc': p_.dec, 'zp_wraps': p_.zpwrap, 'smin': p_.smin, 'taken_branches': p_.ntaken,
            'edges': [[k, f'{a:04X}', f'{b:04X}', c] for (k, a, b), c in p_.edges.items()],
        })
    out['images'] = [{'page': im['page'], 'f0': im['loads'][0]['first_frame'], 'nbytes': im['nbytes'],
                      'count': im['count'], 'loads': len(im['loads'])} for im in coverage['images']]
    out['raw_images'] = len(raw)
    out['smc_exec'] = [[f'{b:04X}', k, f'{w:04X}', bulk, c] for (b, k, w, bulk), c in smc_exec.most_common()]
    out['code_writes'] = [[f'{b:04X}', f'{w:04X}', r, c] for (b, w, r), c in codew.most_common()
                          if w not in bulk_w]
    out['code_writes_bulk'] = sum(c for (b, w, r), c in codew.items() if w in bulk_w)
    out['overlap'] = [[f'{b:04X}', r, f'{pc:04X}', c] for (b, r, pc), c in overlap.most_common()]
    out['jmp_ind'] = [[f'{s:04X}', f'{t:04X}', c] for (s, t), c in jmpi.most_common()]
    out['rts_odd'] = [[f'{s:04X}', f'{t:04X}', k, c] for (s, t, k), c in rts_odd.most_common()]
    out['pla_ret'] = [[f'{s:04X}', o, c] for (s, o), c in pla_ret.most_common()]
    out['txs'] = [[f'{s:04X}', c] for s, c in txs.most_common()]
    out['brk_rti'] = [[f'{s:04X}', o, c] for (s, o), c in brk_rti.most_common()]
    out['site_ea'] = {f'{pc:04X}': v for pc, v in sorted(site_ea.items())}
    return Profile(coverage, out)


# ------------------------------------------------------------------------------------------------- merging
def _h4(a):
    return f'{a:04X}'


def _img_from_json(d):
    return {
        'id': d['id'], 'page': int(d['page'], 16), 'bytes': bytearray.fromhex(d['bytes']),
        'role': bytearray(ROLE_CH.index(c) for c in d['role']),
        'instr': collections.Counter({int(a, 16): c for a, c in d['instr'].items()}),
        'ea': {int(a, 16): v for a, v in d['ea'].items()},
        'flags': {int(a, 16): v for a, v in d['flags'].items()},
        'entries': collections.Counter({(int(t, 16), k, -1 if s is None else int(s, 16), si): c
                                        for t, k, s, si, c in d['entries']}),
        'smc': {int(a, 16): {'tuples': collections.Counter({(L, h): c for L, h, c in s['tuples']}),
                             'writers': {int(w, 16) for w in s['writers']}, 'changed_execs': s['changed_execs']}
                for a, s in d['smc'].items()},
        'volatile': {int(a, 16) for a in d['volatile']},
        'data_writes': {int(a, 16): {'n': w['n'], 'changed': w['changed'], 'writers': {int(x, 16) for x in w['writers']}}
                        for a, w in d['data_writes'].items()},
        'writers': collections.Counter({int(a, 16): c for a, c in d['writers'].items()}),
        'loads': [dict(x) for x in d['loads']],
        'count': d['count'],
    }


def _img_to_json(x):
    vol = x['volatile']
    return {
        'id': x['id'], 'page': f"{x['page']:02X}", 'bytes': x['bytes'].hex().upper(),
        'role': ''.join(ROLE_CH[r] for r in x['role']),
        'nbytes': sum(1 for r in x['role'] if r), 'count': x['count'],
        'instr': {_h4(a): c for a, c in sorted(x['instr'].items())},
        'ea': {_h4(a): v for a, v in sorted(x['ea'].items())},
        'flags': {_h4(a): v for a, v in sorted(x['flags'].items())},
        'entries': [[_h4(t), k, None if s < 0 else _h4(s), si, c]
                    for (t, k, s, si), c in sorted(x['entries'].items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2],
                                                                                       kv[0][3]))],
        'smc': {_h4(a): {'kind': 'opcode' if a in vol else 'operand',
                         'tuples': [[L, h, c] for (L, h), c in sorted(s['tuples'].items())],
                         'writers': [_h4(w) for w in sorted(s['writers'])], 'changed_execs': s['changed_execs']}
                for a, s in sorted(x['smc'].items())},
        'volatile': [_h4(a) for a in sorted(vol)],
        'data_writes': {_h4(a): {'n': w['n'], 'changed': w['changed'], 'writers': [_h4(v) for v in sorted(w['writers'])]}
                        for a, w in sorted(x['data_writes'].items())},
        'writers': {_h4(a): c for a, c in sorted(x['writers'].items())},
        'loads': x['loads'],
        'episodes': sorted({ld['episode'] for ld in x['loads']}),
    }


def _compatible(g, x):
    """same code: executed bytes of either (minus SMC-volatile bytes) agree"""
    vol = g['volatile'] | x['volatile']
    base = g['page'] << 8
    gb, xb, gr, xr = g['bytes'], x['bytes'], g['role'], x['role']
    for o in range(256):
        if (gr[o] or xr[o]) and gb[o] != xb[o] and (base + o) not in vol:
            return False
    return True


def _absorb(g, x):
    for o in range(256):
        if x['role'][o] and not g['role'][o]:
            g['bytes'][o] = x['bytes'][o]
        g['role'][o] |= x['role'][o]
    g['instr'].update(x['instr'])
    for a, v in x['ea'].items():
        g['ea'][a] = g['ea'].get(a, 0) | v
    for a, v in x['flags'].items():
        g['flags'][a] = g['flags'].get(a, 0) | v
    g['entries'].update(x['entries'])
    for a, s in x['smc'].items():
        t = g['smc'].setdefault(a, {'tuples': collections.Counter(), 'writers': set(), 'changed_execs': 0})
        t['tuples'].update(s['tuples'])
        t['writers'] |= s['writers']
        t['changed_execs'] += s['changed_execs']
    g['volatile'] |= x['volatile']
    for a, w in x['data_writes'].items():
        t = g['data_writes'].setdefault(a, {'n': 0, 'changed': False, 'writers': set()})
        t['n'] += w['n']
        t['changed'] = t['changed'] or w['changed']
        t['writers'] |= w['writers']
    g['writers'].update(x['writers'])
    g['loads'] += x['loads']
    g['count'] += x['count']


def _merge_int(covs):
    """merge internal-form coverages (images as dicts with bytearrays / Counters) -> JSON-ready coverage"""
    runs, episodes, imgs, writers = [], [], [], []
    rom_entries, rom_exec = collections.Counter(), collections.Counter()
    for cov in covs:
        r0, e0, i0 = len(runs), len(episodes), len(imgs)
        for r in cov['runs']:
            r = dict(r)
            r['index'] = r['index'] + r0
            runs.append(r)
        for e in cov['episodes']:
            e = dict(e)
            e['id'] += e0
            e['run'] += r0
            e['images'] = [i + i0 for i in e['images']]
            episodes.append(e)
        for x in cov['images']:
            x['id'] += i0
            x['entries'] = collections.Counter({(t, k, s, si + i0 if si >= 0 else -1): c
                                                for (t, k, s, si), c in x['entries'].items()})
            for ld in x['loads']:
                ld['run'] += r0
                ld['episode'] += e0
            imgs.append(x)
        for w in cov['writers']:
            w = dict(w)
            w['run'] += r0
            writers.append(w)
        for (k, s, si, t), c in cov['rom_entries'].items():
            rom_entries[(k, s, si + i0 if si >= 0 else -1, t)] += c
        rom_exec.update(cov['rom_exec'])
    # identity merge, per page in order of first execution
    order = sorted(range(len(imgs)), key=lambda i: (imgs[i]['page'], min((ld['run'], ld['first_frame'])
                                                                         for ld in imgs[i]['loads']), i))
    groups = collections.defaultdict(list)
    gof = {}
    for i in order:
        x = imgs[i]
        for g in groups[x['page']]:
            if _compatible(g, x):
                _absorb(g, x)
                gof[x['id']] = g
                break
        else:
            g = x
            g['loads'] = list(g['loads'])
            groups[x['page']].append(g)
            gof[x['id']] = g
    final = [g for p in sorted(groups) for g in groups[p]]
    newid = {id(g): n for n, g in enumerate(final)}
    remap = {old: newid[id(g)] for old, g in gof.items()}
    for g in final:
        g['entries'] = collections.Counter(_remap_entries(g['entries'], remap))
        g['id'] = newid[id(g)]
        g['loads'].sort(key=lambda ld: (ld['run'], ld['first_frame']))
    for e in episodes:
        e['images'] = sorted({remap[i] for i in e['images']})
    re2 = collections.Counter()
    for (k, s, si, t), c in rom_entries.items():
        re2[(k, s, remap.get(si, -1) if si >= 0 else -1, t)] += c
    cov = {
        'format': FORMAT, 'version': VERSION,
        'runs': runs,
        'min_s': min((r['min_s'] for r in runs), default=0xFF),
        'images': [_img_to_json(g) for g in final],
        'episodes': episodes,
        'writers': writers,
        'bulk_writers': sorted({w['pc'] for w in writers if w['bulk']}),
        'loaders': sorted({w['pc'] for w in writers if w['loader']}),
        'rom_entries': [[k, None if s < 0 else _h4(s), si, _h4(t), c] for (k, s, si, t), c in sorted(re2.items())],
        'rom_exec': {_h4(a): c for a, c in sorted(rom_exec.items())},
    }
    cov['summary'] = summarize(cov)
    return cov


def _remap_entries(ent, remap):
    out = collections.Counter()
    for (t, k, s, si), c in ent.items():
        out[(t, k, s, remap[si] if si >= 0 else -1)] += c
    return out


def merge_coverage(covs):
    """union of several coverage dicts (JSON form, e.g. loaded from files); image ids are renumbered and every
    reference (entries, episodes, rom_entries) is remapped; identical images (docs/COVERAGE.md) are merged"""
    ints = []
    for cov in covs:
        if cov.get('format') != FORMAT or cov.get('version') != VERSION:
            raise ValueError('not an a2cc3 coverage file (version %r)' % cov.get('version'))
        ints.append({
            'runs': cov['runs'], 'episodes': [dict(e) for e in cov['episodes']],
            'images': [_img_from_json(d) for d in cov['images']],
            'writers': cov['writers'],
            'rom_entries': collections.Counter({(k, -1 if s is None else int(s, 16), si, int(t, 16)): c
                                                for k, s, si, t, c in cov['rom_entries']}),
            'rom_exec': collections.Counter({int(a, 16): c for a, c in cov['rom_exec'].items()}),
        })
    return _merge_int(ints)


# ------------------------------------------------------------------------------------------------- checks
def validate_coverage(cov):
    """structural consistency of a coverage dict; raises ValueError"""
    def chk(c, msg):
        if not c:
            raise ValueError(msg)
    chk(cov.get('format') == FORMAT and cov.get('version') == VERSION, 'format/version')
    n, ne, nr = len(cov['images']), len(cov['episodes']), len(cov['runs'])
    for i, r in enumerate(cov['runs']):
        chk(r['index'] == i, f'run index {i}')
    epimg = collections.defaultdict(set)
    for i, e in enumerate(cov['episodes']):
        chk(e['id'] == i and 0 <= e['run'] < nr, f'episode {i} id/run')
        chk(e['kind'] in ('initial', 'burst', 'single'), f'episode {i} kind')
        for x in e['images']:
            chk(0 <= x < n, f'episode {i} image {x}')
            epimg[i].add(x)
    for i, im in enumerate(cov['images']):
        chk(im['id'] == i, f'image id {i}')
        page = int(im['page'], 16)
        chk(page < 0xC0 and len(im['bytes']) == 512 and len(im['role']) == 256, f'image {i} page/bytes/role')
        chk(set(im['role']) <= set(ROLE_CH), f'image {i} role chars')
        onp = lambda a: int(a, 16) >> 8 == page    # noqa: E731
        chk(im['nbytes'] == sum(c != '.' for c in im['role']), f'image {i} nbytes')
        for a, c in im['instr'].items():
            chk(onp(a) and im['role'][int(a, 16) & 0xFF] in 'ox' and c > 0, f'image {i} instr {a}')
        chk(sum(im['instr'].values()) == im['count'], f'image {i} count')
        for a in list(im['ea']) + list(im['flags']) + list(im['smc']):
            chk(a in im['instr'], f'image {i} site {a} not an instruction start')
        for t, k, s, si, c in im['entries']:
            chk(t in im['instr'] and k in ENTRY_KINDS and -1 <= si < n and c > 0, f'image {i} entry {t} {k}')
            chk((si >= 0) == (s is not None and int(s, 16) < 0xC000) or k == 'start', f'image {i} entry site {s}')
        for a in im['volatile'] + list(im['data_writes']):
            chk(onp(a), f'image {i} address {a} off page')
        for a, s in im['smc'].items():
            chk(s['kind'] in ('opcode', 'operand') and s['tuples'], f'image {i} smc {a}')
            for L, h, c in s['tuples']:
                chk(L in (1, 2, 3) and len(h) == 6 and c > 0, f'image {i} smc tuple {a}')
        chk(im['loads'] and im['episodes'] == sorted({ld['episode'] for ld in im['loads']}), f'image {i} loads')
        for ld in im['loads']:
            chk(0 <= ld['episode'] < ne and 0 <= ld['run'] < nr and i in epimg[ld['episode']],
                f'image {i} load episode {ld["episode"]}')
            chk(cov['episodes'][ld['episode']]['run'] == ld['run'], f'image {i} load run')
    for k, s, si, t, c in cov['rom_entries']:
        chk(-1 <= si < n and int(t, 16) >= 0xC000, f'rom entry {k} {s} {t}')
    return True


def summarize(cov):
    ent_kinds = collections.Counter()
    ent_events = collections.Counter()
    smc = collections.Counter()
    variants = 0
    ex = ins = 0
    dw_exec = 0
    for im in cov['images']:
        ex += im['nbytes']
        ins += len(im['instr'])
        seen = set()
        for t, k, s, si, c in im['entries']:
            ent_events[k] += c
            if (t, k) not in seen:
                seen.add((t, k))
                ent_kinds[k] += 1
        for a, s in im['smc'].items():
            smc[s['kind']] += 1
            variants += len(s['tuples'])
        dw_exec += sum(1 for a in im['data_writes'] if im['role'][int(a, 16) & 0xFF] != '.')
    rom_calls = collections.Counter()
    for k, s, si, t, c in cov['rom_entries']:
        if si >= 0:
            rom_calls[t] += c
    return {
        'runs': len(cov['runs']), 'frames': sum(r['frames_run'] for r in cov['runs']),
        'instr': sum(r['instr'] for r in cov['runs']), 'wall_s': round(sum(r['wall_s'] for r in cov['runs']), 1),
        'images': len(cov['images']), 'pages': len({im['page'] for im in cov['images']}),
        'overlay_pages': sorted(p for p, c in collections.Counter(im['page'] for im in cov['images']).items() if c > 1),
        'executed_bytes': ex, 'instruction_starts': ins,
        'entries_distinct': dict(ent_kinds), 'entries_events': dict(ent_events),
        'rom_call_targets': len(rom_calls),
        'smc_sites': dict(smc), 'smc_variants': variants,
        'data_written_exec_bytes': dw_exec,
        'episodes': len(cov['episodes']), 'loaders': len(cov['loaders']), 'bulk_writers': len(cov['bulk_writers']),
        'min_s': cov['min_s'],
    }


def print_summary(cov, out=sys.stdout):
    s = cov['summary']
    print(f"coverage: {s['runs']} run(s), {s['frames']} frames, {s['instr']} instructions, {s['wall_s']} s", file=out)
    print(f"  images {s['images']} on {s['pages']} pages (overlays: {' '.join(s['overlay_pages']) or '-'}), "
          f"executed bytes {s['executed_bytes']}, instruction starts {s['instruction_starts']}", file=out)
    print('  entries (distinct targets): ' + ', '.join(f'{k} {s["entries_distinct"].get(k, 0)}' for k in ENTRY_KINDS
                                                       if s['entries_distinct'].get(k)) +
          f"; ROM call targets {s['rom_call_targets']}", file=out)
    print(f"  SMC sites: opcode {s['smc_sites'].get('opcode', 0)}, operand {s['smc_sites'].get('operand', 0)} "
          f"({s['smc_variants']} tuples); data-written executed bytes {s['data_written_exec_bytes']}", file=out)
    print(f"  episodes {s['episodes']}, loaders {s['loaders']}, bulk writers {s['bulk_writers']}, "
          f"min S ${s['min_s']:02X}", file=out)
    for e in cov['episodes']:
        pages = sorted({cov['images'][i]['page'] for i in e['images']})
        print(f"    episode {e['id']:3d} run {e['run']} {e['kind']:7s} frames {e['frames'][0]}-{e['frames'][1]}: "
              f"{len(e['images'])} images, pages {_ranges(pages)}", file=out)


def _ranges(pages):
    ps = sorted(int(p, 16) for p in pages)
    out, i = [], 0
    while i < len(ps):
        j = i
        while j + 1 < len(ps) and ps[j + 1] == ps[j] + 1:
            j += 1
        out.append(f'{ps[i]:02X}' if i == j else f'{ps[i]:02X}-{ps[j]:02X}')
        i = j + 1
    return ' '.join(out)


# ------------------------------------------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('dsk', nargs='?')
    ap.add_argument('--rom', help='Apple ROM image whose last 16K is $C000-$FFFF (or 12K $D000-)')
    ap.add_argument('--prom')
    ap.add_argument('--scenario', action='append', default=[], help='scenario JSON file (repeatable)')
    ap.add_argument('--name', default='run')
    ap.add_argument('--frames', type=int, default=6000)
    ap.add_argument('--keys', default='', help='frame:hexkey,...')
    ap.add_argument('--joy', default='', help='frame:x:y:buttons,... (paddles 0-255, buttons bitmask)')
    ap.add_argument('--joyfile', default='', help='file holding a --joy script')
    ap.add_argument('--phase', action='append', default=[], help='NAME:FIRSTFRAME (repeatable, in order)')
    ap.add_argument('--bulk', type=int, default=None)
    ap.add_argument('--episode-gap', type=int, default=None)
    ap.add_argument('--out', help='directory for profile.json (per-phase report)')
    ap.add_argument('--coverage', help='write the (merged) coverage file here')
    ap.add_argument('--merge', action='append', default=[], help='coverage file to merge in (repeatable)')
    a = ap.parse_args(argv)
    scens = [load_scenario(p) for p in a.scenario]
    if not scens and a.dsk:
        scens.append(normalize_schedule({'name': a.name, 'frames': a.frames, 'keys': a.keys, 'joy': a.joy,
                                         'joyfile': os.path.abspath(a.joyfile) if a.joyfile else '',
                                         'phases': a.phase}))
    if not scens and not a.merge:
        ap.error('nothing to do: give a disk / --scenario and/or --merge')
    covs = []
    for p in a.merge:
        with open(p) as fh:
            covs.append(json.load(fh))
    for sc in scens:
        if a.bulk is not None:
            sc['bulk'] = a.bulk
        if a.episode_gap is not None:
            sc['episode_gap'] = a.episode_gap
        dsk, rom, prom = a.dsk or sc.get('disk'), a.rom or sc.get('rom'), a.prom or sc.get('prom')
        if not (dsk and rom and prom):
            ap.error(f'scenario {sc["name"]}: disk, --rom and --prom are required')
        m = make_machine(rom, prom, dsk)
        m.boot()
        print(f'scenario {sc["name"]}: {sc["frames"]} frames', flush=True)
        res = profile_machine(m, sc, log=print)
        covs.append(res.coverage)
        if a.out:
            os.makedirs(a.out, exist_ok=True)
            fn = 'profile.json' if len(scens) == 1 else f'profile_{sc["name"]}.json'
            with open(os.path.join(a.out, fn), 'w') as fh:
                json.dump(res.report, fh)
            print(f'report: {os.path.join(a.out, fn)}')
    cov = covs[0] if len(covs) == 1 and scens else merge_coverage(covs)
    validate_coverage(cov)
    if a.coverage:
        d = os.path.dirname(os.path.abspath(a.coverage))
        os.makedirs(d, exist_ok=True)
        with open(a.coverage, 'w') as fh:
            json.dump(cov, fh, separators=(',', ':'))
        print(f'coverage: {a.coverage}')
    print_summary(cov)


if __name__ == '__main__':
    main()
