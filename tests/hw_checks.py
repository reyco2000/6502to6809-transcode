"""Hardware transcoder checks (no game data needed).

    python3 -m tests.hw_checks [GROUP ...]       one or more groups; all when none is given
Run from tests.selftest as part of the kit self-test.
"""
import sys

GROUPS = {}


def group(name):
    def reg(fn):
        GROUPS[name] = fn
        return fn
    return reg


@group('map')
def g_map(check):
    from kit.hw import parse_io_symbol, Runtime, Latch, Ignore, IdiomOnly
    from kit.hw import apple2 as A
    ok = True
    ok &= check('hw: parse io symbol', parse_io_symbol('io_rdx_C061') == ('rdx', 0xC061))
    try:
        parse_io_symbol('vm_rect')
        bad = False
    except ValueError:
        bad = True
    ok &= check('hw: parse io symbol rejects others', bad)
    ok &= check('hw: kbd latch', A.lookup(0xC000, 'rd').action == Latch('kb_kbd'))
    ok &= check('hw: speaker runtime', A.lookup(0xC030, 'wr').action == Runtime())
    ok &= check('hw: slot rdx is slot I/O', 'slot' in A.lookup(0xC080, 'rdx').action.reason)
    ok &= check('hw: LC rd', 'language card' in A.lookup(0xC080, 'rd').action.reason)
    ok &= check('hw: override', A.lookup(0xC05E, 'wr', {0xC05E: Ignore()}).action == Ignore())
    ok &= check('hw: every C0xx rd/wr mapped',
                all(A.lookup(a, m) for a in range(0xC000, 0xC100) for m in ('rd', 'wr')))
    pairs = [(a, m) for e in A.MAP for a in range(e.lo, e.hi + 1) for m in e.modes]
    ok &= check('hw: no overlapping entries', len(pairs) == len(set(pairs)))
    ok &= check('hw: ROM WAIT idiom', A.rom(0xFCA8).action == IdiomOnly(('rom_wait',)))
    ok &= check('hw: ROM_KNOWN covered', all(A.rom(a) for a in (
        0xFCA8, 0xFC58, 0xFDED, 0xFDF0, 0xFD0C, 0xFD1B, 0xFB1E, 0xFBDD, 0xFF3A, 0xFE89, 0xFE93, 0xFB2F,
        0xF3E2, 0xF3D8, 0xC600, 0xC65C)))
    return ok


@group('survey')
def g_survey(check):
    from kit import survey
    ok = True
    ok &= check('survey: speaker via map', survey.softsw(0xC030)[2] == 'runtime')
    ok &= check('survey: Disk II slot', survey.softsw(0xC08C, 'absx')[2].startswith('TRAP'))
    ok &= check('survey: paddle idiom', survey.softsw(0xC064)[2].startswith('idiom'))
    ok &= check('survey: write side used for stores', survey.softsw(0xC000, 'abs', 'STA')[2].startswith('TRAP')
                and survey.softsw(0xC000, 'abs', 'LDA')[2] == 'runtime')
    ok &= check('survey: no SOFTSW table left', not hasattr(survey, 'SOFTSW') and not hasattr(survey, 'ROM_KNOWN'))
    return ok


@group('iocheck')
def g_iocheck(check):
    from kit.hw import iocheck, Ignore
    ok = True
    e, c = iocheck.check({'io_wr_C05E'})
    ok &= check('iocheck: trap is an error', len(e) == 1 and 'double hi-res' in e[0] and 'HW_OVERRIDES' in e[0])
    e, c = iocheck.check({'io_wr_C05E'}, overrides={0xC05E: Ignore()})
    ok &= check('iocheck: override clears it', e == [])
    e, c = iocheck.check({'io_rd_C064'})
    ok &= check('iocheck: idiom-only single access', len(e) == 1 and 'paddle_count' in e[0])
    e, c = iocheck.check({'io_wr_C050', 'io_rd_C030'})
    ok &= check('iocheck: cond collected', e == [] and c == {'HW_IOV'})
    e, c = iocheck.check({'io_rdx_C030'})
    ok &= check('iocheck: unmapped is an error', len(e) == 1 and 'not in the hardware map' in e[0])
    ok &= check('iocheck: ignore stub',
                iocheck.stubs({'io_rd_C020'}, set()) == ['io_rd_C020\tpshs\tcc', '\tclr\tIOV', '\tpuls\tcc,pc'])
    ok &= check('iocheck: ignore write stub', iocheck.stubs({'io_wr_C058'}, set()) == ['io_wr_C058\trts'])
    ok &= check('iocheck: defined symbols get no stub', iocheck.stubs({'io_rd_C030'}, {'io_rd_C030'}) == [])
    try:
        iocheck.stubs({'io_wr_C05F'}, set())
        raised = False
    except SystemExit as x:
        raised = 'double hi-res' in str(x)
    ok &= check('iocheck: stubs refuse a trap', raised)
    e, c = iocheck.check({'io_rd_C000'}, io_mem={0xC000: 'my_latch'})
    ok &= check('iocheck: spec IO_MEM wins', e == [])
    from kit import gen
    ok &= check('iocheck: gen default IO_MEM from the map',
                gen.IO_MEM_DEFAULT[0xC000] == 'kb_kbd' and gen.IO_MEM_DEFAULT[0xC061] == 'btn0'
                and gen.IO_MEM_DEFAULT[0xC062] == 'btn1')
    return ok


