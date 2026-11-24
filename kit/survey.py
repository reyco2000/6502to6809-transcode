"""Survey: what to transcode and what to replace natively (Apple II source).

    python3 -m kit.survey PORT_DIR [--coverage COV.json] [--top 25] [--spec-out draft_spec.py]

Input: the host-Apple coverage profile of the game (kit/host/profile.py --coverage, from one or more play-through
scenarios).  Output: a report on stdout (hardware surface, hi-res sites, hot routines, SMC, timing code, ROM calls,
loaders/overlays) and, with --spec-out, a draft spec.py the port starts from.  See docs/DECIDE.md for the rules.

The cycle numbers are estimates (base cycles per opcode, no page-cross/branch-taken extras): good for ranking.
"""
import argparse
import collections
import json
import os
import sys

from .opcodes import OPS, MODE_LEN
from .xc65 import REJECT
from .hw import apple2 as hw
from .hw import Runtime, Latch, Ignore, Trap, IdiomOnly

# ---------------------------------------------------------------------------------------------- Apple II surface
EA_BITS = {'zp': 1, 'stack': 2, 'ram0': 4, 'hires': 8, 'ram6': 16, 'io': 32, 'rom': 64}   # kit/host/profile.py

# the hardware surface (soft switches, firmware entry points) comes from the hardware map: kit/hw/apple2.py
STORES = {'STA', 'STX', 'STY'}
RMW_IO = {'ASL', 'LSR', 'ROL', 'ROR', 'INC', 'DEC'}


def suggestion(action):
    """what the hardware map says to do with an access (survey wording)"""
    if isinstance(action, (Runtime, Latch)):
        return 'runtime'
    if isinstance(action, Ignore):
        return 'ignored'
    if isinstance(action, Trap):
        return f'TRAP: {action.reason}'
    if isinstance(action, IdiomOnly):
        return f"idiom: {', '.join(action.kinds)} (see section 7)"
    return str(action)


# base cycles (NMOS 6502), by mode, with the exceptions below
MODE_CYC = {'imp': 2, 'acc': 2, 'imm': 2, 'zp': 3, 'zpx': 4, 'zpy': 4, 'abs': 4, 'absx': 4, 'absy': 4,
            'ind': 5, 'indx': 6, 'indy': 5, 'rel': 2}
RMW = {'ASL', 'LSR', 'ROL', 'ROR', 'INC', 'DEC'}
SPECIAL = {'JSR': 6, 'RTS': 6, 'RTI': 6, 'BRK': 7, 'PHA': 3, 'PHP': 3, 'PLA': 4, 'PLP': 4, 'JMP': 3}


def cycles(op):
    mn, mode = OPS.get(op, ('???', 'imp'))
    if mn in SPECIAL:
        return 5 if (mn == 'JMP' and mode == 'ind') else SPECIAL[mn]
    c = MODE_CYC[mode]
    if mn in RMW and mode not in ('acc',):
        c += 2 if mode in ('zp', 'abs') else 3
    if mn == 'STA' and mode in ('absx', 'absy', 'indy'):
        c += 1
    return c


def softsw(a, mode='abs', mn=None):
    """-> (name, category, suggestion) for an access to a soft switch / I/O address, or None outside the map.
    mode: the 6502 addressing mode (abs,X / abs,Y = indexed: $C08x,X is slot I/O); mn: the mnemonic (stores and
    read-modify-writes use the write side of the map)"""
    idx = 'x' if mode in ('absx', 'absy') else ''
    side = 'wr' if mn in STORES or mn in RMW_IO else 'rd'
    e = hw.lookup(a, side + idx)
    if e is None:
        return None
    return e.name, e.cat, suggestion(e.action)


def rom_entry(a):
    """-> (name, category, suggestion) for a firmware entry point, or None"""
    e = hw.rom(a)
    return (e.name, e.cat, suggestion(e.action)) if e else None


# ---------------------------------------------------------------------------------------------- coverage helpers
def _h(s):
    return int(s, 16)


def _maybe_json(v):
    return json.loads(v.replace("'", '"')) if isinstance(v, str) else v


