"""Differential test: translated 6809 code (run in sim/cc3sim) against the 6502 reference (kit/host
cpu6502), routine by routine, from real memory images. Game-independent.

Layout in cc3sim (task 0, default blocks $38-$3F): Apple RAM $0000-$BFFF (phys $70000), test runtime + the
translated code from $C000 (phys $7C000), I/O block at $FC00 (in/out records).
Every transfer that leaves the translated set (native routine, other code set, trap) is a stub that stops the
run; the reference stops when its PC leaves the set.  A run also stops when the routine returns to the driver.

Compared at the stop: where it stopped (6502 address or 'ret'), the 6502 registers and N/Z/C flags that are
live there (liveness from xc65.Live), and Apple RAM $0000-$00FF, $0200-$BFFF (the 6502 stack page differs by
design: the 6809 stack is elsewhere).  I/O reads return 0 on both sides; ROM constants are served by the
reference bus.
"""
import os
import random
import re
import subprocess
import tempfile

from .asm import assemble
from .xc65 import A_, X_, Y_, N_, Z_, C_, FLAGS

HERE = os.path.dirname(os.path.abspath(__file__))
from .paths import SIM  # noqa: E402
IOBASE = 0xFC00            # in: a x y p entry(2)   out: a x y cc stop(2) s(2)
SENTINEL = 0xFFF0

RUNTIME = r'''
RYW	equ	$FBF0
RY	equ	$FBF1
IOV	equ	$FBF2
in_a	equ	$FC00
in_x	equ	$FC01
in_y	equ	$FC02
in_cc	equ	$FC03
in_entry	equ	$FC04
out_a	equ	$FC10
out_x	equ	$FC11
out_y	equ	$FC12
out_cc	equ	$FC13
out_stop	equ	$FC14
out_s	equ	$FC16
tstart	orcc	#$50
	lds	#$FBE0
	clra
	tfr	a,dp
	clr	RYW
	ldb	in_y
	stb	RY
	lda	in_cc
	pshs	a
	lda	in_a
	ldb	in_x
	puls	cc
	jsr	[in_entry]
	pshs	cc
	ldx	#$FFFF
tcom	stx	out_stop
	puls	cc
	pshs	cc
	sta	out_a
	stb	out_x
	puls	a
	sta	out_cc
	sts	out_s
	lda	RY
	sta	out_y
thalt	bra	thalt
xbitb	pshs	b,cc
	ldb	,s
	andb	#$F1
	stb	,s
	ldb	1,s
	bpl	xb1
	ldb	,s
	orb	#$08
	stb	,s
xb1	ldb	1,s
	pshs	a
	andb	,s+
	bne	xb2
	ldb	,s
	orb	#$04
	stb	,s
xb2	puls	cc,b,pc
'''


