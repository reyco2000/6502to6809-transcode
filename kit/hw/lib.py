"""Hardware idiom library: render accepted idioms (kit/hw/idiom.py) from the templates in rt/hw/apple2/.

    lines = render(idiom)            one instance (label hw_<kind>_<entry>)
    lines = render_all(idioms)       every instance plus the shared helpers they need (hw_dly)

Templates are 6809 source with @KEY@ placeholders: @SYM@ (the instance label; internal labels are @SYM@_x),
@EXIT@ (rts, or jmp <exit label> when the idiom has exits), @LOAD_<P>@ (load param P into A) and @<P>@ (a numeric
param).  The prepare_<kind> functions turn an idiom's params into those substitutions.
Param sources: ('a',) ('x',) ('y',) ('zp', addr) ('abs', addr) ('imm', value).
"""
import os

from ..paths import RT
from .idiom import symbol

TDIR = os.path.join(RT, 'hw', 'apple2')
KINDS = {'vbl_poll', 'rom_wait', 'delay', 'tone', 'paddle_count', 'rom_pread', 'rom_bell'}
NEEDS_DLY = {'rom_wait', 'delay', 'tone', 'rom_bell'}
# contracts of idioms that replace a ROM entry (no 6502 body in the RAM image): kind -> (use, defs) names
ROM_CONTRACTS = {'rom_wait': ('A_', 'A_ | FLAGS'), 'rom_pread': ('X_', 'A_ | Y_ | FLAGS'),
                 'rom_bell': ('0', 'A_ | Y_ | FLAGS')}
HW_OVH = 56                 # hw_dly fixed cost in 8-cycle units (calibrated by tests/hw_checks.py)


def load(src, reg='a'):
    """6809 instruction(s) that load a param source into register A or B"""
    k = src[0]
    if k == 'a':
        return [] if reg == 'a' else ['\ttfr\ta,b']
    if k == 'x':
        return ['\ttfr\tb,a'] if reg == 'a' else []
    if k == 'y':
        return [f'\tld{reg}\tRY']
    if k == 'zp':
        return [f'\tld{reg}\t<${src[1]:02X}']
    if k == 'abs':
        return [f'\tld{reg}\t>${src[1]:04X}']
    if k == 'imm':
        return [f'\tld{reg}\t#${src[1] & 0xFF:02X}']
    raise ValueError(f'bad param source {src!r}')


def exit_lines(i):
    if not i.exits:
        return ['\trts']
    e = i.exits[0]
    return [f'\tjmp\t{(i.image.upper() + "_") if i.image else "E"}{e:04X}']


def prepare_vbl_poll(i):
    p = i.params
    v = 0x80 if p.get('exit_n', 0) else 0x00
    seta = [f'\tlda\t#${v:02X}']
    if p.get('keep_a'):
        seta = ['\tpshs\ta'] + seta + ['\tpuls\ta']
    return {'SETA': seta}


ROM_WAIT_OVA = 153          # rom_wait's own fixed cost in Apple cycles (calibrated by tests/hw_checks.py)


def prepare_rom_wait(i):
    return {'OVA': ROM_WAIT_OVA}


DELAY_OVA = 96             # delay's own fixed cost in Apple cycles (calibrated by tests/hw_checks.py)
MAX_APPLE = (1 << 21) - 1   # hw_dly's input range


def exit_regs(p):
    """lines that set the registers and N/Z the 6502 code leaves: params exit_regs {'a'|'x'|'y': value} and
    exit_flags {'n': 0|1, 'z': 0|1} (default: N=0, Z=1, as after a counter reaches 0); C and V are kept"""
    out = []
    for r, v in sorted(p.get('exit_regs', {}).items()):
        if r == 'a':
            out += [f'\tlda\t#${v:02X}']
        elif r == 'x':
            out += [f'\tldb\t#${v:02X}']
        elif r == 'y':
            out += ['\tclr\tRY'] if v == 0 else ['\tpshs\ta', f'\tlda\t#${v:02X}', '\tsta\tRY', '\tpuls\ta']
        else:
            raise ValueError(f'exit_regs: bad register {r!r}')
    if out:
        out = ['\tpshs\tcc'] + out + ['\tpuls\tcc']    # CLR/LD change C and V: keep the 6502's C
    f = p.get('exit_flags', {'n': 0, 'z': 1})
    out += ['\tandcc\t#$F3']
    bits = (0x08 if f.get('n') else 0) | (0x04 if f.get('z') else 0)
    if 'c' in f:                                    # a CPX/CPY in the 6502 code decided C
        out[-1] = '\tandcc\t#$F2'
        bits |= 0x01 if f['c'] else 0
    if bits:
        out += [f'\torcc\t#${bits:02X}']
    return out