class Image:
    def __init__(self, d):
        self.id = d['id']
        self.base = _h(d['page']) << 8
        self.bytes = bytes.fromhex(d['bytes'])
        self.instr = {_h(k): v for k, v in _maybe_json(d['instr']).items()}
        self.ea = {_h(k): v for k, v in d.get('ea', {}).items()}
        self.smc = _maybe_json(d.get('smc') or {})
        self.entries = _maybe_json(d.get('entries') or [])

    def byte(self, a):
        o = a - self.base
        return self.bytes[o] if 0 <= o < len(self.bytes) else None

    def decode(self, a):
        op = self.byte(a)
        if op is None or op not in OPS:
            return None
        mn, mode = OPS[op]
        n = MODE_LEN[mode]
        ops = [self.byte(a + i) for i in range(1, n)]
        if None in ops:
            return mn, mode, None, op
        val = ops[0] | (ops[1] << 8) if n == 3 else (ops[0] if n == 2 else None)
        return mn, mode, val, op


def load(path):
    cov = json.load(open(path))
    return cov, [Image(d) for d in cov['images']]


def routines(images):
    """routine entry points: JSR targets (any image) -> {addr: callers}"""
    r = collections.Counter()
    for im in images:
        for e in im.entries:
            if e[1] == 'jsr':
                r[_h(e[0])] += e[4] if len(e) > 4 else 1
    return r


def owner(entries_sorted, a, reach=0x300):
    """the nearest routine entry at or below a, within reach bytes (heuristic: routines are contiguous)"""
    lo, hi, best = 0, len(entries_sorted) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if entries_sorted[mid] <= a:
            best, lo = entries_sorted[mid], mid + 1
        else:
            hi = mid - 1
    return best if best is not None and a - best <= reach else None


# ---------------------------------------------------------------------------------------------- the survey
def survey(cov, images, top=25):
    rts = routines(images)
    ents = sorted(rts)
    rcyc = collections.Counter()           # routine -> estimated cycles
    rcls = collections.defaultdict(collections.Counter)   # routine -> EA class -> executions
    io_sites = []                          # (pc, image, mnemonic, address, count, softswitch)
    hires_sites = collections.Counter()    # routine -> executions of hi-res memory instructions
    rejected = []                          # (pc, image, mnemonic, routine): opcodes xc65 does not translate
    total = 0
    pages = set()
    for im in images:
        for a, n in im.instr.items():
            d = im.decode(a)
            if d is None:
                continue
            mn, mode, val, op = d
            c = cycles(op) * n
            total += c
            pages.add(a >> 8)
            r = owner(ents, a)
            rcyc[r] += c
            m = im.ea.get(a, 0)
            for nm, bit in EA_BITS.items():
                if m & bit:
                    rcls[r][nm] += n
            if m & EA_BITS['hires']:
                hires_sites[r] += n
            if mn in REJECT or (mn == 'JMP' and mode == 'ind'):
                rejected.append((a, im.id, 'JMP (ind)' if mn == 'JMP' else mn, r))
            if m & EA_BITS['io'] or (val is not None and mode in ('abs', 'absx', 'absy') and 0xC000 <= val <= 0xC0FF):
                io_sites.append((a, im.id, mn, mode, val, n, softsw(val, mode, mn) if val is not None else None))
    smc = []
    for im in images:
        for k, v in im.smc.items():
            smc.append((_h(k), im.id, v.get('kind'), len(v.get('tuples', [])), v.get('writers', [])))
    rom = collections.Counter()
    for e in cov.get('rom_entries', []):
        rom[_h(e[3])] += e[4]
    matches, near = idioms(images)
    return dict(total=total, rcyc=rcyc, rcls=rcls, io=io_sites, hires=hires_sites, smc=sorted(smc), rom=rom,
                pages=sorted(pages), routines=rts, top=top, matches=matches, near=near, rejected=rejected)


