"""Evidence for hardware idioms: an instance of the library routine against the 6502 code it replaces.

    r = difftest_instance(idiom, code_6502, n=300)      -> Evidence (cases, passed, max_timing_err, notes)
"""
import random
from dataclasses import dataclass, field

from .apple2 import APPLE_TO_COCO, DELAY_MIN
from .idiom import symbol
from .lib import render_all


@dataclass
class Evidence:
    cases: int = 0
    passed: int = 0
    max_timing_err: float = 0.0
    timed: int = 0
    notes: list = field(default_factory=list)

    def __str__(self):
        s = f'verified {self.passed}/{self.cases}'
        if self.timed:
            s += f', timing error {100 * self.max_timing_err:.1f}% max ({self.timed} timed)'
        return s + (f'; {"; ".join(self.notes[:3])}' if self.notes else '')


_BASE = {}


def _base_cycles(H):
    """cycles of the test driver around an empty routine (subtracted from the instance's)"""
    if id(H) not in _BASE:
        r = H.run(bytearray(0xC000), 'hwt_empty', 0, 0, 0, 0)
        _BASE[id(H)] = r['cycles']
    return _BASE[id(H)]


def difftest_instance(idiom, code, n=300, seed=1, live=0x3F, setup=None, extra_lines=(), timed=True,
                      render_lines=None):
    """place `code` (the 6502 routine) at idiom.entry, run it on the 6502 reference and the rendered instance
    in cc3sim on n random cases; compare registers/flags (mask `live`) and RAM, and the time (Apple cycles x
    1.75, cases of at least DELAY_MIN Apple cycles) when `timed`"""
    from ..difftest import Harness, reference, compare
    exits = {f'{(idiom.image.upper() + "_") if idiom.image else "E"}{e:04X}': e for e in idiom.exits}
    lines = list(render_lines) if render_lines is not None else render_all([idiom])
    H = Harness(lines, exits, extra_lines=['rt_vbl\trts', 'hwt_empty\trts', 'x_trap\tbra\tx_trap'] + list(extra_lines))
    base = _base_cycles(H)
    rnd = random.Random(seed)
    ev = Evidence()
    inside = set(range(idiom.entry, idiom.end + 1))
    skipped = 0
    for _ in range(n):
        ram = bytearray(rnd.randrange(256) for _ in range(0xC000))
        ram[0x100:0x200] = bytes(0x100)
        ram[idiom.entry:idiom.entry + len(code)] = code
        a, x, y, p = (rnd.randrange(256) for _ in range(4))
        if setup:
            a, x, y, p = setup(rnd, ram, a, x, y, p)
        ref = reference(ram, idiom.entry, a, x, y, p, inside, {}, max_instr=3000000)
        if ref['stop'] in ('wild', 'illegal', 'timeout', 'zpwrap'):
            skipped += 1
            continue
        got = H.run(ram, symbol(idiom), a, x, y, p, max_instr=30000000)
        ev.cases += 1
        diff = compare(ref, got, live)
        if diff:
            if len(ev.notes) < 3:
                ev.notes.append(f'a={a:02X} x={x:02X} y={y:02X}: {diff[0]}')
            continue
        ev.passed += 1
        if timed:
            apple = ref['cycles'] - (6 if ref['stop'] == 'ret' else 0)
            coco = got['cycles'] - base
            if apple >= DELAY_MIN:
                ev.timed += 1
                ev.max_timing_err = max(ev.max_timing_err, abs(coco / (apple * APPLE_TO_COCO) - 1))
    if skipped:
        ev.notes.append(f'{skipped} random case(s) left the routine (not comparable)')
    return ev
