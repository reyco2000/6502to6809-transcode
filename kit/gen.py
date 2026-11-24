"""Generate 6809 source for a port's engine and overlays (game-independent).

    from kit import gen; gen.use(spec); gen.build(outdir)          (a port's gen.py is a two-line wrapper)

The port's spec module supplies the images and natives (see docs/SPEC.md): IMAGES, entries(), load_images(),
profile_roots(), NATIVE, NATIVE_RANGES, CODE_RANGES, SMC_BYTES, SMC_IMM, and optionally NATIVE_CONTRACTS (natives
with no 6502 body, or that never return), IO_MEM (I/O reads that are plain loads of runtime latches) and ROMCONST
(ROM bytes the game reads as data).

Writes engine.asm, ovl_<name>.asm, xiy.asm (pointer helpers), ovt.inc (engine -> overlay vector slots) and
report.txt.  Generated files contain translated game code: they go to build/, never into the repo.
"""
import argparse
import os
import sys

from .program import split
from . import hw
from .hw import apple2 as hw_apple2, iocheck, lib as hw_lib, idiom as hw_idiom
from .xc65 import Ctx, Native, translate, xiy_helpers, XError, routine_contract, REGS, FLAGS, ALL, A_, X_, Y_  # noqa: F401

IO = {}
for _a in range(0xC000, 0xC100):
    IO[_a] = (f'io_rd_{_a:04X}', f'io_wr_{_a:04X}')
# defaults; a port overrides them in its spec
# plain loads of the runtime's latches: the hardware map's Latch read entries (kit/hw/apple2.py)
IO_MEM_DEFAULT = {a: e.action.sym for e in hw_apple2.MAP if isinstance(e.action, hw.Latch) and 'rd' in e.modes
                  for a in range(e.lo, e.hi + 1)}
spec = None
IO_MEM, ROMCONST, NATIVE_CONTRACTS, HW_OVERRIDES = IO_MEM_DEFAULT, {}, {}, {}


def use(port_spec):
    """select the port whose spec module drives the generator"""
    global spec, IO_MEM, ROMCONST, NATIVE_CONTRACTS, HW_OVERRIDES
    spec = port_spec
    hw_idiom.expand(spec)                            # accepted idioms (spec HW_IDIOMS) become natives
    HW_OVERRIDES = dict(getattr(spec, 'HW_OVERRIDES', {}))
    IO_MEM = dict(getattr(spec, 'IO_MEM', IO_MEM_DEFAULT))
    for a, act in HW_OVERRIDES.items():                # an override to a latch is a plain load too
        if isinstance(act, hw.Latch):
            IO_MEM.setdefault(a, act.sym)
    ROMCONST = dict(getattr(spec, 'ROMCONST', {}))
    if getattr(spec, 'ROMDATA', ()):                 # the 6502 reference (difftest) reads the same ROM bytes
        rom = open(spec.PORT.rom, 'rb').read()
        for lo, hi in spec.ROMDATA:
            for a in range(lo, hi + 1):
                ROMCONST.setdefault(a, rom[len(rom) - (0x10000 - a)])
    NATIVE_CONTRACTS = getattr(spec, 'NATIVE_CONTRACTS', {})


def natives(mem):
    out = {a: NATIVE_CONTRACTS[s] for a, s in spec.NATIVE.items() if s in NATIVE_CONTRACTS}
    for a, sym in spec.NATIVE.items():
        if sym not in NATIVE_CONTRACTS:
            rng = [r for r in spec.NATIVE_RANGES if r[0] <= a <= r[1]] or [(a, a + 0xFF)]
            out[a] = routine_contract(mem, a, sym, natives=out, code_ranges=rng)
    return out