def idioms(images):
    """hardware idiom proposals and near-misses (kit/hw/match.py) over every image's executed code
    -> ([(Match, [image ids])], [(NearMiss, [image ids])])"""
    from .discover import discover
    from .hw.match import find
    props, nears = {}, {}
    for im in images:
        mem = bytearray(0x10000)
        mem[im.base:im.base + len(im.bytes)] = im.bytes
        roots = sorted(a for a in im.instr if a < 0xC000)
        if not roots:
            continue
        c = discover(mem, roots, {}, code_ranges=((im.base, min(0xBFFF, im.base + len(im.bytes) - 1)),))
        ms, ns = find(mem, c, set(im.instr))
        for m in ms:
            props.setdefault(repr(m.idiom), (m, []))[1].append(im.id)
        for n in ns:
            nears.setdefault((n.kind, n.lo, n.hi, n.reason), (n, []))[1].append(im.id)
    return sorted(props.values(), key=lambda v: v[0].idiom.entry), sorted(nears.values(), key=lambda v: v[0].lo)


def verify_idioms(s, images, n):
    """attach difftest evidence (kit/hw/verify.py) to every proposal, on the first image it was found in"""
    from .hw.verify import verify
    from .paths import SIM
    if not os.path.exists(SIM):
        raise SystemExit(f'--verify needs the simulator: make -C {os.path.dirname(SIM)}')
    byid = {im.id: im for im in images}
    for m, ids in s.get('matches', []):
        im = byid[ids[0]]
        mem = bytearray(0x10000)
        mem[im.base:im.base + len(im.bytes)] = im.bytes
        m.evidence.append(str(verify(m, mem, n)))


def idiom_line(m, ids, comment=True):
    """one draft-spec line for a proposal"""
    i = m.idiom
    ex = ', '.join(f'0x{e:04X}' for e in i.exits)
    s = (f"Idiom({i.kind!r}, entry=0x{i.entry:04X}, end=0x{i.end:04X}, exits=({ex}{',' if len(i.exits) == 1 else ''}), "
         f"params={i.params!r}),")
    note = '; '.join(m.evidence) + f'; images {" ".join(map(str, ids))}; check: python3 -m kit.listing PORT ' \
        f'{i.entry:04X}-{i.end:04X}'
    return f'#   {s}   # {note}' if comment else s