class Harness:
    """code_lines: translated source (one code set); stubs: {symbol: 6502 address} for every external
    symbol it references (natives, other sets, x_trap -> None); io symbols are generated"""

    def __init__(self, code_lines, stubs, extra_lines=(), workdir=None):
        self.wd = workdir or tempfile.mkdtemp(prefix='k3dt_')
        lines = ['\tpragma\tnoautobranchlength', '\tsetdp\t0', '\torg\t$C000'] + RUNTIME.splitlines()
        self.stub_id = {}
        self.stubs_by_sym = {}
        k = 1
        used = set()
        for ln in list(code_lines) + list(extra_lines):
            p = ln.split('\t')
            if len(p) > 2:
                used |= set(re.findall(r'[A-Za-z_][A-Za-z_0-9]*', p[2]))
        for s in sorted(used):
            if s.startswith('io_rd'):
                lines += [f'{s}\tpshs\tcc', '\tclr\tIOV', '\tpuls\tcc,pc']
            elif s.startswith('io_wr'):
                lines += [f'{s}\trts']
            elif s == 'hr_rd':                  # hi-res access helpers (HIRES_ACCESS): flat memory here
                lines += ['hr_rd\tpshs\tcc,a', '\tlda\t,x', '\tsta\tIOV', '\tpuls\tcc,a,pc']
            elif s == 'hr_wr':
                lines += ['hr_wr\tpshs\tcc,a', '\tcmpx\t#$C000', '\tbhs\thrw_x', '\tlda\tIOV', '\tsta\t,x',
                          'hrw_x\tpuls\tcc,a,pc']        # ROM: dropped like the runtime's
        for s, a in sorted(stubs.items()):
            if s not in used:
                continue
            self.stub_id[k] = a
            self.stubs_by_sym[s] = a
            lines += [f'{s}\tpshs\tcc', f'\tldx\t#{k}', '\tjmp\ttcom']
            k += 1
        lines += list(extra_lines) + list(code_lines)
        self.bin = os.path.join(self.wd, 'code.bin')
        self.sym = os.path.join(self.wd, 'code.sym')
        assemble(lines, self.bin, symbols=self.sym)
        self.syms = {}
        for ln in open(self.sym):
            m = re.match(r'(\S+) EQU \$([0-9A-F]+)', ln)
            if m:
                self.syms[m.group(1)] = int(m.group(2), 16)
        self.size = os.path.getsize(self.bin)
        self.image = open(self.bin, 'rb').read()
        if 0xC000 + self.size > 0xFBE0 - 0x100:
            raise RuntimeError(f'test image too big: {self.size}')

    def run(self, ram, label, a, x, y, p, max_instr=3000000, watch=()):
        """-> dict(stop=6502 addr or 'ret' or 'timeout', a, x, y, p6502, ram)"""
        rb = os.path.join(self.wd, 'ram.bin')
        open(rb, 'wb').write(bytes(ram[:0xC000]))
        cc = (0x08 if p & 0x80 else 0) | (0x04 if p & 0x02 else 0) | (0x01 if p & 0x01 else 0) | 0x50
        ent = self.syms[label]
        io = bytes([a, x, y, cc, ent >> 8, ent & 0xFF]) + bytes(26)
        ib = os.path.join(self.wd, 'io.bin')
        open(ib, 'wb').write(io)
        orb, oib = os.path.join(self.wd, 'oram.bin'), os.path.join(self.wd, 'oio.bin')
        ohb = os.path.join(self.wd, 'ohi.bin')
        r = subprocess.run([SIM, '-l', f'70000:{rb}', '-l', f'7C000:{self.bin}', '-l', f'7FC00:{ib}',
                            '-pc', f'{self.syms["tstart"]:04X}', '-stop', f'{self.syms["thalt"]:04X}',
                            '-max', str(max_instr), '-d', f'70000:C000:{orb}', '-d', f'7FC00:20:{oib}',
                            '-d', f'7C000:3C00:{ohb}'] + [w for a in watch for w in ('-w', f'{a:04X}')],
                           capture_output=True, text=True)
        cyc = re.search(r'stop pc=\S+ cycles=(\d+)', r.stdout) or re.search(r'cycles=(\d+)', r.stdout)
        if r.returncode != 0:
            return {'stop': 'timeout', 'cycles': int(cyc.group(1)) if cyc else 0}
        o = open(oib, 'rb').read()
        stop = o[0x14] << 8 | o[0x15]
        occ = o[0x13]
        pf = (0x80 if occ & 0x08 else 0) | (0x02 if occ & 0x04 else 0) | (0x01 if occ & 0x01 else 0)
        return {'stop': 'ret' if stop == 0xFFFF else self.stub_id.get(stop, f'?{stop}'),
                'a': o[0x10], 'x': o[0x11], 'y': o[0x12], 'p': pf, 'ram': open(orb, 'rb').read(),
                'hi': open(ohb, 'rb').read(), 'image': self.image,
                'cycles': int(cyc.group(1)),
                'writes': [(int(m.group(1), 16), int(m.group(2), 16), int(m.group(3))) for m in
                           re.finditer(r'^w ([0-9A-F]{4}) v=([0-9A-F]{2}) cycles=(\d+)$', r.stdout, re.M)]}


def untested(coverage, ranges, pcs, mems=(None,)):
    """instructions of the replaced ranges that the game ran (coverage profile) but no test case reached:
    -> sorted addresses.  A native test should reach every path the game uses (each exit, mode, clip case).
    mems: the test's snapshot images (only profile images whose code matches them count)"""
    from .listing import load_cov
    instr = set()
    for m in mems:
        instr |= set(load_cov(coverage, None if m is None else bytes(m) + bytes(0x10000 - len(m)))[0])
    inside = set().union(*[range(lo, hi + 1) for lo, hi in ranges])
    return sorted(a for a in instr if a in inside and a not in pcs)


class RefBus:
    def __init__(self, ram, romconst, rom_writes='wild', io=None):
        self.io = io            # optional I/O model: read(addr, cycles) -> byte, write(addr, value, cycles)
        self.cpu = None
        self.ram = bytearray(ram[:0xC000]) + bytearray(0x4000)
        self.romconst = romconst
        self.wild = False       # touched ROM the port does not model (random inputs sent a pointer there)
        # 'wild': a write above $C100 marks the case as a random-input artifact (engine tests);
        # 'ignore': the Apple drops it (ROM), recorded in romw: games use ROM as a write sink on purpose, and the
        # code under test must drop it too (on the CoCo that address range is the runtime / code banks)
        self.rom_writes = rom_writes
        self.romw = set()

    indirect = False            # the current instruction is (zp),Y / (zp,X): an I/O address there is a stray pointer

    def read(self, a):
        if 0xC000 <= a <= 0xC0FF:
            if self.indirect:
                self.wild = True
            return self.io.read(a, self.cpu.cycles) if self.io is not None else 0
        if a >= 0xC000:
            if a not in self.romconst:
                self.wild = True
            return self.romconst.get(a, 0)
        return self.ram[a]

    def write(self, a, v):
        if self.io is not None and 0xC000 <= a <= 0xC0FF:
            self.io.write(a, v, self.cpu.cycles)
        if a < 0xC000:
            self.ram[a] = v
        elif a >= 0xC100:
            if self.rom_writes == 'ignore':
                self.romw.add(a)
            else:
                self.wild = True