def prepare_delay(i):
    p = i.params
    inner = p.get('inner')
    ip = p['inner_pass'] if inner else 0
    op, c = p['outer_pass'], p.get('const', 0)
    if ip * 256 + op > 0xFFFF or c + 256 * (op + 256 * ip) > MAX_APPLE:
        raise ValueError(f'{symbol(i)}: delay too long for hw_dly')
    sub = DELAY_OVA - c
    if not 0 <= sub <= 0xFFFF:
        raise ValueError(f'{symbol(i)}: const {c} out of range')
    return {'LOAD_OUTER': load(p['outer']), 'LOAD_INNER': load(inner) if inner else ['\tlda\t#1'],
            'INNER_PASS': ip, 'OUTER_PASS': op, 'SUB': sub, 'EXITREGS': exit_regs(p)}


TONE_FIX = 86              # CoCo cycles of one tone pass outside its 8-cycle loop (calibrated by tests/hw_checks.py)
TONE_SETUP = 22            # tone setup cost in 8-cycle units, taken off its first pass (calibrated)
BELL_OVA = 26              # rom_bell: its own cost per click in Apple cycles (calibrated)


def prepare_tone(i):
    p = i.params
    ip, op = p['inner_pass'], p['outer_pass']
    if 256 * ip + op > 9362:
        raise ValueError(f'{symbol(i)}: tone pass too long')
    return {'LOAD_DURATION': load(p['duration']), 'LOAD_PERIOD': load(p['period']), 'INNER_PASS': ip,
            'OUTER_PASS': op, 'FIX': TONE_FIX, 'SETUP': TONE_SETUP, 'EXITREGS': exit_regs(p)}


def paddle_model(joy, pass_cycles, max_count):
    """the count an Apple paddle loop of pass_cycles Apple cycles per pass reaches for CoCo stick value joy
    (0..63 -> paddle 0..255; the timer runs about 11 Apple cycles per paddle unit), capped at max_count"""
    return min(max_count, (joy * 255 // 63) * 11 // pass_cycles)


def _tab(vals):
    return ','.join(f'${v:02X}' for v in vals)


def prepare_paddle_count(i):
    p = i.params
    if p.get('paddle') not in (0, 1, 2, 3):
        raise ValueError(f'{symbol(i)}: paddle number not constant')
    mx = p.get('max', 255)
    store = ['\tsta\tRY'] if p.get('counter', 'y') == 'y' else ['\ttfr\ta,b']
    regs = dict(p.get('exit_regs', {}))
    regs.pop(p.get('counter', 'y'), None)
    pm = dict(p, exit_regs=regs)
    pmax = dict(p, exit_regs=regs, exit_flags=p.get('exit_flags_max', p.get('exit_flags', {'n': 0, 'z': 1})))
    return {'JOY': 'joy_y' if p['paddle'] & 1 else 'joy_x', 'STORE': store, 'MAX': mx,
            'TAB': _tab(paddle_model(j, p['pass_cycles'], mx) for j in range(64)),
            'EXITREGS': exit_regs(pm), 'EXITREGS_MAX': exit_regs(pmax)}


def prepare_rom_pread(i):
    return {'TAB': _tab(min(255, j * 255 // 63) for j in range(64))}


def _wait(a):
    a = a or 256
    return (26 + 27 * a + 5 * a * a) // 2


def prepare_rom_bell(i):
    return {'PRE': 2 + _wait(64) + 2, 'PASS': 2 + _wait(12) + 4 + 2 + 3 - BELL_OVA}


def render(i):
    subs = {'SYM': [symbol(i)], 'EXIT': exit_lines(i)}
    for k, v in globals()[f'prepare_{i.kind}'](i).items():
        subs[k] = v if isinstance(v, list) else [str(v)]
    return _fill(open(os.path.join(TDIR, f'{i.kind}.asm')).read(), subs)


def _fill(text, subs):
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith('@') and s.endswith('@') and s[1:-1] in subs and s.count('@') == 2:
            out += subs[s[1:-1]]                 # a placeholder alone on its line: a block of lines
            continue
        for k, v in subs.items():
            if f'@{k}@' in ln:
                ln = ln.replace(f'@{k}@', v[0] if len(v) == 1 else '\n'.join(v))
        if '@' in ln.split(';')[0]:
            raise ValueError(f'unfilled placeholder: {ln!r}')
        out += ln.split('\n')
    return out


def helpers(kinds):
    if not set(kinds) & NEEDS_DLY:
        return []
    return _fill(open(os.path.join(TDIR, 'dly.asm')).read(), {'OVH': [str(HW_OVH)]})


def render_all(idioms):
    idioms = list(idioms)
    out = []
    for i in idioms:
        out += render(i)
    return out + helpers(i.kind for i in idioms)