def report(cov, s, out=None):
    out = out or sys.stdout
    p = lambda *a: print(*a, file=out)
    summ = cov.get('summary', {})
    p(f"# Survey  ({summ.get('runs', '?')} run(s), {summ.get('frames', '?')} frames, "
      f"{summ.get('instr', 0):,} instructions, {summ.get('images', '?')} code images)")
    p(f"est. cycles {s['total']:,};  executed pages: {_ranges(s['pages'])}")
    if summ.get('overlay_pages'):
        p(f"pages with several code images (overlays / loaded code): {' '.join(summ['overlay_pages'])}")
    p(f"loaders (bulk writers that load code): {' '.join(cov.get('loaders', []))}")

    p('\n## 1. Hardware surface: every I/O access site  -> its action in the hardware map (kit/hw/apple2.py; DECIDE.md rule H)')
    bycat = collections.defaultdict(list)
    for a, imid, mn, mode, val, n, sw in s['io']:
        bycat[sw[1] if sw else 'unknown'].append((a, imid, mn, mode, val, n, sw))
    for cat in sorted(bycat):
        p(f'### {cat}')
        for a, imid, mn, mode, val, n, sw in sorted(bycat[cat]):
            tgt = f'${val:04X}' if val is not None else '?'
            idx = {'absx': ',X', 'absy': ',Y'}.get(mode, '')
            r = owner(sorted(s['routines']), a)
            p(f"  ${a:04X} img{imid:<3} {mn} {tgt}{idx:<3} x{n:<9} in {_r(r)}  {sw[0] if sw else ''}"
              f"{'  -> ' + sw[2] if sw else ''}")

    p('\n## 2. Hi-res memory traffic by routine  -> native video routines (rule V)')
    tot_h = sum(s['hires'].values()) or 1
    for r, n in s['hires'].most_common(s['top']):
        p(f"  {_r(r)}  {n:>11,} hi-res accesses  {100 * n / tot_h:5.1f}%  est {s['rcyc'][r]:>12,} cyc")

    p('\n## 3. Hot routines (estimated cycles)  -> native if hot AND pixel/hardware bound (rule P)')
    for r, c in s['rcyc'].most_common(s['top']):
        cls = s['rcls'][r]
        tag = ' HIRES' if cls.get('hires') else ''
        tag += ' IO' if cls.get('io') else ''
        p(f"  {_r(r)}  {100 * c / (s['total'] or 1):5.1f}%  {c:>13,} cyc  calls {s['routines'].get(r, 0):>8,}{tag}")

    p('\n## 4. Self-modifying code  -> SMC_BYTES / SMC_IMM, or inside a native (rule S)')
    for a, imid, kind, nt, wr in s['smc']:
        p(f"  ${a:04X} img{imid:<3} {kind:<8} {nt} variants  writers {' '.join(wr)}")

    p('\n## 5. ROM / firmware calls  -> natives (rule R)')
    for a, n in sorted(s['rom'].items()):
        k = rom_entry(a)
        p(f"  ${a:04X} x{n:<8} {k[0] + ' (' + k[1] + ') -> ' + k[2] if k else 'unknown: look it up (a2-help skill)'}")

    p('\n## 6. Timing code to check by hand (rule T)')
    timed = collections.defaultdict(set)
    for a, imid, mn, mode, val, n, sw in s['io']:
        if sw and (sw[1] in ('sound', 'vbl') or (sw[1] == 'joystick' and not 0xC061 <= val <= 0xC063)):
            timed[(owner(sorted(s['routines']), a), sw[1])].add(a)
    for (r, cat), sites in sorted(timed.items(), key=lambda k: (k[0][0] or 0, k[0][1])):
        p(f"  {_r(r)} {cat:<8} sites {' '.join(f'${x:04X}' for x in sorted(sites))}: cycle-timed loop; "
          f"replace the whole loop natively, keep its exits")
    if 0xFCA8 in s['rom']:
        p('  $FCA8 WAIT is used: real-time delays; the rom_wait idiom scales them to CoCo cycles')

    p('\n## 7. Hardware idioms: proposed / near-miss  -> spec HW_IDIOMS after checking each one (kit/hw/lib.py)')
    for m, ids in s.get('matches', []):
        i = m.idiom
        p(f"  proposed  {i.kind:<13} ${i.entry:04X}-${i.end:04X}  img {' '.join(map(str, ids))}  "
          f"{'; '.join(m.evidence)}")
    for n, ids in s.get('near', []):
        p(f"  near-miss {n.kind:<13} ${n.lo:04X}-${n.hi:04X}  img {' '.join(map(str, ids))}  {n.reason}")
    if not s.get('matches') and not s.get('near'):
        p('  (none)')

    p('\n## 8. Least likely to transcode (this game)  -> SKILL.md / docs/DECIDE.md; everything else transcodes')
    last, group = None, []

    def flush():
        if group:
            more = f'  (+{len(group) - 1} more up to ${group[-1][0]:04X})' if len(group) > 1 else ''
            p(f'  {_r(group[0][0])}  {group[0][1]}{more}')
    for b, r, why in rank(s, s.get('matches', []), s.get('near', []), cov):
        if group and (b != last or why != group[0][1] or r is None or r - group[-1][0] > 0x100):
            flush()
            group = []
        if b != last:
            p(f'### {b}. {BUCKETS[b]}')
            last = b
        group.append((r, why))
    flush()


BUCKETS = {
    1: 'loaders, RWTS, copy protection: never transcoded (snapshots + nt_load)',
    2: 'cycle-timed sound beyond a single voice: hand-written native',
    3: 'hardware the map traps on: extend the kit, or HW_OVERRIDES when it does not matter',
    4: 'code the transcoder rejects (decimal mode, PHP/PLP, TSX/TXS, RTI, BRK, JMP (ind), opcode SMC): native or extend xc65',
    5: 'hot hi-res code (blitters, fills, scrolls): transcodes correctly but slowly; native for speed',
    6: 'standard hardware idioms: library routines (section 7)',
}


