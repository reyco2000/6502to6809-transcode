"""Accepted hardware idioms (spec HW_IDIOMS) and their expansion into ordinary natives.

    from kit.hw import Idiom
    HW_IDIOMS = [Idiom('tone', entry=0x0D00, end=0x0D3A, exits=(0x0D3B,), params={...})]

expand(spec) adds, for each idiom, NATIVE[entry] = hw_<kind>_<entry>, NATIVE_RANGES += (entry, end) and
NATIVE_EXITS += exits.  The routine itself is rendered from rt/hw/apple2/<kind>.asm (kit/hw/lib.py) into the
generated hw_inst.asm.  Matchers (kit/hw/match.py) only ever *propose* idioms: the user moves them into spec.py.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Idiom:
    kind: str
    entry: int
    end: int
    exits: tuple = ()
    params: dict = field(default_factory=dict, hash=False, compare=True)
    image: str | None = None        # the overlay image whose code the exits jump into (None = engine)


def symbol(i):
    return f'hw_{i.kind}_{i.entry:04x}'


def _overlap(a, b):
    return a[0] <= b[1] and b[0] <= a[1]


def expand(spec):
    """merge spec.HW_IDIOMS into NATIVE / NATIVE_RANGES / NATIVE_EXITS (idempotent); overlaps are errors"""
    if getattr(spec, '_hw_expanded', False):
        return
    from .lib import KINDS, ROM_CONTRACTS
    idioms = list(getattr(spec, 'HW_IDIOMS', ()))
    native = dict(spec.NATIVE)
    ranges = list(spec.NATIVE_RANGES)
    exits = list(spec.NATIVE_EXITS)
    errors = []
    for k, i in enumerate(idioms):
        r = (i.entry, i.end)
        if i.kind not in KINDS:
            errors.append(f'{symbol(i)}: unknown idiom kind {i.kind!r} (known: {", ".join(sorted(KINDS))})')
        if i.end < i.entry:
            errors.append(f'{symbol(i)}: end ${i.end:04X} before entry')
        for j in idioms[:k]:
            if _overlap(r, (j.entry, j.end)):
                errors.append(f'{symbol(i)} overlaps idiom {symbol(j)}')
        for lo, hi in spec.NATIVE_RANGES:
            if _overlap(r, (lo, hi)):
                errors.append(f'{symbol(i)} overlaps NATIVE_RANGES (${lo:04X}, ${hi:04X})')
        for a, s in spec.NATIVE.items():
            if i.entry <= a <= i.end:
                errors.append(f'{symbol(i)} overlaps NATIVE ${a:04X} {s}')
        native[i.entry] = symbol(i)
        ranges.append(r)
        exits += [e for e in i.exits if e not in exits]
    contracts = dict(getattr(spec, 'NATIVE_CONTRACTS', {}))
    for i in idioms:
        if i.entry >= 0xC000 and i.kind in ROM_CONTRACTS:       # ROM entry: no 6502 body to derive it from
            from .. import xc65
            use, defs = (eval(s, vars(xc65)) for s in ROM_CONTRACTS[i.kind])
            contracts.setdefault(symbol(i), xc65.Native(symbol(i), use=use, defs=defs))
        elif i.entry >= 0xC000:
            errors.append(f'{symbol(i)}: kind {i.kind} cannot replace a ROM entry')
    if errors:
        raise SystemExit('spec HW_IDIOMS:\n  ' + '\n  '.join(errors))
    spec.NATIVE, spec.NATIVE_RANGES, spec.NATIVE_EXITS = native, ranges, exits
    spec.NATIVE_CONTRACTS = contracts
    spec._hw_expanded = True
