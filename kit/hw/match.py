"""Hardware idiom matchers: find the standard hardware loops and ROM calls in a game and *propose* them.

    matches, near = find(mem, code, executed=None)
      mem       64K image; code: kit.discover.Code over it; executed: instruction addresses the profile ran
      matches   [Match(idiom, evidence)]   proposals for spec.HW_IDIOMS (never applied by the kit itself)
      near      [NearMiss(kind, lo, hi, reason)]   loops that look like an idiom but are not one: hand natives

Each matcher checks the shape of the code, extracts the parameters the library routine needs (kit/hw/lib.py)
and the exit state, or reports why it does not fit.  The user confirms every proposal against the listing.
"""
from dataclasses import dataclass, field

from . import IdiomOnly
from . import apple2
from .apple2 import DELAY_MIN
from .idiom import Idiom


@dataclass
class Match:
    idiom: Idiom
    evidence: list = field(default_factory=list)


@dataclass
class NearMiss:
    kind: str
    lo: int
    hi: int
    reason: str


BRANCH = {'BPL', 'BMI', 'BVC', 'BVS', 'BCC', 'BCS', 'BNE', 'BEQ'}
STORES = {'STA', 'STX', 'STY', 'INC', 'DEC', 'ASL', 'LSR', 'ROL', 'ROR'}
DECS = {'DEX': 'x', 'DEY': 'y'}
LOADS = {'LDX': 'x', 'LDY': 'y'}


def target(ins):
    """branch / JMP target, or None"""
    if ins.mode == 'rel':
        off = ins.arg - 256 if ins.arg >= 128 else ins.arg
        return (ins.addr + 2 + off) & 0xFFFF
    if ins.mn in ('JMP', 'JSR') and ins.mode == 'abs':
        return ins.arg
    return None


def cyc(ins, taken=False):
    """Apple cycles of an instruction (base; a taken branch +1; no page-cross extras)"""
    from ..survey import cycles
    return cycles(ins.op) + (1 if taken and ins.mode == 'rel' else 0)


def body(code, head, tail):
    """the instructions from head to tail in address order (straight-line), or None if there is a gap"""
    out, a = [], head
    while a <= tail:
        ins = code.insns.get(a)
        if ins is None:
            return None
        out.append(ins)
        a += ins.len
    return out if a == tail + code.insns[tail].len else None


def loops(code):
    """(head, tail, exit) for every backward branch / JMP inside straight-line code"""
    out = []
    for a, ins in sorted(code.insns.items()):
        t = target(ins)
        if t is not None and ins.mn != 'JSR' and t <= a and t in code.insns and body(code, t, a):
            out.append((t, a, a + ins.len))
    return out


def io_addr(ins):
    return ins.arg if ins.mode in ('abs', 'absx', 'absy') and ins.arg is not None and 0xC000 <= ins.arg <= 0xC0FF \
        else None


def _source(ins):
    """the param source of a counter load (LDX/LDY #imm / zp / abs RAM), or None"""
    if ins.mode == 'imm':
        return ('imm', ins.arg)
    if ins.mode == 'zp':
        return ('zp', ins.arg)
    if ins.mode == 'abs' and ins.arg < 0xC000:
        return ('abs', ins.arg)
    return None


# ------------------------------------------------------------------------------------------------ vbl_poll
def _vbl_loop(code, head):
    """(exit_n, keep_a, tail) when head starts L: LDA|BIT $C019 / BPL|BMI L"""
    i = code.insns.get(head)
    if i is None or i.mn not in ('LDA', 'BIT') or i.mode != 'abs' or i.arg != 0xC019:
        return None
    b = code.insns.get(head + i.len)
    if b is None or b.mn not in ('BPL', 'BMI') or target(b) != head:
        return None
    return (1 if b.mn == 'BPL' else 0), (1 if i.mn == 'BIT' else 0), b.addr + b.len - 1


def m_vbl_poll(mem, code, loop):
    head, tail, ex = loop
    v = _vbl_loop(code, head)
    if v is None or v[2] != tail + 1:
        return None
    exit_n, keep_a, end = v
    w = _vbl_loop(code, ex)                       # the paired loop for the other edge
    if w is not None and w[0] != exit_n and w[1] == keep_a:
        exit_n, end = w[0], w[2]
        ex = end + 1
    return Match(Idiom('vbl_poll', head, end, (ex,), {'exit_n': exit_n, 'keep_a': keep_a}),
                 [f'RDVBL poll loop ${head:04X}-${end:04X}'])