def rank(s, matches, near, cov):
    """what is least likely to transcode in this game: [(bucket 1-6, routine, reason)], one line per routine
    (its lowest bucket), sorted by bucket then routine.  Everything not listed (bucket 7) transcodes."""
    ents = sorted(s['routines'])
    own = lambda a: owner(ents, a) if owner(ents, a) is not None else a
    out = {}

    def add(b, r, why):
        if r not in out or out[r][0] > b:
            out[r] = (b, why)
    for x in cov.get('loaders', []):
        add(1, _h(x) if isinstance(x, str) else x, 'loader (bulk writer of code)')
    near = [n[0] if isinstance(n, tuple) else n for n in near]
    matches = [m[0] if isinstance(m, tuple) else m for m in matches]
    for n in near:
        if n.kind == 'tone':
            add(2, own(n.lo), n.reason)
        elif n.kind != 'delay':
            add(6, own(n.lo), f'{n.kind} near-miss: {n.reason}')
    for a, imid, mn, mode, val, cnt, sw in s['io']:
        if sw and sw[2].startswith('TRAP'):
            disk = sw[1] == 'disk'                       # Disk II access is loader code
            add(1 if disk else 3, own(a), 'Disk II access (loader)' if disk else f'{mn} ${val:04X}: {sw[2][6:]}')
    for a, n in s['rom'].items():
        k = rom_entry(a)
        if k and k[2].startswith('TRAP'):
            disk = k[1] == 'disk'
            add(1 if disk else 3, a, f'ROM {k[0]} (loader)' if disk else f'ROM {k[0]}: {k[2][6:]}')
    for a, imid, mn, r in s.get('rejected', []):
        add(4, r if r is not None else a, f'{mn} at ${a:04X}')
    for a, imid, kind, nt, wr in s['smc']:
        if kind == 'opcode':
            add(4, own(a), f'opcode SMC at ${a:04X}')
    tot_h = sum(s['hires'].values()) or 1
    for r, n in s['hires'].most_common(12):
        if r is not None and n / tot_h > 0.02:
            add(5, r, f'{100 * n / tot_h:.0f}% of hi-res traffic')
    for m in matches:
        add(6, own(m.idiom.entry) if m.idiom.entry < 0xC000 else m.idiom.entry, f'{m.idiom.kind} (proposed)')
    return sorted(((b, r, why) for r, (b, why) in out.items()), key=lambda v: (v[0], v[1]))


def _r(r):
    return f'${r:04X}' if r is not None else '  ?  '


def _ranges(pages):
    out, i = [], 0
    while i < len(pages):
        j = i
        while j + 1 < len(pages) and pages[j + 1] == pages[j] + 1:
            j += 1
        out.append(f'${pages[i]:02X}' if i == j else f'${pages[i]:02X}-${pages[j]:02X}')
        i = j + 1
    return ' '.join(out)


def clip_ranges(ranges, holes=((0x0000, 0x01FF), (0x2000, 0x5FFF), (0xC000, 0xFFFF))):
    """cut zero page/stack, the hi-res pages and I/O+ROM out of (lo, hi) ranges (a run that starts in the
    stack page, like $01-$0E, keeps its $0200-$0EFF part)"""
    out = [tuple(r) for r in ranges]
    for hlo, hhi in holes:
        nxt = []
        for lo, hi in out:
            if hi < hlo or lo > hhi:
                nxt.append((lo, hi))
                continue
            if lo < hlo:
                nxt.append((lo, hlo - 1))
            if hi > hhi:
                nxt.append((hhi + 1, hi))
        out = nxt
    return out


