"""Offline test and benchmark of the 512K video converter (rt/t512/vid.asm vc_conv) in cc3sim.  Game-independent.

    python3 -m kit.vidtest [-n CASES] [-s SEED] [-v] [--vid FILE]

Each case: an old Apple page whose shadow and GIME buffer are in sync, a new page that differs only inside a list
of dirty rectangles (or the whole-page mark), then vc_conv on it.  Checked: the GIME buffer equals the reference
conversion (kit/mame/invcheck512.convert) of the new page, and the shadow equals the new page.  Reported per scene
kind: cycles, cycles per changed byte pair and per rectangle line, so converter changes can be compared like for
like (`--vid` runs another copy of vid.asm, e.g. the previous version).
"""
import argparse
import os
import random
import re
import subprocess
import sys
import tempfile

from . import build512 as b5
from . import tables as gen_tables
from .asm import assemble
from .mame.invcheck512 import convert, row_addr
from .paths import SIM

L = b5.LAYOUT
VR_MAX = 24
VARS = {'vr_n1': 0xE100, 'vr_n2': 0xE101, 'vr_l1': 0xE102, 'vr_l2': 0xE102 + VR_MAX * 4,
        'vr_rfon': 0xE102 + VR_MAX * 8, 'vr_rflag': 0xE103 + VR_MAX * 8}
PAGE_ARG = 0xE0F0
STACK = 0xFD00
DRIVER = ['\torg\t$E000',
          'start\tlds\t#$%04X' % STACK,
          '\tlda\t#BLK_VID', '\tsta\t$FFAE',          # task 1: slot 6 = converter, slot 7 = this block
          '\tlda\t#BLK_RT', '\tsta\t$FFAF',
          '\tlda\t#1', '\tsta\t$FF91',
          '\tlda\t$%04X' % PAGE_ARG,
          '\tjsr\t$C000',
          '\tclr\t$FF91',
          'halt\tbra\thalt']


def blk(b):
    return b * 0x2000


def build(vid_src, wd):
    """-> (vid block bytes, driver bytes, halt address, tables)"""
    vid = os.path.join(wd, 'vid.bin')
    assemble(['\tpragma\tnoautobranchlength'] + b5.layout_lines() + ['VVPAGE\tequ\t$FE', f'VR_MAX\tequ\t{VR_MAX}']
             + [f'{k}\tequ\t${v:04X}' for k, v in VARS.items()] + open(vid_src).read().splitlines()
             + b5.rowtab_lines(), vid)
    drv, sym = os.path.join(wd, 'drv.bin'), os.path.join(wd, 'drv.sym')
    assemble(['\tpragma\tnoautobranchlength'] + b5.layout_lines() + DRIVER, drv, symbols=sym)
    halt = None
    for ln in open(sym):
        m = re.match(r'halt EQU \$([0-9A-F]+)', ln)
        if m:
            halt = int(m.group(1), 16)
    ta, tb = gen_tables.build()
    return open(vid, 'rb').read(), open(drv, 'rb').read(), halt, (bytes(ta), bytes(tb))