def sim_run(lines, out_len=0x20, watch=()):
    """assemble `lines` (raw, org $C000 is added; must define tstart and thalt) and run them in cc3sim;
    -> the bytes at $FC10.. (out_len) and the cycle count"""
    import os
    import re
    import subprocess
    import tempfile
    from kit.asm import assemble
    from kit.paths import SIM
    wd = tempfile.mkdtemp(prefix='hwck_')
    b, s, o = (os.path.join(wd, n) for n in ('t.bin', 't.sym', 'o.bin'))
    assemble(['\tpragma\tnoautobranchlength', '\tsetdp\t0', '\torg\t$C000'] + list(lines), b, symbols=s)
    sym = {m.group(1): int(m.group(2), 16) for m in (re.match(r'(\S+) EQU \$([0-9A-F]+)', ln) for ln in open(s)) if m}
    w = [x for a in watch for x in ('-w', f'{a:04X}')]
    r = subprocess.run([SIM, '-l', f'7C000:{b}', '-pc', f'{sym["tstart"]:04X}', '-stop', f'{sym["thalt"]:04X}',
                        '-max', '20000000', '-d', f'7FC10:{out_len:X}:{o}'] + w, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'cc3sim: {r.stdout} {r.stderr}')
    cyc = int(re.search(r'stop pc=\S+ cycles=(\d+)', r.stdout).group(1))
    if watch:
        writes = [(int(m.group(1), 16), int(m.group(2), 16), int(m.group(3)))
                  for m in re.finditer(r'^w ([0-9A-F]{4}) v=([0-9A-F]{2}) cycles=(\d+)$', r.stdout, re.M)]
        return open(o, 'rb').read(), cyc, writes
    return open(o, 'rb').read(), cyc


IOV_EQU = ['IOV\tequ\t$FB00', 'v_dpage\tequ\t$FB01', 'vbl_over\tequ\t$FB02']


def iov_seq(calls, v_dpage=0, vbl_over=0, ff92=0):
    """run io_video.asm handlers in order; -> (IOV, v_mode) after the last one"""
    from kit.paths import RT
    import os
    src = open(os.path.join(RT, 'hw', 'apple2', 'io_video.asm')).read().splitlines()
    drv = ['tstart\tlds\t#$FBE0', '\tclr\tIOV', f'\tlda\t#${v_dpage:02X}', '\tsta\tv_dpage',
           f'\tlda\t#${vbl_over:02X}', '\tsta\tvbl_over', f'\tlda\t#${ff92:02X}', '\tsta\t$FF92']
    drv += [f'\tjsr\t{c}' for c in calls]
    drv += ['\tlda\tIOV', '\tsta\t$FC10', '\tlda\tv_mode', '\tsta\t$FC11', 'thalt\tbra\tthalt']
    out, _ = sim_run(IOV_EQU + drv + src)
    return out[0], out[1]


def iov_preserves(handler):
    from kit.paths import RT
    import os
    src = open(os.path.join(RT, 'hw', 'apple2', 'io_video.asm')).read().splitlines()
    drv = ['tstart\tlds\t#$FBE0', '\tlda\t#$5A', '\tldb\t#$A5', '\tandcc\t#$F0', '\torcc\t#$05', f'\tjsr\t{handler}',
           '\tpshs\tcc', '\tsta\t$FC10', '\tstb\t$FC11', '\tpuls\ta', '\tsta\t$FC12', 'thalt\tbra\tthalt']
    out, _ = sim_run(IOV_EQU + drv + src)
    return out[0] == 0x5A and out[1] == 0xA5 and (out[2] & 0x0F) == 0x05


IOV_HANDLERS = ['io_rd_C050', 'io_wr_C050', 'io_rd_C051', 'io_wr_C051', 'io_rd_C052', 'io_wr_C052', 'io_rd_C053',
                'io_wr_C053', 'io_rd_C056', 'io_wr_C056', 'io_rd_C057', 'io_wr_C057', 'io_rd_C019', 'io_rd_C01A',
                'io_rd_C01B', 'io_rd_C01C', 'io_rd_C01D']


@group('iov')
def g_iov(check):
    ok = True
    ok &= check('iov: TXTSET then RDTEXT', iov_seq(['io_wr_C051', 'io_rd_C01A'])[0] == 0x80)
    ok &= check('iov: TXTCLR then RDTEXT', iov_seq(['io_wr_C051', 'io_wr_C050', 'io_rd_C01A'])[0] == 0x00)
    ok &= check('iov: MIXSET then RDMIXED', iov_seq(['io_rd_C053', 'io_rd_C01B'])[0] == 0x80)
    ok &= check('iov: LORES then RDHIRES', iov_seq(['io_wr_C056', 'io_rd_C01D'])[0] == 0x00)
    ok &= check('iov: HIRES then RDHIRES', iov_seq(['io_wr_C056', 'io_wr_C057', 'io_rd_C01D'])[0] == 0x80)
    ok &= check('iov: v_mode bits', iov_seq(['io_wr_C051', 'io_wr_C053', 'io_wr_C056'])[1] == 0x07)
    ok &= check('iov: RDPAGE2 follows v_dpage', iov_seq(['io_rd_C01C'], v_dpage=1)[0] == 0x80
                and iov_seq(['io_rd_C01C'], v_dpage=0)[0] == 0x00)
    ok &= check('iov: RDVBL inside blank', iov_seq(['io_rd_C019'], ff92=0x00, vbl_over=0)[0] & 0x80 == 0)
    ok &= check('iov: RDVBL after the window', iov_seq(['io_rd_C019'], ff92=0x20, vbl_over=0)[0] & 0x80 == 0x80
                and iov_seq(['io_rd_C019'], ff92=0x00, vbl_over=1)[0] & 0x80 == 0x80)
    ok &= check('iov: handlers preserve A, B, CC', all(iov_preserves(h) for h in IOV_HANDLERS))
    return ok


def _doc_block(path):
    t = open(path).read()
    a, b = t.find('<!-- hw-map:begin -->'), t.find('<!-- hw-map:end -->')
    return t[a:b] if 0 <= a < b else ''


