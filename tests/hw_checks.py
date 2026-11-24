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
