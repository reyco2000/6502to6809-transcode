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

# ---------------------------------------------------------------------------------------------- Apple II surface
EA_BITS = {'zp': 1, 'stack': 2, 'ram0': 4, 'hires': 8, 'ram6': 16, 'io': 32, 'rom': 64}   # kit/host/profile.py

SOFTSW = [  # (lo, hi, name, category, suggested native from docs/NATIVES.md)
    (0xC019, 0xC019, 'RDVBL (IIe vertical blank)', 'vbl', 'nt_vbl (GIME field sync)'),
    (0xC000, 0xC00F, 'KBD (keyboard data / IIe switches on write)', 'keyboard', 'kbd_read (rt/natives/apple2/kbd.asm)'),
    (0xC010, 0xC01F, 'KBDSTRB / IIe status reads', 'keyboard', 'kbd_strobe'),
    (0xC020, 0xC02F, 'cassette out', 'sound', 'ignore'),
    (0xC030, 0xC03F, 'SPKR (speaker toggle)', 'sound', 'snd_* DAC player: replace the whole cycle-timed loop'),
    (0xC050, 0xC057, 'video mode / page switches (TXTCLR..HIRES)', 'video', 'vm_page / vconv (rt vid converter)'),
    (0xC058, 0xC05F, 'annunciators / IIe DHR switch', 'video', 'check: AN3 = double hi-res'),
    (0xC060, 0xC063, 'push buttons / cassette in', 'joystick', 'joy_buttons'),
    (0xC064, 0xC067, 'paddle timers', 'joystick', 'nj_paddle: replace the count loop'),
    (0xC070, 0xC070, 'PTRIG (paddle trigger)', 'joystick', 'nj_paddle'),
    (0xC080, 0xC08F, 'language card banking', 'memory', 'map in the GIME MMU, or a native bank switch'),
    (0xC090, 0xC0FF, 'slot I/O (Disk II = $C0E0-$C0EF in slot 6)', 'disk', 'native stage loader (DSKCON / dstream)'),
    (0xC100, 0xCFFF, 'slot ROM / expansion ROM', 'rom', 'native'),
]
ROM_KNOWN = {  # monitor / firmware entry points a game commonly calls
    0xFCA8: ('WAIT', 'timing', 'nt_wait'), 0xFC58: ('HOME', 'text', 'native text clear'),
    0xFDED: ('COUT', 'text', 'native text out'), 0xFDF0: ('COUT1', 'text', 'native text out'),
    0xFD0C: ('RDKEY', 'keyboard', 'kbd_read loop'), 0xFD1B: ('KEYIN', 'keyboard', 'kbd_read loop'),
    0xFB1E: ('PREAD', 'joystick', 'nj_paddle'), 0xFBDD: ('BELL1', 'sound', 'snd beep'),
    0xFF3A: ('BELL', 'sound', 'snd beep'), 0xFE89: ('SETKBD', 'init', 'drop'), 0xFE93: ('SETVID', 'init', 'drop'),
    0xFB2F: ('INIT', 'init', 'drop'), 0xF3E2: ('HGR', 'video', 'native clear'), 0xF3D8: ('HGR2', 'video', 'native clear'),
    0xC600: ('Disk II boot', 'disk', 'native loader'), 0xC65C: ('Disk II read sector', 'disk', 'native loader'),
}

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


def softsw(a, mode='abs'):
    if mode in ('absx', 'absy') and 0xC080 <= a <= 0xC08F:      # $C08x,X with X = slot*16: slot I/O (Disk II)
        return f'slot I/O ${a:04X}+slot*16 (Disk II: $C08C = data latch)', 'disk', 'native stage loader (DSKCON / dstream)'
    for lo, hi, name, cat, nat in SOFTSW:
        if lo <= a <= hi:
            return name, cat, nat
    return None


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
            if m & EA_BITS['io'] or (val is not None and mode in ('abs', 'absx', 'absy') and 0xC000 <= val <= 0xC0FF):
                io_sites.append((a, im.id, mn, mode, val, n, softsw(val, mode) if val is not None else None))
    smc = []
    for im in images:
        for k, v in im.smc.items():
            smc.append((_h(k), im.id, v.get('kind'), len(v.get('tuples', [])), v.get('writers', [])))
    rom = collections.Counter()
    for e in cov.get('rom_entries', []):
        rom[_h(e[3])] += e[4]
    return dict(total=total, rcyc=rcyc, rcls=rcls, io=io_sites, hires=hires_sites, smc=sorted(smc), rom=rom,
                pages=sorted(pages), routines=rts, top=top)


def report(cov, s, out=sys.stdout):
    p = lambda *a: print(*a, file=out)
    summ = cov.get('summary', {})
    p(f"# Survey  ({summ.get('runs', '?')} run(s), {summ.get('frames', '?')} frames, "
      f"{summ.get('instr', 0):,} instructions, {summ.get('images', '?')} code images)")
    p(f"est. cycles {s['total']:,};  executed pages: {_ranges(s['pages'])}")
    if summ.get('overlay_pages'):
        p(f"pages with several code images (overlays / loaded code): {' '.join(summ['overlay_pages'])}")
    p(f"loaders (bulk writers that load code): {' '.join(cov.get('loaders', []))}")

    p('\n## 1. Hardware surface: every I/O access site  -> each one needs a native routine (DECIDE.md rule H)')
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
        k = ROM_KNOWN.get(a)
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
        p('  $FCA8 WAIT is used: real-time delays; nt_wait scales them to CoCo cycles')


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
        if r is not None and r not in seen and sw:
            seen.add(r)
            L.append(f"    0x{r:04X}: 'n_{sw[1]}_{r:04x}',   # {sw[0]} at ${a:04X}; suggested: {sw[2]}")
    tot_h = sum(s['hires'].values()) or 1
    for r, n in s['hires'].most_common(12):
        if r is not None and r not in seen and n / tot_h > 0.02:
            seen.add(r)
            L.append(f"    0x{r:04X}: 'nh_{r:04x}',   # {100 * n / tot_h:.0f}% of hi-res traffic")
    for a in sorted(s['rom']):
        k = ROM_KNOWN.get(a)
        L.append(f"    0x{a:04X}: '{(k[2] if k else 'n_rom_%04x' % a).split()[0]}',   # ROM {k[0] if k else '?'}")
    L += ['}', '', '# (lo, hi) of every replaced routine body: fill in from the listing', 'NATIVE_RANGES = []', '',
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
    a = ap.parse_args(argv)
    path = a.coverage
    if not path:
        from .portcfg import load as load_port
        path = load_port(a.port).coverage
    cov, images = load(path)
    s = survey(cov, images, a.top)
    report(cov, s)
    if a.spec_out:
        open(a.spec_out, 'w').write(draft_spec(cov, s))
        print(f'\ndraft spec -> {a.spec_out}')


if __name__ == '__main__':
    main()
