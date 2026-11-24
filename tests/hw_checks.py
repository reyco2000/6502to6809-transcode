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


def sim_run(lines, out_len=0x20):
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
    r = subprocess.run([SIM, '-l', f'7C000:{b}', '-pc', f'{sym["tstart"]:04X}', '-stop', f'{sym["thalt"]:04X}',
                        '-max', '2000000', '-d', f'7FC10:{out_len:X}:{o}'], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'cc3sim: {r.stdout} {r.stderr}')
    return open(o, 'rb').read(), int(re.search(r'cycles=(\d+)', r.stdout).group(1))


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
    r = difftest_instance(ti, tone_code(), n=60, setup=audible)
    ok &= check(f'tone: regs/flags/RAM ({r})', r.passed == r.cases > 0)
    ok &= check(f'tone: total time ({r.max_timing_err:.4f})', r.timed and r.max_timing_err <= 0.02)

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
