"""Evidence for hardware idioms: an instance of the library routine against the 6502 code it replaces.

    ev = verify(match, mem, n=300)                     -> Evidence; str(ev) = 'verified 300/300, ...'
    ev = difftest_instance(idiom, code_6502, n=300)    the plain register/flag/RAM (+ total time) comparison

verify() picks the comparison per kind: registers, flags and RAM always; for tone the click intervals ($C030 on the
6502, $FF20 DAC writes in cc3sim, converted by 1.75); for delay / rom_wait the total time; for paddle_count the
count against an Apple paddle-timer model; for vbl_poll a RDVBL model and one rt_vbl call.  ROM entries are tested
on a copy of the documented ROM routine.
"""
import random
from dataclasses import dataclass, field

from .apple2 import APPLE_TO_COCO, DELAY_MIN
from .idiom import symbol
from .lib import render_all, TONE_MIN_PASS


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
                      render_lines=None, io=None, check=None):
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
        model = io(rnd, ram) if io else None
        ref = reference(ram, idiom.entry, a, x, y, p, inside, {}, max_instr=3000000, io=model)
        if ref['stop'] in ('wild', 'illegal', 'timeout', 'zpwrap'):
            skipped += 1
            continue
        got = H.run(ram, symbol(idiom), a, x, y, p, max_instr=30000000, watch=(0xFF20,))
        ev.cases += 1
        diff = compare(ref, got, live)
        if not diff and check is not None:
            diff = check(ref, got, model, H, ev)
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


# monitor WAIT ($FCA8), written out from its documented source (SEC / W2: PHA / W3: SBC #1 / BNE W3 / PLA /
# SBC #1 / BNE W2 / RTS): code, not ROM bytes
WAIT_6502 = bytes([0x38, 0x48, 0xE9, 0x01, 0xD0, 0xFC, 0x68, 0xE9, 0x01, 0xD0, 0xF6, 0x60])
JOY = 0xBF00                # test layout: joy_x / joy_y live in Apple RAM, set per case


class ClickLog:
    """6502 side: the cycle stamps of every $C030 access"""
    def __init__(self):
        self.t = []

    def read(self, a, cyc):
        if 0xC030 <= a <= 0xC03F:
            self.t.append(cyc)
        return 0

    def write(self, a, v, cyc):
        self.read(a, cyc)


class PaddleTimer:
    """6502 side: $C070 starts the timers; $C064+n reads bit 7 = 1 while the paddle's time has not run out
    (paddle value = stick x 255/63, about 11 Apple cycles per unit)"""
    def __init__(self, joys):
        self.joys, self.t0 = joys, None

    def read(self, a, cyc):
        if a == 0xC070 or 0xC071 <= a <= 0xC07F:
            self.t0 = cyc
            return 0
        if 0xC064 <= a <= 0xC067 and self.t0 is not None:
            j = self.joys[(a - 0xC064) & 1]
            return 0x80 if cyc - self.t0 < (j * 255 // 63) * 11 else 0
        return 0

    def write(self, a, v, cyc):
        self.read(a, cyc)


class Blank:
    """6502 side: RDVBL reads give `first` for k reads, then the other polarity for k reads, and so on"""
    def __init__(self, first, k):
        self.v, self.k, self.n = first, k, 0

    def read(self, a, cyc):
        if a != 0xC019:
            return 0
        self.n += 1
        if self.n > self.k:
            self.v, self.n = self.v ^ 0x80, 1
        return self.v

    def write(self, a, v, cyc):
        pass


def _intervals(ts):
    return [b - a for a, b in zip(ts, ts[1:])]


def verify(m, mem, n=300, seed=1, render_lines=None):
    """evidence for one proposal (kit/hw/match.Match) on the 64K image mem"""
    from dataclasses import replace
    i = m.idiom
    if i.kind == 'rom_wait':
        loc = replace(i, entry=0x0800, end=0x080B, exits=())
        return difftest_instance(loc, WAIT_6502, n=n, seed=seed, render_lines=render_lines)
    if i.entry >= 0xC000:
        ev = Evidence()
        ev.notes.append(f'{i.kind}: ROM routine, not compared (no 6502 copy in the kit)')
        return ev
    code = bytes(mem[i.entry:i.end + 1])
    joy = [f'joy_x\tequ\t${JOY:04X}', f'joy_y\tequ\t${JOY + 1:04X}']
    if i.kind in ('delay',):
        return difftest_instance(i, code, n=n, seed=seed, render_lines=render_lines)
    if i.kind == 'tone':
        def per(ref, got, model, H, ev):
            a = _intervals(model.t)
            c = _intervals([w[2] for w in got['writes'] if w[0] == 0xFF20])
            if len(model.t) != len(got['writes']):
                return [f'{len(model.t)} clicks on the Apple, {len(got["writes"])} on the CoCo']
            if a and sum(a) / len(a) < TONE_MIN_PASS:
                ev.short = getattr(ev, 'short', 0) + 1     # above ~12 kHz: below the routine's floor
                return []
            if a and sum(a) >= DELAY_MIN:
                ev.timed += 1
                ev.max_timing_err = max(ev.max_timing_err, abs(sum(c) / (sum(a) * APPLE_TO_COCO) - 1))
            return []
        ev = difftest_instance(i, code, n=n, seed=seed, render_lines=render_lines, timed=False,
                               io=lambda rnd, ram: ClickLog(), check=per)
        if getattr(ev, 'short', 0):
            ev.notes.append(f'{ev.short} case(s) with a pass under {TONE_MIN_PASS} Apple cycles not timed '
                            f'(the routine\'s floor)')
        return ev
    if i.kind == 'paddle_count':
        reg = i.params.get('counter', 'y')
        live = 0x3F & ~(2 if reg == 'x' else 4) & ~0x38          # the count is compared with a tolerance of 1

        def io(rnd, ram):
            j = (rnd.randrange(64), rnd.randrange(64))
            ram[JOY], ram[JOY + 1] = j
            return PaddleTimer(j)

        def cnt(ref, got, model, H, ev):
            if abs(ref[reg] - got[reg]) > 1:
                return [f'count {got[reg]} on the CoCo, {ref[reg]} on the Apple (stick {model.joys})']
            return []
        return difftest_instance(i, code, n=n, seed=seed, render_lines=render_lines, timed=False, live=live,
                                 extra_lines=joy, io=io, check=cnt)
    if i.kind == 'vbl_poll':
        first = 0x80 if i.params.get('exit_n', 0) == 0 else 0x00
        calls = ['rt_vbl_n\trmb\t1']

        def once(ref, got, model, H, ev):
            k = got['hi'][H.syms['rt_vbl_n'] - 0xC000]
            return [] if k == 1 else [f'rt_vbl called {k} times']
        lines = list(render_lines) if render_lines is not None else render_all([i])
        lines = [ln.replace('\tjsr\trt_vbl', '\tinc\trt_vbl_n\n\tjsr\trt_vbl') for ln in lines]
        lines = [x for ln in lines for x in ln.split('\n')]
        return difftest_instance(i, code, n=n, seed=seed, render_lines=lines + calls, timed=False,
                                 io=lambda rnd, ram: Blank(first, rnd.randrange(1, 40)), check=once)
    ev = Evidence()
    ev.notes.append(f'{i.kind}: no verification model')
    return ev
