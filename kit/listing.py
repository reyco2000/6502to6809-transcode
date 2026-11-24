"""Coverage-guided listing of a snapshot: the instructions that really ran, with execution counts, entry kinds and
SMC marks (workflow step 5: confirm the survey's candidates).

    python3 -m kit.listing PORT [--image NAME] RANGE...        RANGE = 0AA5-0B40 or 0AA5 (one routine, to its RTS)
    python3 -m kit.listing PORT --a2dis build/dis 0800-0EFF ... an annotated ca65 listing per range from the
                                                                a2-disassemble skill, seeded with every executed
                                                                entry point of the profile (--entry flags)

A snapshot is a 64K RAM dump: traced from its first byte alone, a disassembler leaves most of the game as data.
The coverage profile knows every instruction start that ran, so it supplies the entry points.

Only executed instruction starts are listed (from build/coverage.json), so data tables between routines do not
confuse the decode.  Lines: `addr  count  bytes  insn   ; entries / smc`.  Never commit the output (it holds game
bytes): print it, or write it under build/.
"""
import argparse
import json
import os
import subprocess

# the a2-disassemble skill's disassembler (ca65 output with Apple II annotations); override with $A2DIS
A2DIS = os.environ.get('A2DIS', os.path.expanduser('~/.claude/6502-disassembler-agent/tools/dasm6502.py'))

from . import opcodes, portcfg


def load_cov(path, mem=None):
    """executed starts, entries and SMC marks of the profile images whose executed bytes match mem (all images
    when mem is None): code images of other overlays at the same addresses are left out"""
    with open(path) as f:
        cov = json.load(f)
    instr, entries, smc = {}, {}, set()
    for im in cov['images']:
        base = int(im['page'], 16) << 8
        if mem is not None and base < 0xC000:
            b = bytes.fromhex(im['bytes'])
            vol = {(int(v, 16) if isinstance(v, str) else v) - base for v in im.get('volatile', [])}
            if any(b[o] != mem[base + o] for o in range(256) if im['role'][o] != '.' and o not in vol):
                continue
        for a, n in im['instr'].items():
            a = int(a, 16)
            instr[a] = instr.get(a, 0) + n
        for tgt, kind, site, simg, n in im['entries']:
            entries.setdefault(int(tgt, 16) if isinstance(tgt, str) else tgt, set()).add(
                (kind, site if site is None or isinstance(site, int) else int(site, 16)))
        for a in im.get('smc', {}):
            smc.add(int(a, 16))
        for a in im.get('volatile', []):
            smc.add(int(a, 16) if isinstance(a, str) else a)
    return instr, entries, smc


def listing(mem, instr, entries, smc, lo, hi=None):
    out = []
    a = lo
    stop_at_rts = hi is None
    hi = 0xFFFF if hi is None else hi
    while a <= hi:
        if a not in instr:
            # skip to the next executed start (show the gap size)
            nxt = min((x for x in instr if a < x <= hi), default=None)
            if nxt is None or (stop_at_rts and nxt - a > 0x40):
                break
            out.append(f'       ; .. {nxt - a} byte(s) not executed')
            a = nxt
            continue
        ins = opcodes.decode(mem, a)
        if ins is None:
            out.append(f'{a:04X} {instr[a]:>9}  {mem[a]:02X}        ???')
            a += 1
            continue
        n = ins.len
        raw = ' '.join(f'{mem[(a + i) & 0xFFFF]:02X}' for i in range(n))
        note = []
        if a in entries:
            ks = sorted(entries[a], key=lambda e: (e[0], e[1] or 0))
            ext = [f'{k}<{s:04X}' if s is not None else k for k, s in ks if k not in ('fall',)]
            if ext:
                note.append('from ' + ' '.join(ext[:6]) + (' ...' if len(ext) > 6 else ''))
        if any(x in smc for x in range(a, a + n)):
            note.append('SMC')
        out.append(f'{a:04X} {instr[a]:>9}  {raw:<9} {ins.text():<16}' + ('; ' + ', '.join(note) if note else ''))
        a += n
        if stop_at_rts and mem[a - n] in (0x60, 0x40) and a not in instr:
            nxt = min((x for x in instr if a <= x <= a + 8), default=None)
            if nxt is None:
                break
    return out


def a2dis(mem, instr, entries, lo, hi, outdir):
    """run the a2-disassemble skill's dasm6502.py on mem[lo..hi] with every executed entry as --entry"""
    os.makedirs(outdir, exist_ok=True)
    stem = os.path.join(outdir, f'z{lo:04x}')
    with open(stem + '.bin', 'wb') as f:
        f.write(bytes(mem[lo:hi + 1]))
    seeds = sorted({a for a in entries if lo <= a <= hi and a in instr}
                   | {a for a in instr if lo <= a <= hi and (a - 1) not in instr and (a - 2) not in instr
                      and (a - 3) not in instr})
    cmd = ['python3', A2DIS, stem + '.bin', '--org', hex(lo), '--cpu', '6502', '--annotate', '--output', stem + '.asm']
    for a in seeds:
        cmd += ['--entry', hex(a)]
    with open(stem + '_flags.txt', 'w') as f:
        f.write(' '.join(cmd[2:]) + '\n')
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(r.stdout[-500:] + r.stderr[-500:])
    with open(stem + '.asm') as f:
        traced = next((ln.strip('; \n') for ln in f if 'Traced instructions' in ln), '?')
    print(f'{stem}.asm: {len(seeds)} entries seeded; {traced}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('port')
    ap.add_argument('ranges', nargs='+')
    ap.add_argument('--image', help='snapshot name (default: the spec BASE / first snapshot)')
    ap.add_argument('--coverage')
    ap.add_argument('--a2dis', metavar='DIR', help='write a2-disassemble listings of the ranges into DIR (under build/)')
    a = ap.parse_args(argv)
    port = portcfg.load(a.port)
    img = a.image or 'cold'
    with open(os.path.join(port.snap, f'snap_{img}.bin'), 'rb') as f:
        mem = bytearray(f.read())
    mem += bytes(0x10000 - len(mem))
    instr, entries, smc = load_cov(a.coverage or port.coverage, mem)
    for r in a.ranges:
        lo, _, hi = r.partition('-')
        if a.a2dis:
            if not os.path.exists(A2DIS):
                raise SystemExit(f'a2-disassemble tool not found at {A2DIS} (set $A2DIS)')
            a2dis(mem, instr, entries, int(lo, 16), int(hi or lo, 16), a.a2dis)
            continue
        print(f'; ---- {r}')
        print('\n'.join(listing(mem, instr, entries, smc, int(lo, 16), int(hi, 16) if hi else None)))


if __name__ == '__main__':
    main()
