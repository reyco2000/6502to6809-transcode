"""Bucket a MAME `trace` log (maincpu, noloop) of the kit runtime by routine.  Game-independent.

    python3 -m kit.mame.profile TRACE BINDIR [--bank st1] [-n 40]

BINDIR is <build>/t512/bin (or t128): main.lst (runtime + windows), vid.lst (video block, task 1), bank_<b>.bin.sym (slot 6).
`STB $FF91` ... `CLR $FF91` brackets task-1 code (the video block at $C000); otherwise $C000-$DFFF is the bank.
Cycles are estimated per instruction (6809 timings, indexed post-byte costs approximated from the operand text).
"""
import argparse
import bisect
import collections
import os
import re

_LST = re.compile(r'^([0-9A-F]{4})(?: [0-9A-F]+)?\s+\(.*?\):\d+ {9}([A-Za-z_.@$][\w.@$]*)')


def lst_labels(path):
    out = {}
    for ln in open(path, errors='replace'):
        m = _LST.match(ln)
        if m:
            out.setdefault(int(m.group(1), 16), m.group(2))
    return out


def sym_labels(path, lo, hi):
    out = {}
    for ln in open(path):
        p = ln.split()
        if len(p) == 3 and p[1] == 'EQU':
            v = int(p[2].lstrip('$'), 16)
            if lo <= v <= hi:
                out.setdefault(v, p[0])
    return out


class Syms:
    def __init__(self, labels):
        self.a = sorted(labels)
        self.n = [labels[a] for a in self.a]

    def __call__(self, pc):
        i = bisect.bisect_right(self.a, pc) - 1
        return self.n[i] if i >= 0 else '?'


INH = {'MUL': 11, 'ABX': 3, 'RTS': 5, 'RTI': 15, 'TFR': 6, 'EXG': 8, 'SWI': 19, 'SYNC': 4, 'CWAI': 20}
W16 = ('LDD', 'LDX', 'LDY', 'LDU', 'LDS', 'STD', 'STX', 'STY', 'STU', 'STS', 'ADDD', 'SUBD', 'CMPX',
       'CMPD', 'CMPY', 'CMPU', 'CMPS')
RMW = ('INC', 'DEC', 'CLR', 'TST', 'NEG', 'COM', 'LSR', 'LSL', 'ASL', 'ASR', 'ROL', 'ROR')


def cycles(op, arg):
    if op in INH:
        return INH[op]
    if op in ('PSHS', 'PSHU', 'PULS', 'PULU'):
        n = 0
        for r in arg.split(','):
            n += 2 if r.strip() in ('X', 'Y', 'U', 'S', 'PC', 'D') else 1
        return 5 + n
    if op in ('BSR',):
        return 7
    if op == 'LBSR':
        return 9
    if op.startswith('LB'):
        return 6
    if op.startswith('B') and len(op) == 3 and not arg.startswith('#') and ',' not in arg and op not in (
            'BIT',) and op[1:] in ('RA', 'RN', 'HI', 'LS', 'CC', 'HS', 'CS', 'LO', 'NE', 'EQ', 'VC', 'VS', 'PL',
                                   'MI', 'GE', 'LT', 'GT', 'LE'):
        return 3
    if not arg:
        return 2
    w = op in W16
    pre = 1 if op in ('CMPD', 'CMPY', 'CMPU', 'CMPS', 'LDY', 'STY', 'LDS', 'STS') else 0
    if arg.startswith('#'):
        return (4 if w else 2) + pre
    if ',' in arg or arg.startswith('['):
        a = arg.strip('[]')
        off, reg = a.rsplit(',', 1) if ',' in a else (a, '')
        if reg.endswith('++') or reg.startswith('--'):
            ex = 3
        elif reg.endswith('+') or reg.startswith('-'):
            ex = 2
        elif off == '':
            ex = 0
        elif off in ('A', 'B'):
            ex = 1
        elif off == 'D':
            ex = 4
        else:
            try:
                v = int(off.replace('$', '0x'), 16) if off.lstrip('-').startswith('$') else int(off)
            except ValueError:
                v = 999
            ex = 0 if -16 <= v <= 15 else 1 if -128 <= v <= 127 else 4
        if arg.startswith('['):
            ex += 3
        if op.startswith('LEA'):
            return 4 + ex
        if op == 'JSR':
            return 7 + ex
        if op == 'JMP':
            return 3 + ex
        return (6 if op in RMW else 5 if w else 4) + ex + pre
    if arg.startswith('<'):
        if op == 'JSR':
            return 7
        if op == 'JMP':
            return 3
        return (6 if op in RMW else 5 if w else 4) + pre
    if op == 'JSR':
        return 8
    if op == 'JMP':
        return 4
    return (7 if op in RMW else 6 if w else 5) + pre


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('trace')
    ap.add_argument('bindir')
    ap.add_argument('--bank', default=None, help='slot-6 bank (cold, st1..st5)')
    ap.add_argument('-n', type=int, default=40)
    a = ap.parse_args()
    main_s = Syms(lst_labels(os.path.join(a.bindir, 'main.lst')))
    vid_s = Syms(lst_labels(os.path.join(a.bindir, 'vid.lst')))
    bank_s = Syms(sym_labels(os.path.join(a.bindir, f'bank_{a.bank}.bin.sym'), 0xC000, 0xDFFF)) if a.bank else None
    task1 = False
    cyc = collections.Counter()
    cnt = collections.Counter()
    region = collections.Counter()
    for ln in open(a.trace, errors='replace'):
        if len(ln) < 7 or ln[4] != ':':
            continue
        pc = int(ln[:4], 16)
        p = ln[6:].split(None, 1)
        op = p[0].upper()
        arg = p[1].strip() if len(p) > 1 else ''
        if task1 and 0xC000 <= pc < 0xE000:
            name, reg = 'vid:' + vid_s(pc), 'video'
        elif 0xC000 <= pc < 0xE000:
            name, reg = ('bank:' + bank_s(pc)) if bank_s else 'bank', 'bank'
        else:
            name = main_s(pc)
            reg = 'runtime' if pc >= 0xE000 else 'engine'
        c = cycles(op, arg)
        cyc[name] += c
        cnt[name] += 1
        region[reg] += c
        if op == 'STB' and arg == '$FF91':
            task1 = True
        elif op == 'CLR' and arg == '$FF91':
            task1 = False
    tot = sum(cyc.values())
    print(f'{tot} cycles (~{tot / 1.79e6:.2f} s at 1.79 MHz), {sum(cnt.values())} instructions')
    for r, c in region.most_common():
        print(f'  {r:8s} {100 * c / tot:5.1f}%')
    print()
    for name, c in cyc.most_common(a.n):
        print(f'{100 * c / tot:5.1f}%  {c:9d}  {cnt[name]:8d}  {name}')


if __name__ == '__main__':
    main()