def draft_spec(cov, s):
    """a starting spec.py (to be edited by hand): the candidates, not decisions"""
    code = []
    for pg in s['pages']:
        if code and code[-1][1] == (pg << 8) - 1:
            code[-1][1] = (pg << 8) | 0xFF
        else:
            code.append([pg << 8, (pg << 8) | 0xFF])
    L = ['"""DRAFT spec made by kit/survey.py: every entry is a candidate; check each against the listing."""', '',
         '# replaced code: entry -> native symbol (rt/natives/apple2 or the port\'s src/)', 'NATIVE = {']
    seen = set()
    for a, imid, mn, mode, val, n, sw in sorted(s['io']):
        r = owner(sorted(s['routines']), a)
        if any(m.idiom.entry <= a <= m.idiom.end for m, _ in s.get('matches', [])):
            continue                                 # proposed as a hardware idiom below
        if r is not None and r not in seen and sw and sw[2].startswith(('TRAP', 'idiom')):
            seen.add(r)
            L.append(f"    0x{r:04X}: 'n_{sw[1]}_{r:04x}',   # {sw[0]} at ${a:04X}; suggested: {sw[2]}")
    for n, ids in s.get('near', []):                  # loops that look like an idiom but need a hand native
        r = owner(sorted(s['routines']), n.lo)
        r = n.lo if r is None else r
        if r not in seen:
            seen.add(r)
            L.append(f"    0x{r:04X}: 'n_{n.kind}_{r:04x}',   # near-miss ${n.lo:04X}-${n.hi:04X}: {n.reason}")
    tot_h = sum(s['hires'].values()) or 1
    for r, n in s['hires'].most_common(12):
        if r is not None and r not in seen and n / tot_h > 0.02:
            seen.add(r)
            L.append(f"    0x{r:04X}: 'nh_{r:04x}',   # {100 * n / tot_h:.0f}% of hi-res traffic")
    for a in sorted(s['rom']):
        k = rom_entry(a)
        sym = ('n_' + k[0].lower().replace(' ', '_')) if k else 'n_rom_%04x' % a
        L.append(f"    0x{a:04X}: '{sym}',   # ROM {k[0] + ': ' + k[2] if k else '?'}")
    L += ['}', '', '# hardware idioms the survey proposes (section 7): check each against the listing, then uncomment',
          '# (an exit into overlay code needs image=\'<name>\').  from kit.hw import Idiom', 'HW_IDIOMS = [']
    L += [idiom_line(m, ids) for m, ids in s.get('matches', [])]
    L += [']', '', '# (lo, hi) of every replaced routine body: fill in from the listing', 'NATIVE_RANGES = []', '',
          '# translated addresses a native continues into', 'NATIVE_EXITS = []', '',
          '# opcode SMC bytes handled by natives / operand reads', 'SMC_BYTES = [' +
          ', '.join(f'0x{a:04X}' for a, imid, k, nt, wr in s['smc'] if k == 'opcode') + ']',
          '# operand SMC sites: the template reads the operand from memory', 'SMC_IMM = [' +
          ', '.join(f'0x{a:04X}' for a, imid, k, nt, wr in s['smc'] if k == 'operand') + ']', '',
          '# where translated code may live (executed pages; drop hi-res/zero/stack pages)',
          'CODE_RANGES = [' + ', '.join(f'(0x{lo:04X}, 0x{hi:04X})' for lo, hi in clip_ranges(code)) + ']', '']
    return '\n'.join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('port', nargs='?', help='port directory holding port.toml')
    ap.add_argument('--coverage', help='coverage JSON (default: port.toml coverage)')
    ap.add_argument('--top', type=int, default=25)
    ap.add_argument('--spec-out', help='write a draft spec.py here')
    ap.add_argument('--verify', nargs='?', type=int, const=300, metavar='N',
                    help='test every proposed hardware idiom against its 6502 code on N random cases (needs sim/cc3sim)')
    a = ap.parse_args(argv)
    path = a.coverage
    if not path:
        from .portcfg import load as load_port
        path = load_port(a.port).coverage
    cov, images = load(path)
    s = survey(cov, images, a.top)
    if a.verify:
        verify_idioms(s, images, a.verify)
    report(cov, s)
    if a.spec_out:
        open(a.spec_out, 'w').write(draft_spec(cov, s))
        print(f'\ndraft spec -> {a.spec_out}')


if __name__ == '__main__':
    main()