@group('docs')
def g_docs(check):
    import os
    import re
    from kit.hw import Runtime
    from kit.hw import apple2 as A
    from kit.paths import REPO, RT
    ok = True
    doc = A.render_doc()
    ok &= check('docs: NATIVES.md table current', doc in _doc_block(os.path.join(REPO, 'docs', 'NATIVES.md')))
    ok &= check('docs: apple2.md table current',
                doc in _doc_block(os.path.join(REPO, 'docs', 'machines', 'apple2.md')))
    labels = {}
    for f in ('t512/rt.asm', 't128/rt.asm', 'hw/apple2/io_video.asm'):
        labels[f] = set(re.findall(r'^(io_\w+)', open(os.path.join(RT, f)).read(), re.M))
    missing = []
    for e in A.MAP:
        if not isinstance(e.action, Runtime):
            continue
        for a in range(e.lo, e.hi + 1):
            for m in e.modes:
                s = f'io_{m}_{a:04X}'
                files = [A.COND_SRC[e.action.cond]] if e.action.cond else ['t512/rt.asm', 't128/rt.asm']
                missing += [f'{s} in {f}' for f in files if s not in labels[f]]
    ok &= check('hw: Runtime labels exist' + (f' (missing: {", ".join(missing)})' if missing else ''), not missing)
    return ok


# monitor WAIT ($FCA8), written out from its documented source: SEC / W2: PHA / W3: SBC #1 / BNE W3 / PLA /
# SBC #1 / BNE W2 / RTS  (code, not ROM bytes)
WAIT_6502 = bytes([0x38, 0x48, 0xE9, 0x01, 0xD0, 0xFC, 0x68, 0xE9, 0x01, 0xD0, 0xF6, 0x60])


def raises(exc, fn, *a):
    try:
        fn(*a)
    except exc:
        return True
    return False


@group('idiom')
def g_idiom(check):
    import types
    from kit.asm import assemble, AsmError
    from kit.hw import Idiom
    from kit.hw.idiom import expand, symbol
    from kit.hw.lib import render_all
    from kit.hw.verify import difftest_instance
    import tempfile
    import os
    ok = True
    S = types.SimpleNamespace(NATIVE={}, NATIVE_RANGES=[], NATIVE_EXITS=[],
                              HW_IDIOMS=[Idiom('vbl_poll', 0x0900, 0x0905, (0x0906,))])
    expand(S)
    expand(S)
    ok &= check('idiom: expand', S.NATIVE == {0x0900: 'hw_vbl_poll_0900'} and S.NATIVE_RANGES == [(0x0900, 0x0905)]
                and S.NATIVE_EXITS == [0x0906] and symbol(S.HW_IDIOMS[0]) == 'hw_vbl_poll_0900')
    S2 = types.SimpleNamespace(NATIVE={0x0902: 'x'}, NATIVE_RANGES=[(0x0902, 0x0910)], NATIVE_EXITS=[],
                               HW_IDIOMS=[Idiom('vbl_poll', 0x0900, 0x0905)])
    ok &= check('idiom: overlap with a native refused', raises(SystemExit, expand, S2))
    S3 = types.SimpleNamespace(NATIVE={}, NATIVE_RANGES=[], NATIVE_EXITS=[],
                               HW_IDIOMS=[Idiom('vbl_poll', 0x0900, 0x0905), Idiom('delay', 0x0904, 0x0910)])
    ok &= check('idiom: overlapping idioms refused', raises(SystemExit, expand, S3))
    S4 = types.SimpleNamespace(NATIVE={}, NATIVE_RANGES=[], NATIVE_EXITS=[], HW_IDIOMS=[Idiom('nonsense', 0x900, 0x905)])
    ok &= check('idiom: unknown kind refused', raises(SystemExit, expand, S4))
    S5 = types.SimpleNamespace(NATIVE={}, NATIVE_RANGES=[], NATIVE_EXITS=[], HW_IDIOMS=[Idiom('rom_wait', 0xFCA8, 0xFCA8)])
    expand(S5)
    from kit.xc65 import A_, FLAGS
    c = S5.NATIVE_CONTRACTS.get('hw_rom_wait_fca8')
    ok &= check('idiom: ROM entry gets a contract', c is not None and c.use == A_ and c.defs == A_ | FLAGS)
    two = render_all([Idiom('rom_wait', 0xFCA8, 0xFCA8), Idiom('rom_wait', 0x0900, 0x090B)])
    d = tempfile.mkdtemp(prefix='hwck_')
    try:
        assemble(['\tsetdp\t0', '\torg\t$C000', 'RY\tequ\t$F0', 'rt_vbl\trts'] + two, os.path.join(d, 'a.bin'))
        asm_ok = True
    except AsmError as x:
        print('   ', x)
        asm_ok = False
    ok &= check('idiom: two instances assemble', asm_ok)
    r = difftest_instance(Idiom('rom_wait', 0x0800, 0x080B), WAIT_6502, n=100)
    ok &= check(f'idiom: rom_wait vs 6502 WAIT ({r})', r.passed == r.cases > 0)
    ok &= check(f'idiom: rom_wait timing ({r.max_timing_err:.3f})', r.max_timing_err <= 0.02)
    vb = bytes([0xAD, 0x19, 0xC0, 0x30, 0xFB, 0x60])          # L: LDA $C019 / BMI L / RTS
    r = difftest_instance(Idiom('vbl_poll', 0x0800, 0x0804, (0x0805,), params={'exit_n': 0}), vb, n=50)
    ok &= check(f'idiom: vbl_poll vs 6502 loop ({r})', r.passed == r.cases > 0)
    vb = bytes([0x2C, 0x19, 0xC0, 0x30, 0xFB, 0x60])          # L: BIT $C019 / BMI L / RTS (A kept)
    r = difftest_instance(Idiom('vbl_poll', 0x0800, 0x0804, (0x0805,), params={'exit_n': 0, 'keep_a': 1}), vb, n=50,
                          live=0x3F & ~0x10)                   # Z after BIT depends on A AND the floating bus
    ok &= check(f'idiom: vbl_poll BIT keeps A ({r})', r.passed == r.cases > 0)
    return ok


