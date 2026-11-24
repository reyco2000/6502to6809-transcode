"""Hardware transcoder: the source machine's hardware map and its actions (machine-neutral parts).

Every hardware address translated code can touch has one entry in the machine's map (kit/hw/apple2.py) with
one action:
  Runtime(cond)   the runtime defines the handler io_<mode>_<ADDR> (with cond: only when the build sets that equate)
  Latch(sym)      a read with no side effect: a plain load of the runtime latch `sym`
  Ignore(value)   an explicit no-op: writes do nothing, reads give `value`
  Trap(reason)    unsupported: a translated access is a generation error (unless the spec overrides it)
  IdiomOnly(kinds) meaningful only inside a loop or call the idiom matchers replace (kit/hw/match.py)
Access modes: rd, wr (absolute), rdx, wrx (indexed by 6502 X; $C08x,X = slot I/O).
"""
import re
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Runtime:
    cond: str | None = None


@dataclass(frozen=True)
class Latch:
    sym: str


@dataclass(frozen=True)
class Ignore:
    value: int = 0


@dataclass(frozen=True)
class Trap:
    reason: str


@dataclass(frozen=True)
class IdiomOnly:
    kinds: tuple


RW = frozenset({'rd', 'wr'})


@dataclass(frozen=True)
class Hw:
    lo: int
    hi: int
    name: str
    cat: str
    action: object
    modes: frozenset = field(default=RW)
    doc: str = ''


_IOSYM = re.compile(r'io_(rdx|wrx|rd|wr)_([0-9A-F]{4})$')


def parse_io_symbol(sym):
    """'io_rdx_C061' -> ('rdx', 0xC061)"""
    m = _IOSYM.match(sym)
    if not m:
        raise ValueError(f'not an I/O handler symbol: {sym}')
    return m.group(1), int(m.group(2), 16)


def lookup_in(table, addr, mode, overrides=None):
    """the entry of `table` for (addr, mode), with a spec override's action applied"""
    hit = None
    for e in table:
        if e.lo <= addr <= e.hi and mode in e.modes:
            hit = e
            break
    if overrides and addr in overrides:
        if hit is None:
            return Hw(addr, addr, f'${addr:04X} (spec override)', 'override', overrides[addr], frozenset({mode}))
        return replace(hit, action=overrides[addr])
    return hit


from .idiom import Idiom  # noqa: E402,F401  (specs: from kit.hw import Idiom)