def cross_live(P, nat):
    """liveness of the engine and every overlay, solved together (least fixpoint across the sets)"""
    from .xc65 import Live, ALL
    eng = P.engine
    names = list(P.overlays)
    roots = {n: set(spec.entries(n)) for n in names}
    eroots = set().union(*roots.values()) & set(eng.insns)
    elive, olive = None, {n: None for n in names}
    ea = spec.profile_ea() if (getattr(spec, 'HIRES_ACCESS', False) or getattr(spec, 'ROMDATA', ())) else {}
    hires = ea if getattr(spec, 'HIRES_ACCESS', False) else None
    romdata = getattr(spec, 'ROMDATA', ())
    zp_wrap = spec.profile_flags(2) if hasattr(spec, 'profile_flags') else set()

    def e_ext_live(a):          # engine transfer to an overlay address
        v = 0
        for n in names:
            if olive[n] is not None and a in P.overlays[n].insns:
                v |= olive[n].before[a]
        return v

    def o_ext_live(a):          # overlay transfer to the engine
        return elive.before.get(a, 0) if elive is not None else 0

    for _ in range(50):
        # engine routines called from overlays: live at the overlay return sites; tail-jumped: ALL
        eret = {}
        for n in names:
            ov = P.overlays[n]
            for t, refs in ov.outside.items():
                for kind, site in refs:
                    if kind == 'jsr':
                        r = ov.insns[site].next
                        eret[t] = eret.get(t, 0) | (olive[n].lb(r) if olive[n] else 0)
                    elif kind != 'ret':
                        eret[t] = ALL
        ectx = Ctx(natives=nat, io=IO, io_mem=IO_MEM, romconst=ROMCONST, smc_imm=list(spec.SMC_IMM) + list(getattr(spec, 'SMC_OPS', {})), ext_live=e_ext_live,
                   ext_ret=eret, roots=eroots, traps=traps_of(getattr(spec, 'BASE', 'cold')), hires=hires,
                   romdata=romdata, ea=ea, zp_wrap=zp_wrap)
        new_e = Live(eng, ectx)
        new_o = {}
        octxs = {}
        for n in names:
            ov = P.overlays[n]
            oret = {}
            for t, refs in P.ovl_entries[n].items():
                for kind, site in refs:
                    if kind == 'jsr':
                        oret[t] = oret.get(t, 0) | new_e.lb(eng.insns[site].next)
                    elif kind != 'ret':
                        oret[t] = ALL
            octxs[n] = Ctx(natives=nat, io=IO, io_mem=IO_MEM, romconst=ROMCONST, smc_imm=list(spec.SMC_IMM) + list(getattr(spec, 'SMC_OPS', {})), ext_live=o_ext_live,
                           ext_ret=oret, roots=roots[n] & set(ov.insns), traps=set(P.traps[n]) | traps_of(n), hires=hires,
                           romdata=romdata, ea=ea, zp_wrap=zp_wrap)
            new_o[n] = Live(ov, octxs[n])
        stable = elive is not None and new_e.before == elive.before and \
            all(new_o[n].before == olive[n].before for n in names)
        elive, olive = new_e, new_o
        if stable:
            return elive, olive, ectx, octxs
    raise SystemExit('cross liveness did not converge')


def romdata_lines():
    """spec ROMDATA [(lo, hi)]: ROM_<lo> tables with the bytes of the user's ROM (port.toml rom; build output
    only, never committed).  Translated code and natives read them instead of the Apple ROM."""
    rd = getattr(spec, 'ROMDATA', ())
    if not rd:
        return []
    rom = open(spec.PORT.rom, 'rb').read()
    out = []
    for lo, hi in rd:
        b = rom[len(rom) - (0x10000 - lo):len(rom) - (0x10000 - hi) + 1]
        out.append(f'ROM_{lo:04X}')
        out += ['\tfcb\t' + ','.join(f'${x:02X}' for x in b[k:k + 16]) for k in range(0, len(b), 16)]
    # xrd: a pointer read (X = Apple address) that may reach ROM: ROMDATA tables, else hi-res / RAM.  -> IOV
    out += ['xrd\tpshs\tcc']
    for k, (lo, hi) in enumerate(rd):
        out += [f'\tcmpx\t#${lo:04X}', f'\tblo\txrd_{k}', f'\tcmpx\t#${hi:04X}', f'\tbhi\txrd_{k}',
                f'\tleax\tROM_{lo:04X}-${lo:04X},x', '\tpshs\ta', '\tlda\t,x', '\tsta\tIOV', '\tpuls\ta',
                f'\tleax\t${lo:04X}-ROM_{lo:04X},x', '\tpuls\tcc,pc', f'xrd_{k}']
    if getattr(spec, 'HIRES_ACCESS', False):
        out += ['\tpuls\tcc', '\tjmp\thr_rd']
    else:
        out += ['\tpshs\ta', '\tlda\t,x', '\tsta\tIOV', '\tpuls\ta', '\tpuls\tcc,pc']
    return out


def traps_of(name):
    """spec TRAPS for one code set: bare addresses everywhere, (image, addr) only in that image"""
    t = getattr(spec, 'TRAPS', ())
    return {a for a in t if not isinstance(a, tuple)} | {a[1] for a in t if isinstance(a, tuple) and a[0] == name}


def check_traps(imgs):
    """a trap on code the profile executed in that image would halt the game: refuse it"""
    roots = spec.profile_roots(imgs)
    bad = [f'TRAPS ${a:04X} in {n}: the profile executed it' for n in imgs for a in traps_of(n) if a in roots.get(n, ())]
    if bad:
        raise SystemExit('\n'.join(bad))


def program(imgs):
    """the engine / overlay split of the port's images with every spec option (gen and test_engine)"""
    return split(imgs, {n: spec.entries(n) for n in spec.IMAGES}, spec.profile_roots(imgs), spec.NATIVE,
                 spec.NATIVE_RANGES, spec.CODE_RANGES, spec.SMC_BYTES, base=getattr(spec, 'BASE', 'cold'),
                 traps=getattr(spec, 'TRAPS', ()), smc_jmp=getattr(spec, 'SMC_JMP', {}),
                 smc_ops=getattr(spec, 'SMC_OPS', {}))


