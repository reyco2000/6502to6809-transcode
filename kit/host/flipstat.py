"""Measure hi-res churn: at each page flip, bytes/pairs/128-byte units changed since that page was last shown."""
import argparse, collections
from .apple2 import Apple2, CYCLES_PER_FRAME
from .hires import decode_page, save_png

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dsk'); ap.add_argument('--rom'); ap.add_argument('--prom')
    ap.add_argument('--frames', type=int, default=6000); ap.add_argument('--keys', default='')
    ap.add_argument('--out', default=None)
    ap.add_argument('--joy', default='', help='frame:x:y:buttons,... joystick script (0-255, buttons bitmask)')
    a = ap.parse_args()
    full = open(a.rom, 'rb').read()
    m = Apple2(full[-0x3000:], open(a.prom, 'rb').read(), open(a.dsk, 'rb').read(), cxrom=full[-0x4000:-0x3000])
    keys = dict((int(f), int(k, 16)) for f, k in (s.split(':') for s in a.keys.split(',') if s))
    joy = {}
    for it in (x for x in a.joy.split(',') if x):
        fr, jx, jy, jb = (int(v) for v in it.split(':')); joy[fr] = (jx, jy, jb)
    last = {0: bytes(0x2000), 1: bytes(0x2000)}
    flips = []
    orig_io = m.io
    state = {'page': None}
    def io(addr, v):
        lo = addr & 0xFF
        if lo in (0x54, 0x55):
            p = lo - 0x54
            if state['page'] != p:
                state['page'] = p
                cur = bytes(m.ram[0x2000 + p * 0x2000: 0x4000 + p * 0x2000])
                old = last[p]
                nb = sum(1 for i in range(0x2000) if cur[i] != old[i])
                pairs = len({i >> 1 for i in range(0x2000) if cur[i] != old[i]})
                units = len({i >> 7 for i in range(0x2000) if cur[i] != old[i]})
                flips.append((m.cpu.cycles // CYCLES_PER_FRAME, p, nb, pairs, units))
                last[p] = cur
        return orig_io(addr, v)
    m.io = io
    m.boot()
    for f in range(a.frames):
        if f in keys: m.kbd = keys[f] | 0x80
        if f in joy: m.paddle[0], m.paddle[1], m.buttons = joy[f]
        m.run_cycles(CYCLES_PER_FRAME)
        if a.out and f % 500 == 0:
            save_png(decode_page(m.hires_page()), f'{a.out}/s{f:05d}.png')
    # summarise in windows of 600 frames
    win = collections.defaultdict(list)
    for fr, p, nb, pairs, units in flips:
        win[fr // 600].append((nb, pairs, units))
    for w in sorted(win):
        L = win[w]
        print(f'frames {w*600:5d}-{w*600+599}: flips {len(L):3d} ({len(L)/10:.1f}/s)  '
              f'avg changed bytes {sum(x[0] for x in L)/len(L):6.0f}  pairs {sum(x[1] for x in L)/len(L):6.0f}  '
              f'units {sum(x[2] for x in L)/len(L):4.1f}/64  max pairs {max(x[1] for x in L)}')

if __name__ == '__main__':
    main()