def run(parts, page, old, new, rects, wd):
    """rects: list of (y0, y1, c0, c1) or None (whole page).  -> (cycles, ok, message)"""
    vid, drv, halt, (ta, tb) = parts
    mem = bytearray(512 * 1024)
    mem[blk(L['BLK_TA']):blk(L['BLK_TA']) + len(ta)] = ta
    mem[blk(L['BLK_TB']):blk(L['BLK_TB']) + len(tb)] = tb
    mem[blk(L['BLK_VID']):blk(L['BLK_VID']) + len(vid)] = vid
    rt = blk(L['BLK_RT'])
    mem[rt:rt + len(drv)] = drv
    mem[rt + PAGE_ARG - 0xE000] = page
    mem[blk(L['BLK_ARAM'] + 1 + page):blk(L['BLK_ARAM'] + 1 + page) + 0x2000] = new
    mem[blk(L['BLK_SH1'] + page):blk(L['BLK_SH1'] + page) + 0x2000] = old
    g0 = blk(L['BLK_GIME1'] + 4 * page)
    mem[g0:g0 + 30720] = convert(old)
    n = VARS['vr_n1'] + page - 0xE000
    lst = VARS['vr_l1'] + page * VR_MAX * 4 - 0xE000
    if rects is None or len(rects) > VR_MAX:
        mem[rt + n] = 0xFF
    else:
        mem[rt + n] = len(rects)
        for i, r in enumerate(rects):
            mem[rt + lst + 4 * i:rt + lst + 4 * i + 4] = bytes(r)
    img, out = os.path.join(wd, 'mem.bin'), os.path.join(wd, 'out.bin')
    open(img, 'wb').write(mem)
    r = subprocess.run([SIM, '-l', f'0:{img}', '-m', f'7:{L["BLK_RT"]:X}', '-m', f'F:{L["BLK_RT"]:X}',
                        '-pc', 'E000', '-stop', f'{halt:04X}', '-max', '50000000', '-d', f'0:80000:{out}'],
                       capture_output=True, text=True)
    m = re.search(r'cycles=(\d+)', r.stdout)
    if r.returncode != 0 or not m:
        return 0, False, f'did not finish: {r.stdout.strip()} {r.stderr.strip()[-200:]}'
    o = open(out, 'rb').read()
    gm = o[g0:g0 + 30720]
    ref = convert(new)
    bad = [i for i in range(30720) if 10 <= i % 160 < 150 and gm[i] != ref[i]]
    sh = o[blk(L['BLK_SH1'] + page):blk(L['BLK_SH1'] + page) + 0x2000]
    bads = [y for y in range(192) if sh[row_addr(y):row_addr(y) + 40] != new[row_addr(y):row_addr(y) + 40]]
    msg = ''
    if bad:
        msg += f'GIME: {len(bad)} bytes differ, first line {bad[0] // 160} byte {bad[0] % 160 - 10}; '
    if bads:
        msg += f'shadow: lines {bads[:6]} differ'
    return int(m.group(1)), not msg, msg


# ---- scenes: (kind, old page, new page, rects)
def _page(rnd, fill):
    p = bytearray(0x2000)
    for y in range(192):
        a = row_addr(y)
        for c in range(40):
            p[a + c] = fill(rnd, y, c)
    return p


def _blank(rnd, y, c):
    return 0


def _busy(rnd, y, c):
    return rnd.choice([0x00, 0x7F, 0xFF, 0x80, 0x2A, 0xD5, rnd.randint(0, 255)])


def _set(p, y, c, v):
    p[row_addr(y) + c] = v


def _get(p, y, c):
    return p[row_addr(y) + c]


def scene_sprites(rnd, old):
    new, rects = bytearray(old), []
    for _ in range(rnd.randint(1, 14)):
        w, h = rnd.randint(2, 6), rnd.randint(3, 30)
        c0, y0 = rnd.randint(0, 40 - w), rnd.randint(0, 192 - h)
        for y in range(y0, y0 + h):
            for c in range(c0, c0 + w):
                if rnd.random() < 0.7:
                    _set(new, y, c, (_get(new, y, c) & rnd.randint(0, 255)) | rnd.randint(0, 255))
        rects.append((y0, y0 + h - 1, c0, c0 + w - 1))
    return new, rects


