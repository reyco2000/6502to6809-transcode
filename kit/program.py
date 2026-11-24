"""Multi-image program: shared engine code + per-image overlay code. Game-independent.

A program is a set of memory images (e.g. the cold image and one image per stage).  A byte is *shared* when it
has the same value in every image (or is a listed SMC byte); shared code is translated once (the engine),
other code per image (overlays).  Transfers engine -> overlay go through a per-image vector; a target that does
not decode cleanly in an image becomes a trap there (the path is dead in that image, e.g. stage-specific calls).

split(images, entries, roots, native, native_ranges, code_ranges, smc_bytes) -> Program
  images   {name: 64K bytearray}
  entries  {name: [addr]}        image entry points (overlay roots)
  roots    {name: set(addr)}     extra instruction starts (profile), any region
"""
from .discover import discover


class Program:
    def __init__(self):
        self.engine = None          # Code
        self.overlays = {}          # name -> Code
        self.ovl_entries = {}       # name -> {addr: [(kind, engine site)]}  valid engine -> overlay entries
        self.traps = {}             # name -> {addr: [(kind, site)]}  engine -> overlay targets dead in that image
        self.shared = None          # predicate
        self.errors = []
        self.base = None            # image name used for engine bytes


def split(images, entries, roots, native=None, native_ranges=(), code_ranges=((0x0200, 0xBFFF),),
          smc_bytes=(), base=None, traps=(), smc_jmp=None, smc_ops=None):
    names = list(images)
    base = base or names[0]
    smc = set(smc_bytes) | set(smc_ops or {})
    for a in (smc_jmp or {}):                    # patched JMP operands are data, not code identity
        smc |= {a + 1, a + 2}
    ref = images[base]
    sh = bytearray(0x10000)
    for a in range(0xC000):
        if a in smc or all(images[n][a] == ref[a] for n in names):
            sh[a] = 1

    def shared(a):
        return a < 0xC000 and sh[a] == 1

    def shared_insn(a):         # the instruction starting at a (decoded in the base image) is all shared
        from .opcodes import decode
        if not shared(a):
            return False
        i = decode(ref, a)
        return i is None or all(shared(a + k) for k in range(i.len))

    P = Program()
    P.shared, P.base = shared, base
    # traps: [addr | (image, addr)]: a bare address applies to every image
    tr_all = {t for t in traps if not isinstance(t, tuple)}
    tr_img = {n: tr_all | {t[1] for t in traps if isinstance(t, tuple) and t[0] == n} for n in names}
    tr_eng = tr_all | {t[1] for t in traps if isinstance(t, tuple) and t[0] == base}
    eng_roots, eng_prof = set(), set()
    for n in names:
        eng_prof |= {a for a in roots.get(n, ()) if shared_insn(a)}
    for _ in range(20):
        eng = discover(ref, eng_roots, native, native_ranges, code_ranges, outside=lambda a: not shared_insn(a), prof=eng_prof,
                       traps=tr_eng, smc_jmp=smc_jmp, smc_ops=smc_ops)
        to_ovl = eng.outside                              # overlay addresses the engine transfers to
        new_eng = set()
        P.overlays, P.ovl_entries, P.traps = {}, {}, {}
        for n in names:
            mem = images[n]
            oroots = set(entries.get(n, ()))
            oprof = {a for a in roots.get(n, ()) if not shared_insn(a)}
            good, bad = {}, {}
            own = discover(mem, oroots, native, native_ranges, code_ranges, outside=shared_insn, prof=oprof, traps=tr_img[n], smc_jmp=smc_jmp, smc_ops=smc_ops)
            starts = {}
            for b, i in own.insns.items():
                for k in range(i.len):
                    starts[b + k] = b
            for a, refs in to_ovl.items():
                t = discover(mem, [a], native, native_ranges, code_ranges, outside=shared_insn, traps=tr_img[n], smc_jmp=smc_jmp, smc_ops=smc_ops)
                clash = any(starts.get(b + k, b) != b for b, i in t.insns.items() for k in range(i.len))
                (bad if t.errors or clash else good)[a] = refs
            ov = discover(mem, oroots | set(good), native, native_ranges, code_ranges, outside=shared_insn, prof=oprof,
                          traps=tr_img[n], smc_jmp=smc_jmp, smc_ops=smc_ops)
            P.overlays[n], P.ovl_entries[n], P.traps[n] = ov, good, bad
            new_eng |= set(ov.outside)
        if new_eng <= eng_roots:
            break
        eng_roots |= new_eng
    P.engine = eng
    for a, ins in eng.insns.items():
        if not all(shared(a + k) for k in range(ins.len)):
            P.errors.append(f'engine ${a:04X}: instruction bytes not shared')
    P.errors += [f'engine {e}' for e in eng.errors]
    for n, ov in P.overlays.items():
        P.errors += [f'{n} {e}' for e in ov.errors]
    # dead-path hints: the first never-executed instruction leading to each error stop
    from .discover import suggest_traps
    executed = set().union(*[set(r) for r in roots.values()]) if roots else set()
    P.trap_hints = {}
    for c in [eng] + list(P.overlays.values()):
        for t, bads in suggest_traps(c, executed).items():
            P.trap_hints.setdefault(t, set()).update(bads)
    return P
