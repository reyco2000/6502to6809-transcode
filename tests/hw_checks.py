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