# ------------------------------------------------------------------------------------------------ delay
def _count_loop(ins_list):
    """a loop body [NOP..., DEr, NOP..., Bxx back]: -> (reg, cycles of one taken pass) or None"""
    *rest, br = ins_list
    if br.mn != 'BNE':
        return None
    decs = [i for i in rest if i.mn in DECS]
    if len(decs) != 1 or any(i.mn not in DECS and i.mn != 'NOP' for i in rest):
        return None
    return DECS[decs[0].mn], sum(cyc(i) for i in rest) + 3


def m_delay(mem, code, loop):
    head, tail, ex = loop
    ins = body(code, head, tail)
    if any(io_addr(i) is not None for i in ins):
        return None
    *rest, br = ins
    if br.mn != 'BNE':
        return None
    if any(i.mn in STORES for i in rest):
        if any(i.mn in DECS for i in rest):
            return NearMiss('delay', head, tail, 'loop body writes memory: not a pure delay')
        return None
    one = _count_loop(ins)
    inner = None
    if one is None:
        # outer: [LDr' source, inner loop on r', NOP..., DEr, NOP..., BNE head]
        if len(rest) < 3 or rest[0].mn not in LOADS:
            return None
        ir = LOADS[rest[0].mn]
        src = _source(rest[0])
        k = next((n for n, i in enumerate(rest) if i.mn == 'BNE'), None)
        if src is None or k is None or target(rest[k]) != rest[1].addr:
            return None
        il = _count_loop(rest[1:k + 1])
        if il is None or il[0] != ir:
            return None
        tail_part = rest[k + 1:]
        decs = [i for i in tail_part if i.mn in DECS]
        if len(decs) != 1 or any(i.mn not in DECS and i.mn != 'NOP' for i in tail_part) or DECS[decs[0].mn] == ir:
            return None
        reg = DECS[decs[0].mn]
        inner, inner_pass = src, il[1]
        outer_pass = cyc(rest[0]) + sum(cyc(i) for i in tail_part) + 3 - 1    # the inner loop's last BNE: 2
    else:
        reg, outer_pass = one
        inner_pass = 0
    entry, const = head, -1                       # the last BNE is not taken
    outer = (reg,)
    prev = [a for a, i in code.insns.items() if a + i.len == head and i.mn in LOADS and LOADS[i.mn] == reg]
    if prev and _source(code.insns[prev[0]]) is not None and prev[0] not in code.targets.get(head, ()):
        entry = prev[0]
        outer = _source(code.insns[prev[0]])
        const += cyc(code.insns[prev[0]])
    big = lambda s: 256 if s[0] != 'imm' else (s[1] or 256)
    total = const + big(outer) * (outer_pass + (big(inner) * inner_pass if inner else 0))
    if total < DELAY_MIN:
        return None
    regs = {reg: 0}
    if inner:
        regs[LOADS[rest[0].mn]] = 0
    params = dict(outer=outer, inner_pass=inner_pass, outer_pass=outer_pass, const=const, exit_regs=regs)
    if inner:
        params['inner'] = inner
    end = tail + code.insns[tail].len - 1
    return Match(Idiom('delay', entry, end, (ex,), params),
                 [f'counted delay ${entry:04X}-${end:04X}, up to {total:,} Apple cycles'])


# ------------------------------------------------------------------------------------------------ ROM calls
def m_rom(mem, code):
    out, seen = [], set()
    for a, ins in sorted(code.insns.items()):
        if ins.mn != 'JSR' or ins.arg in seen:
            continue
        e = apple2.rom(ins.arg)
        if e is not None and isinstance(e.action, IdiomOnly):
            seen.add(ins.arg)
            out.append(Match(Idiom(e.action.kinds[0], ins.arg, ins.arg), [f'JSR ${ins.arg:04X} {e.name} at ${a:04X}']))
    return out


LOOP_MATCHERS = [m_vbl_poll, m_delay]


def find(mem, code, executed=None):
    """-> (matches, near-misses), outermost loops first; nothing inside an accepted range is reported again"""
    matches, near, taken = [], [], []
    inside = lambda lo, hi: any(a <= lo and hi <= b for a, b in taken)
    for lp in sorted(loops(code), key=lambda l: (l[0] - l[1], l[0])):      # longest first
        head, tail, _ = lp
        if executed is not None and not any(a in executed for a in range(head, tail + 1)):
            continue
        if inside(head, tail):
            continue
        for m in LOOP_MATCHERS:
            r = m(mem, code, lp)
            if isinstance(r, Match):
                matches.append(r)
                taken.append((r.idiom.entry, r.idiom.end))
                break
            if isinstance(r, NearMiss):
                near.append(r)
                break
    matches += m_rom(mem, code)
    matches.sort(key=lambda m: m.idiom.entry)
    return matches, near