_ZPIDX = None


def reference(ram, entry, a, x, y, p, inside, romconst, max_instr=300000, pcs=None, rom_writes='wild',
              wrapsafe=None, io=None):
    """run the 6502 from entry until the PC leaves `inside` (a set) or returns to the sentinel.
    pcs: a set that collects every executed instruction address (path coverage of a test, see untested())
    rom_writes: see RefBus ('ignore' for native tests: the result's 'romw' lists the dropped ROM writes)
    wrapsafe: when a set (the profile's zp-wrap sites), a zp,X / zp,Y access whose index wraps past $FF at any
    other site stops the case as 'zpwrap': the translation assumes no wrap there (the profile never saw one),
    so random inputs that make one are artifacts, like 'wild' pointers"""
    global _ZPIDX
    if _ZPIDX is None:
        from . import opcodes
        _ZPIDX = {op: (1 if md == 'zpx' else 2) for op, (mn, md) in opcodes.OPS.items() if md in ('zpx', 'zpy')}
    from .host.cpu6502 import CPU6502, IllegalOpcode
    bus = RefBus(ram, romconst, rom_writes, io)
    c = CPU6502(bus)
    bus.cpu = c
    r = SENTINEL - 1
    bus.ram[0x1FF], bus.ram[0x1FE] = r >> 8, r & 0xFF
    c.s, c.pc, c.a, c.x, c.y, c.p = 0xFD, entry, a, x, y, (p & 0xC3) | 0x24
    n = 0
    while True:
        if bus.wild:
            return {'stop': 'wild', 'cycles': c.cycles}
        if c.pc == SENTINEL:
            stop = 'ret'
            break
        if c.pc not in inside:
            stop = c.pc
            break
        op = bus.ram[c.pc] if c.pc < 0xC000 else 0
        if pcs is not None:
            pcs.add(c.pc)
        if wrapsafe is not None and op in _ZPIDX and c.pc < 0xC000 and c.pc not in wrapsafe:
            if bus.ram[(c.pc + 1) & 0xFFFF] + (c.x if _ZPIDX[op] == 1 else c.y) > 0xFF:
                return {'stop': 'zpwrap', 'cycles': c.cycles}
        bus.indirect = (op & 0x1F) in (0x01, 0x11)
        try:
            c.step()
        except IllegalOpcode:               # random inputs drove the 6502 into data: not a comparable case
            return {'stop': 'illegal', 'cycles': c.cycles}
        n += 1
        if n > max_instr:
            return {'stop': 'timeout', 'cycles': c.cycles}
    return {'stop': stop, 'a': c.a, 'x': c.x, 'y': c.y, 'p': c.p & 0x83, 'ram': bytes(bus.ram[:0xC000]),
            'cycles': c.cycles, 'n': n, 'romw': bus.romw}


def compare(ref, got, live, ignore=()):
    """-> list of differences (empty = same).  live: mask of A/X/Y/N/Z/C to compare; ignore: RAM addresses
    not compared (besides the stack page)"""
    d = []
    if ref['stop'] != got['stop']:
        return [f"stop ref={ref['stop'] if isinstance(ref['stop'], str) else hex(ref['stop'])} "
                f"got={got['stop'] if isinstance(got['stop'], str) else hex(got['stop'])}"]
    for r, m in (('a', A_), ('x', X_), ('y', Y_)):
        if live & m and ref[r] != got[r]:
            d.append(f'{r.upper()} ref={ref[r]:02X} got={got[r]:02X}')
    for f, m, bit in (('N', N_, 0x80), ('Z', Z_, 0x02), ('C', C_, 0x01)):
        if live & m and (ref['p'] & bit) != (got['p'] & bit):
            d.append(f'flag {f} ref={int(bool(ref["p"] & bit))}')
    hit = [a for a in sorted(ref.get('romw', ())) if 'hi' in got and a - 0xC000 < len(got['hi'])
           and got['hi'][a - 0xC000] != (got['image'][a - 0xC000] if a - 0xC000 < len(got['image']) else 0)]
    if hit:                                 # the 6502 wrote into ROM (dropped); the code under test did not drop it
        d.append(f'wrote ROM addresses the Apple ignores: ' + ' '.join(f'${a:04X}' for a in hit[:4]))
    rr, gr = ref['ram'], got['ram']
    ign = set(ignore)
    bad = [i for i in range(0xC000) if not 0x100 <= i < 0x200 and rr[i] != gr[i] and i not in ign]
    if bad:
        d.append(f'RAM {len(bad)} bytes differ, first ' + ', '.join(f'${i:04X} ref={rr[i]:02X} got={gr[i]:02X}'
                                                                 for i in bad[:4]))
    return d