def build(outdir, comments=True):
    imgs = spec.load_images()
    check_traps(imgs)
    P = program(imgs)
    if P.errors:
        hint = ''
        if P.trap_hints:
            hint = ('\n\nThese errors sit on paths the profile never executed.  If the listing shows they are dead '
                    '(data after a branch that is always taken, etc.), add the first unexecuted address to spec TRAPS '
                    '(the path becomes `jsr x_trap`); otherwise profile the path:\n' +
                    '\n'.join(f'  0x{t:04X},   # leads to {" ".join(f"${b:04X}" for b in sorted(bs))}'
                              for t, bs in sorted(P.trap_hints.items())))
        raise SystemExit('\n'.join(P.errors) + hint)
    os.makedirs(outdir, exist_ok=True)
    nat = natives(imgs[getattr(spec, 'BASE', 'cold')])
    elive, olive, ectx, octxs = cross_live(P, nat)
    # engine -> overlay vector slots (global order)
    slots = sorted(set().union(*[set(P.ovl_entries[n]) | set(P.traps[n]) for n in P.overlays]))
    ovt = {a: f'OVT_{a:04X}' for a in slots}
    rep = []
    ptrs = set()
    eng_labels = set(P.engine.insns)
    ectx.outside = lambda a: ovt.get(a)
    lines, em = translate(P.engine, 'E', ectx, comments=comments, live=elive)
    ptrs |= em.ptrs
    open(os.path.join(outdir, 'engine.asm'), 'w').write('\n'.join(lines) + '\n')
    rep.append(f'engine: {len(P.engine.insns)} insns -> {len(lines)} lines')
    for n, ov in P.overlays.items():
        pf = f'{n.upper()}_'
        octx = octxs[n]
        octx.outside = lambda a: f'E{a:04X}' if a in eng_labels else None
        lines, em = translate(ov, pf, octx, comments=comments, live=olive[n])
        ptrs |= em.ptrs
        vt, stubs = [f'{pf}VEC'], []
        for a in slots:                                   # every slot is one 3-byte JMP
            if a in P.ovl_entries[n]:
                vt.append(f'\tjmp\t{pf}{a:04X}')
            else:
                vt.append(f'\tjmp\t{pf}T{a:04X}')
                stubs += [f'{pf}T{a:04X}\tjsr\tx_trap', f'\tfdb\t${a:04X}']
        open(os.path.join(outdir, f'ovl_{n}.asm'), 'w').write('\n'.join(vt + stubs + lines) + '\n')
        rep.append(f'{n}: {len(ov.insns)} insns -> {len(lines)} lines')
    open(os.path.join(outdir, 'hw_inst.asm'), 'w').write(     # accepted hardware idioms (kit/hw/lib.py)
        '\n'.join(hw_lib.render_all(getattr(spec, 'HW_IDIOMS', ()))) + '\n')
    open(os.path.join(outdir, 'ovt.inc'), 'w').write(
        '\n'.join(f'OVT_{a:04X}\tequ\tOVBASE+{3 * k}' for k, a in enumerate(slots)) + '\n')
    open(os.path.join(outdir, 'xiy.asm'), 'w').write('\n'.join(xiy_helpers(ptrs) + romdata_lines()) + '\n')
    names = {1: 'A', 2: 'X', 4: 'Y', 8: 'N', 16: 'Z', 32: 'C'}
    fmt = lambda m: ''.join(v for k, v in names.items() if m & k) or '-'
    for a, n in sorted(nat.items()):
        rep.append(f'native {n.sym:10s} ${a:04X}  uses {fmt(n.use):6s} may change {fmt(n.defs):6s} '
                   f'must produce {fmt(n.outputs)}')
    open(os.path.join(outdir, 'report.txt'), 'w').write('\n'.join(rep) + '\n')
    print('\n'.join(rep))
    io_check(outdir)
    return P


def io_check(outdir):
    """no silent I/O defaults: every handler the generated code uses must have an action in the hardware map
    (or spec HW_OVERRIDES).  Runs after the sources are written, so they can be inspected."""
    srcs = [open(os.path.join(outdir, f)).read().splitlines() for f in sorted(os.listdir(outdir))
            if f.endswith('.asm')]
    errors, conds = iocheck.check(iocheck.used_symbols(srcs), HW_OVERRIDES, IO_MEM)
    if errors:
        raise SystemExit('hardware accesses with no CoCo action (kit/hw/apple2.py):\n  ' + '\n  '.join(errors))
    return conds


def main(port_spec, argv=None):
    use(port_spec)
    ap = argparse.ArgumentParser()
    ap.add_argument('-o', default=os.path.join(spec.PORT.build, 'src'))
    a = ap.parse_args(argv)
    try:
        build(a.o)
    except XError as e:
        sys.exit(f'xc65: {e}')
