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
HW_OVH = 34                 # hw_dly fixed cost in 8-cycle units (calibrated by tests/hw_checks.py)


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