def scene_lines(rnd, old):
    """diagonal lines (one or two bytes a row), reported as 32-line bands rounded out to 4 columns"""
    new, rects = bytearray(old), []
    for _ in range(rnd.randint(1, 6)):
        y0, n = rnd.randint(0, 150), rnd.randint(10, 165)
        y1 = min(191, y0 + n)
        c0 = rnd.randint(0, 39)
        cols = {}
        for y in range(y0, y1 + 1):
            c = min(39, c0 + (y - y0) * 2 // 7)
            for cc in (c, c + 1)[:rnd.choice([1, 2])]:
                if cc <= 39:
                    _set(new, y, cc, _get(new, y, cc) | rnd.choice([0x81, 0xC0, 0x82, 0x85, 0xA0]))
            cols[y] = c
        y = y0
        while y <= y1:
            yb = min(y1, (y | 31))
            lo, hi = min(cols[k] for k in range(y, yb + 1)), max(cols[k] for k in range(y, yb + 1)) + 1
            rects.append((y, yb, lo & ~3, min(39, hi | 3)))
            y = yb + 1
    return new, rects


def scene_edges(rnd, old):
    """changes at the line ends and in the neighbour bits (0, 6, 7)"""
    new, rects = bytearray(old), []
    for _ in range(rnd.randint(1, 8)):
        y0 = rnd.randint(0, 180)
        y1 = y0 + rnd.randint(0, 11)
        c = rnd.choice([0, 1, 2, 37, 38, 39, rnd.randint(0, 39)])
        for y in range(y0, y1 + 1):
            _set(new, y, c, _get(new, y, c) ^ rnd.choice([0x01, 0x40, 0x80, 0xC1, rnd.randint(1, 255)]))
        rects.append((y0, y1, c, c))
    return new, rects


def scene_page(rnd, old):
    new = bytearray(old)
    for _ in range(rnd.randint(50, 3000)):
        new[row_addr(rnd.randint(0, 191)) + rnd.randint(0, 39)] = rnd.randint(0, 255)
    return new, None


def scene_empty(rnd, old):
    """rectangles with nothing changed: the scan alone"""
    return bytearray(old), [(y, min(191, y + rnd.randint(10, 60)), c, min(39, c + rnd.randint(2, 30)))
                            for y, c in [(rnd.randint(0, 150), rnd.randint(0, 30)) for _ in range(rnd.randint(1, 8))]]


SCENES = {'sprites': scene_sprites, 'lines': scene_lines, 'edges': scene_edges, 'page': scene_page,
          'empty': scene_empty}


def changed_pairs(old, new):
    return sum(1 for y in range(192) for p in range(20)
               if old[row_addr(y) + 2 * p:row_addr(y) + 2 * p + 2] != new[row_addr(y) + 2 * p:row_addr(y) + 2 * p + 2])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('-n', type=int, default=60)
    ap.add_argument('-s', type=int, default=1)
    ap.add_argument('-v', action='store_true')
    ap.add_argument('--vid', default=os.path.join(b5.RT, 'vid.asm'))
    a = ap.parse_args(argv)
    rnd = random.Random(a.s)
    stats, fails = {}, 0
    with tempfile.TemporaryDirectory() as wd:
        parts = build(a.vid, wd)
        print(f'vid block {len(parts[0])} bytes ({a.vid})')
        for i in range(a.n):
            kind = sorted(SCENES)[i % len(SCENES)]
            old = _page(rnd, rnd.choice([_blank, _busy]))
            new, rects = SCENES[kind](rnd, old)
            page = rnd.randint(0, 1)
            cyc, ok, msg = run(parts, page, old, new, rects, wd)
            s = stats.setdefault(kind, [0, 0, 0, 0])
            s[0] += 1
            s[1] += cyc
            s[2] += changed_pairs(old, new)
            s[3] += 192 if rects is None else sum(r[1] - r[0] + 1 for r in rects)
            if not ok:
                fails += 1
                print(f'FAIL case {i} {kind} page {page}: {msg}')
            elif a.v:
                print(f'case {i} {kind}: {cyc} cycles')
    tot = [0, 0, 0, 0]
    for k, (n, cyc, pairs, lines) in sorted(stats.items()):
        tot = [t + v for t, v in zip(tot, (n, cyc, pairs, lines))]
        print(f'  {k:8} {n:3} cases  {cyc / n:9.0f} cycles/case  {cyc / max(pairs, 1):6.1f} /changed pair  '
              f'{cyc / max(lines, 1):6.1f} /rect line')
    print(f'  {"all":8} {tot[0]:3} cases  {tot[1]} cycles   fails {fails}')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