# LDX #0 / L: LDY #0 / M: DEY / BNE M / DEX / BNE L / RTS  (256 x 256 passes)
DELAY_00 = bytes([0xA2, 0x00, 0xA0, 0x00, 0x88, 0xD0, 0xFD, 0xCA, 0xD0, 0xF8, 0x60])
# L: LDY #3 / M: DEY / BNE M / DEX / BNE L / RTS  (outer count = X on entry)
DELAY_X3 = bytes([0xA0, 0x03, 0x88, 0xD0, 0xFD, 0xCA, 0xD0, 0xF8, 0x60])
# L: LDY $10 / M: DEY / BNE M / DEX / BNE L / RTS  (inner count from zero page)
DELAY_XZ = bytes([0xA4, 0x10, 0x88, 0xD0, 0xFD, 0xCA, 0xD0, 0xF8, 0x60])


@group('lib_delay')
def g_lib_delay(check):
    from kit.hw import Idiom
    from kit.hw.verify import difftest_instance
    ok = True
    xy0 = {'x': 0, 'y': 0}
    i = Idiom('delay', 0x0800, 0x080A, params=dict(outer=('imm', 0), inner=('imm', 0), inner_pass=5, outer_pass=6,
                                                     const=1, exit_regs=xy0))
    r = difftest_instance(i, DELAY_00, n=3)
    ok &= check(f'delay: 256 x 256 passes, regs/flags/RAM ({r})', r.passed == r.cases > 0)
    ok &= check(f'delay: 256 x 256 passes, timing ({r.max_timing_err:.4f})', r.timed and r.max_timing_err <= 0.02)
    i = Idiom('delay', 0x0800, 0x0808, params=dict(outer=('x',), inner=('imm', 3), inner_pass=5, outer_pass=6,
                                                     const=-1, exit_regs=xy0))
    r = difftest_instance(i, DELAY_X3, n=150)
    ok &= check(f'delay: outer count from X (0 = 256) ({r})', r.passed == r.cases > 0)
    ok &= check(f'delay: outer from X, timing ({r.max_timing_err:.4f})', r.timed and r.max_timing_err <= 0.02)
    edge = lambda rnd, ram, a, x, y, p: (a, rnd.choice([0, 1, 2, 255]), y, p)
    r = difftest_instance(i, DELAY_X3, n=12, setup=edge, timed=False)
    ok &= check(f'delay: X = 0, 1, 2, 255 exact ({r})', r.passed == r.cases > 0)
    i = Idiom('delay', 0x0800, 0x0808, params=dict(outer=('x',), inner=('zp', 0x10), inner_pass=5, outer_pass=7,
                                                     const=-1, exit_regs=xy0))
    r = difftest_instance(i, DELAY_XZ, n=100)
    ok &= check(f'delay: inner count from zero page ({r})', r.passed == r.cases > 0)
    ok &= check(f'delay: inner from zero page, timing ({r.max_timing_err:.4f})', r.timed and r.max_timing_err <= 0.02)
    return ok


# L: LDA $C030 / LDY $06 / M: DEY / BNE M / DEX / BNE L / RTS   (period in $06, duration in X)
def tone_code(op=0xAD):
    return bytes([op, 0x30, 0xC0, 0xA4, 0x06, 0x88, 0xD0, 0xFD, 0xCA, 0xD0, 0xF5, 0x60])


TONE_PARAMS = dict(period=('zp', 0x06), duration=('x',), inner_pass=5, outer_pass=11, const=-1,
                   exit_regs={'a': 0, 'x': 0, 'y': 0})
# LDA $C070 / LDY #0 / L: LDA $C064 / BPL D / INY / BNE L / DEY / D: RTS   (PREAD-like count loop)
PADDLE_CODE = bytes([0xAD, 0x70, 0xC0, 0xA0, 0x00, 0xAD, 0x64, 0xC0, 0x10, 0x04, 0xC8, 0xD0, 0xF8, 0x88, 0x60])
PADDLE_PARAMS = dict(paddle=0, counter='y', pass_cycles=11, max=255, exit_regs={'a': 0}, exit_flags={'n': 0, 'z': 1})


def run_paddle(idiom, joy, joy_y=0):
    """the instance's count for a stick position (cc3sim)"""
    from kit.hw.lib import render_all
    from kit.hw.idiom import symbol
    lines = render_all([idiom])
    drv = ['RYW\tequ\t$FBF0', 'RY\tequ\t$FBF1', 'tstart\tlds\t#$FBE0', '\tclra', '\ttfr\ta,dp', '\tclr\tRYW',
           f'\tldb\t#${idiom.params.get("paddle", 0):02X}', f'\tjsr\t{symbol(idiom)}', '\tstb\t$FC10', '\tlda\tRY',
           '\tsta\t$FC11', 'thalt\tbra\tthalt', f'joy_x\tfcb\t{joy}', f'joy_y\tfcb\t{joy_y}', 'rt_vbl\trts']
    out, _ = sim_run(drv + lines)
    return out[1] if idiom.params.get('counter', 'y') == 'y' else out[0]


