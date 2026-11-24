"""Differential test of a port's translated code against the 6502 reference (game-independent).

  python3 -m ports.<game>.test_engine [-n CASES] [-s SEED] [--set engine|IMAGE] [--entry HHHH] [-v]

Each case: a routine entry of the engine, one of the snapshot images (real game state), random A/X/Y/flags,
sometimes random zero-page noise.  Both sides run until the routine returns or leaves the engine (native,
overlay); see kit/difftest.py for what is compared.
"""
import argparse
import random
import sys

from .difftest import Harness, reference, compare
from .xc65 import translate, FLAGS
from . import gen

spec = None


def setup(which='engine'):
    imgs = spec.load_images()
    P = gen.program(imgs)
    nat = gen.natives(imgs[getattr(spec, 'BASE', 'cold')])
    elive, olive, ectx, octxs = gen.cross_live(P, nat)
    slots = sorted(set().union(*[set(P.ovl_entries[n]) | set(P.traps[n]) for n in P.overlays]))
    ovt = {a: f'OVT_{a:04X}' for a in slots}
    from kit.xc65 import xiy_helpers
    stubs = {n.sym: a for a, n in nat.items()}
    stubs['x_trap'] = 'trap'
    if which == 'engine':
        ectx.outside = lambda a: ovt.get(a)
        lines, em = translate(P.engine, 'E', ectx, comments=False, live=elive)
        stubs.update({s: a for a, s in ovt.items()})
        code, L, pfx = P.engine, elive, 'E'
    else:
        eng = set(P.engine.insns)
        octx = octxs[which]
        octx.outside = lambda a: f'E{a:04X}' if a in eng else None
        pfx = which.upper() + '_'
        lines, em = translate(P.overlays[which], pfx, octx, comments=False, live=olive[which])
        stubs.update({f'E{a:04X}': a for a in eng})
        code, L = P.overlays[which], olive[which]
    latches = [f'{v}\tfcb\t0' for v in sorted(set(gen.IO_MEM.values()))]   # I/O reads give 0
    H = Harness(lines, stubs, extra_lines=xiy_helpers(em.ptrs) + latches + gen.romdata_lines())
    return imgs, code, L, H, pfx


def rts_mask(L, e):
    m = L.ctx.ext_ret.get(e, 0)
    for j in L.callers.get(e, ()):
        m |= L.lb(L.code.insns[j].next)
    if e in L.ctx.roots:
        m = 0x3F
    return m


def main(port_spec, argv=None):
    global spec
    spec = port_spec
    gen.use(spec)
    ap = argparse.ArgumentParser()
    ap.add_argument('-n', type=int, default=300)
    ap.add_argument('-s', type=int, default=1)
    ap.add_argument('--entry', default='')
    ap.add_argument('-v', action='store_true')
    ap.add_argument('--set', default='engine', help='engine or an image name (spec.IMAGES)')
    a = ap.parse_args(argv)
    imgs, code, L, H, pfx = setup(a.set)
    canon = {ad: H.stubs_by_sym[sym] for ad, sym in spec.NATIVE.items() if sym in H.stubs_by_sym}
    print(f'{a.set} image {H.size} bytes', flush=True)
    rnd = random.Random(a.s)
    entries = sorted(set(L.callers) | set(L.ctx.ext_ret))
    if a.entry:
        entries = [int(x, 16) for x in a.entry.split(',')]
    inside = set(code.insns)
    names = list(imgs) if a.set == 'engine' else [a.set]
    if not entries:
        entries = sorted(L.ctx.roots)
    stats = {'same': 0, 'diff': 0, 'timeout': 0}
    zpw = spec.profile_flags(2) if hasattr(spec, 'profile_flags') else set()   # wrap-safe zp,X / zp,Y sites
    fails = {}
    cyc_ref = cyc_got = 0
    for k in range(a.n):
        e = rnd.choice(entries)
        img = rnd.choice(names)
        ram = bytearray(imgs[img][:0xC000])
        if rnd.random() < 0.3:
            for _ in range(rnd.randint(1, 8)):
                ram[rnd.randint(0, 0xFF)] = rnd.randint(0, 255)
        ra, rx, ry, rp = (rnd.randint(0, 255) for _ in range(4))
        ref = reference(ram, e, ra, rx, ry, rp, inside, gen.ROMCONST, wrapsafe=zpw)
        if ref['stop'] in canon:                  # several 6502 entries of one native: one stub
            ref['stop'] = canon[ref['stop']]
        if ref['stop'] in ('timeout', 'illegal', 'wild', 'zpwrap'):
            stats[ref['stop']] = stats.get(ref['stop'], 0) + 1
            continue
        got = H.run(ram, f'{pfx}{e:04X}', ra, rx, ry, rp)
        if ref['stop'] == 'ret':
            live = rts_mask(L, e)
        else:
            live = L.lb(ref['stop'])
        d = compare(ref, got, live)
        if d:
            stats['diff'] += 1
            fails.setdefault(e, []).append((img, ra, rx, ry, rp, d))
            if a.v:
                print(f'DIFF stop={ref["stop"] if isinstance(ref["stop"], str) else hex(ref["stop"])} ${e:04X} {img} A={ra:02X} X={rx:02X} Y={ry:02X} P={rp:02X}: {d}', flush=True)
        else:
            stats['same'] += 1
            cyc_ref += ref['cycles']
            cyc_got += got['cycles']
    print(stats)
    if cyc_ref:
        print(f'cycles: 6502 {cyc_ref}, 6809 {cyc_got} -> {cyc_got / cyc_ref:.2f} CoCo cycles per Apple cycle '
              f'(CoCo 1.79 MHz / Apple 1.02 MHz = 1.75 means real speed)')
    for e, fl in sorted(fails.items()):
        img, ra, rx, ry, rp, d = fl[0]
        print(f'  ${e:04X}: {len(fl)} fails, e.g. {img} A={ra:02X} X={rx:02X} Y={ry:02X} P={rp:02X}: {d}')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