@group('lib_io')
def g_lib_io(check):
    from kit.hw import Idiom
    from kit.hw.lib import paddle_model, render_all
    from kit.hw.verify import difftest_instance
    ok = True
    ti = Idiom('tone', 0x0800, 0x080B, params=TONE_PARAMS)
    per = lambda rnd, ram, a, x, y, p: (a, rnd.choice([0, 1, 2, 30, 80, 200, 255]), y, p)
    def audible(rnd, ram, a, x, y, p):              # passes of at least ~45 Apple cycles (below ~12 kHz)
        ram[0x06] = rnd.choice([0] + list(range(8, 256)))
        return a, x, y, p
    r = difftest_instance(ti, tone_code(), n=60, setup=audible, timed=False)   # pitch: group verify
    ok &= check(f'tone: regs/flags/RAM ({r})', r.passed == r.cases > 0)
    # tone timing is checked on the click intervals (group verify), not the total time: the routine's setup
    # (~100 Apple cycles before the first click) is not taken off the first period

    def p0(rnd, ram, a, x, y, p):
        ram[0x06] = rnd.choice([0, 1, 2])
        return a, rnd.choice([0, 1, 2]), y, p
    r = difftest_instance(ti, tone_code(), n=20, setup=p0, timed=False)
    ok &= check(f'tone: period/duration 0 = 256, 1, 2 exact ({r})', r.passed == r.cases > 0)
    ok &= check('paddle_model: extremes', paddle_model(0, 12, 255) == 0 and paddle_model(63, 11, 255) == 255
                and paddle_model(63, 5, 200) == 200)
    pi = Idiom('paddle_count', 0x0800, 0x080E, params=PADDLE_PARAMS)
    ok &= check('paddle_count: count = model',
                all(run_paddle(pi, j) == paddle_model(j, 11, 255) for j in (0, 1, 31, 32, 63)))
    pj = Idiom('paddle_count', 0x0800, 0x080E, params=dict(PADDLE_PARAMS, paddle=1))
    ok &= check('paddle_count: paddle 1 reads joy_y', run_paddle(pj, 0, joy_y=63) == paddle_model(63, 11, 255))
    zero = lambda lines: ['joy_x\tfcb\t0', 'joy_y\tfcb\t0']
    r = difftest_instance(pi, PADDLE_CODE, n=30, extra_lines=zero(0), timed=False)
    ok &= check(f'paddle_count: stick at 0 = the 6502 with timers expired ({r})', r.passed == r.cases > 0)
    rp = Idiom('rom_pread', 0xFB1E, 0xFB1E)
    ok &= check('rom_pread: Y from joy', all(run_paddle(rp, j) == min(255, j * 255 // 63) for j in (0, 32, 63)))
    try:
        from kit.asm import assemble
        import tempfile
        import os
        assemble(['\tsetdp\t0', '\torg\t$C000', 'RY\tequ\t$F0'] + render_all([Idiom('rom_bell', 0xFF3A, 0xFF3A)]),
                 os.path.join(tempfile.mkdtemp(), 'b.bin'))
        bell = True
    except Exception as x:
        print('   ', x)
        bell = False
    ok &= check('rom_bell: assembles', bell)
    return ok


def find_in(code, org=0x0800):
    """run the idiom matchers over synthetic 6502 code at org -> (matches, near-misses)"""
    from kit.discover import discover
    from kit.hw.match import find
    mem = bytearray(0x10000)
    mem[org:org + len(code)] = code
    c = discover(mem, [org], {}, code_ranges=((org, org + 0xFF),))
    return find(mem, c)


def kinds(res):
    return [m.idiom.kind for m in res[0]]


def near(res, text):
    return any(text in n.reason for n in res[1])


@group('match')
def g_match(check):
    ok = True
    r = find_in(bytes([0xAD, 0x19, 0xC0, 0x10, 0xFB, 0x60]))
    ok &= check('match: vbl poll', kinds(r) == ['vbl_poll'] and r[0][0].idiom.exits == (0x0805,)
                and r[0][0].idiom.params == {'exit_n': 1, 'keep_a': 0})
    r = find_in(bytes([0x2C, 0x19, 0xC0, 0x30, 0xFB, 0x60]))
    ok &= check('match: vbl poll BIT/BMI', kinds(r) == ['vbl_poll'] and r[0][0].idiom.params == {'exit_n': 0, 'keep_a': 1})
    r = find_in(bytes([0xAD, 0x19, 0xC0, 0x30, 0xFB, 0xAD, 0x19, 0xC0, 0x10, 0xFB, 0x60]))
    ok &= check('match: paired vbl loops are one idiom', kinds(r) == ['vbl_poll'] and r[0][0].idiom.end == 0x0809
                and r[0][0].idiom.exits == (0x080A,) and r[0][0].idiom.params['exit_n'] == 1)
    r = find_in(DELAY_00)
    ok &= check('match: nested delay', kinds(r) == ['delay'] and r[0][0].idiom.params['exit_regs'] == {'x': 0, 'y': 0}
                and (r[0][0].idiom.entry, r[0][0].idiom.end) == (0x0800, 0x0809))
    p = r[0][0].idiom.params if r[0] else {}
    ok &= check('match: nested delay cost formula', (p.get('outer'), p.get('inner'), p.get('inner_pass'),
                p.get('outer_pass'), p.get('const')) == (('imm', 0), ('imm', 0), 5, 6, 1))
    r = find_in(DELAY_XZ)
    p = r[0][0].idiom.params if r[0] else {}
    ok &= check('match: delay with counts from X and zero page', kinds(r) == ['delay'] and p.get('outer') == ('x',)
                and p.get('inner') == ('zp', 0x10) and p.get('outer_pass') == 7 and p.get('const') == -1)
    r = find_in(bytes([0xA2, 0x00, 0x9D, 0x00, 0x30, 0xCA, 0xD0, 0xFA, 0x60]))
    ok &= check('match: delay writing RAM is a near-miss', kinds(r) == [] and near(r, 'writes memory'))
    r = find_in(bytes([0xA9, 0x40, 0x20, 0xA8, 0xFC, 0x60]))
    ok &= check('match: JSR WAIT', kinds(r) == ['rom_wait'] and r[0][0].idiom.entry == 0xFCA8)
    r = find_in(bytes([0x20, 0xA8, 0xFC, 0x20, 0xA8, 0xFC, 0x60]))
    ok &= check('match: one proposal per ROM entry', kinds(r) == ['rom_wait'])
    ok &= check('match: short loop is not a delay', kinds(find_in(bytes([0xA2, 0x03, 0xCA, 0xD0, 0xFD, 0x60]))) == [])
    from kit.hw.verify import difftest_instance
    r = find_in(DELAY_XZ)
    ev = difftest_instance(r[0][0].idiom, DELAY_XZ, n=40) if r[0] else None
    ok &= check(f'match: proposed delay passes its difftest ({ev})', ev is not None and ev.passed == ev.cases > 0)
    return ok


# L: LDA $C030 / LDY $06 / M: DEY / BNE M / LDA $C030 / LDY $07 / N: DEY / BNE N / DEX / BNE L / RTS
TWO_VOICE = bytes([0xAD, 0x30, 0xC0, 0xA4, 0x06, 0x88, 0xD0, 0xFD, 0xAD, 0x30, 0xC0, 0xA4, 0x07, 0x88, 0xD0, 0xFD,
                   0xCA, 0xD0, 0xED, 0x60])
# L: LDA $C030 / LDY $06 / M: DEY / BNE M / LDA $C000 / DEX / BNE L / RTS   (keyboard read inside the note)
TONE_KEY = bytes([0xAD, 0x30, 0xC0, 0xA4, 0x06, 0x88, 0xD0, 0xFD, 0xAD, 0x00, 0xC0, 0xCA, 0xD0, 0xF2, 0x60])
# as PADDLE_CODE, with LDA $C064,X (paddle number from X on entry)
PADDLE_X_CODE = bytes([0xAD, 0x70, 0xC0, 0xA0, 0x00, 0xBD, 0x64, 0xC0, 0x10, 0x04, 0xC8, 0xD0, 0xF8, 0x88, 0x60])
# LDX #1 / LDA $C070 / LDY #0 / L: LDA $C064,X / BPL D / INY / CPY #$C0 / BNE L / D: RTS
PADDLE_CPY = bytes([0xA2, 0x01, 0xAD, 0x70, 0xC0, 0xA0, 0x00, 0xBD, 0x64, 0xC0, 0x10, 0x05, 0xC8, 0xC0, 0xC0, 0xD0,
                    0xF6, 0x60])


@group('match_io')
def g_match_io(check):
    ok = True
    for op in (0xAD, 0x2C, 0x8D):                 # LDA / BIT / STA $C030
        r = find_in(tone_code(op))
        ok &= check(f'match: tone via {op:02X}', kinds(r) == ['tone'])
    r = find_in(tone_code())
    p = r[0][0].idiom.params if r[0] else {}
    ok &= check('match: tone params', (p.get('period'), p.get('duration'), p.get('inner_pass'), p.get('outer_pass'))
                == (('zp', 6), ('x',), 5, 11) and p.get('exit_regs') == {'a': 0, 'x': 0, 'y': 0})
    r8 = find_in(tone_code(0x8D))
    ok &= check('match: tone via STA keeps A', r8[0] and 'a' not in r8[0][0].idiom.params['exit_regs'])
    from kit.hw.verify import difftest_instance
    ev = difftest_instance(r[0][0].idiom, tone_code(), n=20, timed=False) if r[0] else None
    ok &= check(f'match: proposed tone passes its difftest ({ev})', ev is not None and ev.passed == ev.cases > 0)
    ok &= check('match: two-voice near-miss', kinds(find_in(TWO_VOICE)) == [] and near(find_in(TWO_VOICE), 'multi-voice'))
    ok &= check('match: keyboard in the note is a near-miss', kinds(find_in(TONE_KEY)) == []
                and near(find_in(TONE_KEY), 'keyboard'))
    r = find_in(PADDLE_CODE)
    p = r[0][0].idiom.params if r[0] else {}
    ok &= check('match: paddle loop', kinds(r) == ['paddle_count'] and p.get('paddle') == 0 and p.get('counter') == 'y'
                and p.get('pass_cycles') == 11 and p.get('max') == 255 and r[0][0].idiom.exits == (0x080E,)
                and p.get('exit_flags_max') == {'n': 1, 'z': 0})
    ev = difftest_instance(r[0][0].idiom, PADDLE_CODE, n=20, extra_lines=['joy_x\tfcb\t0', 'joy_y\tfcb\t0'],
                           timed=False) if r[0] else None
    ok &= check(f'match: proposed paddle passes its difftest at stick 0 ({ev})', ev is not None and ev.passed == ev.cases > 0)
    ok &= check('match: paddle,X unknown is a near-miss', kinds(find_in(PADDLE_X_CODE)) == []
                and near(find_in(PADDLE_X_CODE), 'not constant'))
    r = find_in(PADDLE_CPY)
    p = r[0][0].idiom.params if r[0] else {}
    ok &= check('match: paddle,X after LDX #1 with CPY max', kinds(r) == ['paddle_count'] and p.get('paddle') == 1
                and p.get('max') == 0xC0 and p.get('pass_cycles') == 13 and r[0][0].idiom.entry == 0x0800)
    return ok


def synth_cov(code, org=0x0800, entries=True):
    """a minimal coverage profile around one image of code (every instruction executed once)"""
    from kit.discover import discover
    mem = bytearray(0x10000)
    mem[org:org + len(code)] = code
    c = discover(mem, [org], {}, code_ranges=((org, org + 0xFF),))
    d = {'id': 0, 'page': f'{org >> 8:02X}', 'bytes': code.hex().upper(),
         'instr': {f'{a:04X}': 1 for a in c.insns}, 'ea': {}, 'smc': {},
         'entries': [[f'{org:04X}', 'jsr', '0000', 0, 1]] if entries else []}
    return {'images': [d], 'rom_entries': [], 'loaders': [], 'summary': {}}


@group('survey_idioms')
def g_survey_idioms(check):
    import io
    from kit import survey
    ok = True
    cov = synth_cov(tone_code())
    s = survey.survey(cov, [survey.Image(d) for d in cov['images']])
    d = survey.draft_spec(cov, s)
    ok &= check('survey: draft lists idiom commented out', "#   Idiom('tone'" in d and 'HW_IDIOMS = [' in d)
    ok &= check('survey: idiom routine not in draft NATIVE', 'n_sound_0800' not in d)
    out = io.StringIO()
    survey.report(cov, s, out)
    ok &= check('survey: section 7 lists the proposal', '## 7. Hardware idioms' in out.getvalue()
                and 'tone' in out.getvalue().split('## 7.')[1])
    cov = synth_cov(TWO_VOICE)
    s = survey.survey(cov, [survey.Image(d) for d in cov['images']])
    out = io.StringIO()
    survey.report(cov, s, out)
    ok &= check('survey: near-miss reported', 'multi-voice' in out.getvalue().split('## 7.')[1])
    ok &= check('survey: near-miss routine stays a native candidate', 'n_tone_0800' in survey.draft_spec(cov, s))
    return ok


@group('verify')
def g_verify(check):
    from kit.hw import Idiom
    from kit.hw.lib import render_all
    from kit.hw.verify import verify
    from kit.hw.match import Match
    ok = True
    _, _, w = sim_run(['tstart\tlda\t#1', '\tsta\t$FF20', '\tnop', '\tsta\t$FF20', 'thalt\tbra\tthalt'], watch=(0xFF20,))
    ok &= check(f'cc3sim -w logs writes ({w})', len(w) == 2 and w[0][:2] == (0xFF20, 1) and w[1][2] - w[0][2] == 7)

    def mem_with(code, org=0x0800):
        m = bytearray(0x10000)
        m[org:org + len(code)] = code
        return m
    tm = find_in(tone_code())[0][0]
    ev = verify(tm, mem_with(tone_code()), n=40)
    ok &= check(f'verify: tone passes with period timing ({ev})', ev.passed == ev.cases > 0 and ev.timed
                and ev.max_timing_err <= 0.02)
    from kit.hw import lib
    bad = [ln.replace('\tldx\thw_tone_0800_k', '\tldx\thw_tone_0800_k\n\tleax\t3,x')   # +24 cycles a pass
           for ln in render_all([tm.idiom])]
    assert any('leax\t3,x' in ln for ln in bad)
    bad = [x for ln in bad for x in ln.split('\n')]
    ev = verify(tm, mem_with(tone_code()), n=20, render_lines=bad)
    ok &= check(f'verify: wrong timing is caught ({ev})', ev.max_timing_err > 0.02)
    pm = find_in(PADDLE_CODE)[0][0]
    ev = verify(pm, mem_with(PADDLE_CODE), n=40)
    ok &= check(f'verify: paddle count = Apple timer model ({ev})', ev.passed == ev.cases > 0)
    vm = find_in(bytes([0xAD, 0x19, 0xC0, 0x30, 0xFB, 0x60]))[0][0]
    ev = verify(vm, mem_with(bytes([0xAD, 0x19, 0xC0, 0x30, 0xFB, 0x60])), n=20)
    ok &= check(f'verify: vbl_poll with a blank model ({ev})', ev.passed == ev.cases > 0)
    dm = find_in(DELAY_XZ)[0][0]
    ev = verify(dm, mem_with(DELAY_XZ), n=30)
    ok &= check(f'verify: delay ({ev})', ev.passed == ev.cases > 0 and ev.max_timing_err <= 0.02)
    bell = Idiom('rom_bell', 0xFF3A, 0xFF3A)
    lines = ['RY\tequ\t$FBF1', 'tstart\tlds\t#$FBE0', '\tjsr\thw_rom_bell_ff3a', 'thalt\tbra\tthalt'] + \
        render_all([bell])
    _, cyc, w = sim_run(lines, watch=(0xFF20,))
    per = [b[2] - a[2] for a, b in zip(w, w[1:])]
    want = lib._wait(12) + 2 + 4 + 2 + 3
    ok &= check(f'rom_bell: 192 DAC writes, {sum(per) / len(per) / 1.75:.0f} Apple cycles apart (want {want})',
                len(w) == 192 and abs(sum(per) / len(per) / (want * 1.75) - 1) <= 0.02)
    import json
    import io as _io
    import tempfile
    import os
    from contextlib import redirect_stdout
    from kit import survey
    d = tempfile.mkdtemp(prefix='hwck_')
    cp = os.path.join(d, 'cov.json')
    json.dump(synth_cov(tone_code()), open(cp, 'w'))
    out = _io.StringIO()
    with redirect_stdout(out):
        survey.main(['--coverage', cp, '--verify', '8', '--spec-out', os.path.join(d, 'draft.py')])
    ok &= check('survey --verify: evidence in section 7 and the draft', 'verified 8/8' in out.getvalue().split('## 7.')[1]
                and 'verified 8/8' in open(os.path.join(d, 'draft.py')).read())
    return ok


@group('guide')
def g_guide(check):
    import os
    from kit import survey
    from kit.hw.match import NearMiss
    from kit.paths import REPO
    ok = True
    # one image: a loader-ish routine, a TRAP site, an SED, a hot hi-res routine, a tone, a two-voice near-miss
    code = bytearray(0x200)
    code[0x000:0x00C] = tone_code()                                   # $0800 tone (bucket 6)
    code[0x020:0x024] = bytes([0x8D, 0x5E, 0xC0, 0x60])               # $0820 STA $C05E (AN3: bucket 3)
    code[0x040:0x043] = bytes([0xF8, 0xD8, 0x60])                     # $0840 SED / CLD (bucket 4)
    code[0x060:0x060 + len(TWO_VOICE)] = TWO_VOICE                    # $0860 two-voice (bucket 2)
    code[0x0A0:0x0A4] = bytes([0x8D, 0x00, 0x20, 0x60])               # $08A0 STA $2000 (hi-res: bucket 5)
    from kit.discover import discover
    mem = bytearray(0x10000)
    mem[0x800:0xA00] = code
    roots = [0x800, 0x820, 0x840, 0x860, 0x8A0]
    c = discover(mem, roots, {}, code_ranges=((0x800, 0x9FF),))
    d = {'id': 0, 'page': '08', 'bytes': bytes(code).hex().upper(), 'instr': {f'{a:04X}': 100 for a in c.insns},
         'ea': {'08A0': 8}, 'smc': {}, 'entries': [[f'{r:04X}', 'jsr', '0000', 0, 1] for r in roots]}
    cov = {'images': [d], 'rom_entries': [], 'loaders': ['0900'], 'summary': {}}
    s = survey.survey(cov, [survey.Image(x) for x in cov['images']])
    r = survey.rank(s, s['matches'], s['near'], cov)
    b = {a: k for k, a, _ in r}
    ok &= check(f'rank: buckets ordered ({r})', [k for k, _, _ in r] == sorted(k for k, _, _ in r))
    ok &= check('rank: loader is bucket 1', b.get(0x0900) == 1)
    ok &= check('rank: multi-voice is bucket 2', b.get(0x0860) == 2)
    ok &= check('rank: trapped switch is bucket 3', b.get(0x0820) == 3)
    ok &= check('rank: decimal mode is bucket 4', b.get(0x0840) == 4)
    ok &= check('rank: hot hi-res is bucket 5', b.get(0x08A0) == 5)
    ok &= check('rank: proposed idiom is bucket 6', b.get(0x0800) == 6)
    ok &= check('rank: one line per routine', len(b) == len(r))
    import io
    out = io.StringIO()
    survey.report(cov, s, out)
    ok &= check('survey: section 8 printed', '## 8. Least likely to transcode' in out.getvalue())
    for f in ('SKILL.md', os.path.join('docs', 'DECIDE.md')):
        ok &= check(f'guide: {f} has the ranking', 'Least likely to transcode' in open(os.path.join(REPO, f)).read())
    return ok


@group('review')
def g_review(check):
    """findings of the final review, each pinned by a test"""
    import types
    from kit.hw import Idiom
    from kit.hw.idiom import expand
    from kit.hw.verify import verify, difftest_instance, WAIT_6502
    from kit.hw.lib import render_all

    def mem_with(code, org=0x0800):
        m = bytearray(0x10000)
        m[org:org + len(code)] = code
        return m
    ok = True
    for op in (0x2C, 0x8D):                       # 1: BIT / STA $C030 tones keep A
        r = find_in(tone_code(op))
        ev = verify(r[0][0], mem_with(tone_code(op)), n=10) if r[0] else None
        ok &= check(f'review: tone via {op:02X} keeps A ({ev})', ev is not None and ev.passed == ev.cases > 0)
    small = lambda rnd, ram, a, x, y, p: (rnd.randrange(1, 16), x, y, p)      # 2: short WAITs keep their time
    ev = difftest_instance(Idiom('rom_wait', 0x0800, 0x080B), WAIT_6502, n=40, setup=small, floor=0)
    ok &= check(f'review: WAIT with A = 1..15 within tolerance ({ev})', ev.passed == ev.cases > 0
                and ev.timed == ev.cases)                       # timing_ok: 2%, or 16 CoCo cycles
    ev = verify(find_in(PADDLE_CPY)[0][0], mem_with(PADDLE_CPY), n=20)                # 3: LDX #n folded in
    ok &= check(f'review: paddle after LDX #1 leaves X = 1 ({ev})', ev.passed == ev.cases > 0)
    mid = bytes([0xAD, 0x70, 0xC0, 0x85, 0x20, 0xA0, 0x00, 0xAD, 0x64, 0xC0, 0x10, 0x04, 0xC8, 0xD0, 0xF8, 0x88, 0x60])
    r = find_in(mid)                                                                # 4: STA between trigger and reset
    ok &= check('review: work between trigger and counter reset is a near-miss', kinds(r) == [] and bool(r[1]))
    r = find_in(tone_code(), org=0x08FA)                                            # 5: page-crossing branch
    p = r[0][0].idiom.params if r[0] else {}
    ok &= check(f'review: page-crossing inner branch costs 6 ({p.get("inner_pass")})', p.get('inner_pass') == 6)
    ev = verify(r[0][0], mem_with(tone_code(), 0x08FA), n=20) if r[0] else None
    ok &= check(f'review: page-crossing tone verifies ({ev})', ev is not None and ev.passed == ev.cases > 0)
    from kit.hw import lib
    bad = [ln.replace('\tldx\thw_tone_0800_k', '\tldx\thw_tone_0800_k\n\tleax\t3,x')
           for ln in render_all([find_in(tone_code())[0][0].idiom])]
    bad = [x for ln in bad for x in ln.split('\n')]
    ev = verify(find_in(tone_code())[0][0], mem_with(tone_code()), n=10, render_lines=bad)
    ok &= check(f'review: a timing error fails the case ({ev})', ev.passed < ev.cases)
    S = types.SimpleNamespace(NATIVE={}, NATIVE_RANGES=[], NATIVE_EXITS=[],                # 6: overlay exits
                              HW_IDIOMS=[Idiom('delay', 0x900, 0x905, (0x906,), image='st2')])
    ok &= check('review: overlay exits are refused', raises(SystemExit, expand, S))
    S = types.SimpleNamespace(NATIVE={}, NATIVE_RANGES=[], NATIVE_EXITS=[], HW_IDIOMS=[Idiom('rom_bell', 0xFF3A, 0xFF3A)])
    expand(S)                                                                       # 7: BELL keeps Y, A = $87
    from kit.xc65 import Y_
    ok &= check('review: BELL ($FF3A) contract keeps Y', not S.NATIVE_CONTRACTS['hw_rom_bell_ff3a'].defs & Y_)
    lines = ['RYW\tequ\t$FBF0', 'RY\tequ\t$FBF1', 'tstart\tlds\t#$FBE0', '\tlda\t#$33', '\tsta\tRY',
             '\tjsr\thw_rom_bell_ff3a', '\tsta\t$FC10', '\tlda\tRY', '\tsta\t$FC11', 'thalt\tbra\tthalt'] + \
        render_all([Idiom('rom_bell', 0xFF3A, 0xFF3A)])
    out, _ = sim_run(lines)
    ok &= check(f'review: BELL returns A = $87, Y kept ({out[0]:02X} {out[1]:02X})', out[0] == 0x87 and out[1] == 0x33)
    return ok


def run(check, only=None):
    ok = True
    for name, fn in GROUPS.items():
        if only and name not in only:
            continue
        ok &= fn(check)
    return ok


def _check(name, ok):
    print(f'{"ok  " if ok else "FAIL"} {name}')
    return ok


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    unknown = [g for g in argv if g not in GROUPS]
    if unknown:
        raise SystemExit(f'unknown group(s): {" ".join(unknown)}; known: {" ".join(GROUPS)}')
    good = run(_check, set(argv) or None)
    print('ALL OK' if good else 'FAILURES')
    sys.exit(0 if good else 1)


if __name__ == '__main__':
    main()
