"""Check the converter invariant on an inv.lua dump: every page's GIME buffer = conversion of its shadow.

    python3 -m kit.mame.invcheck512 DUMP

The reference conversion mirrors vid.asm vc_cvt: per pair (b0, b1) of a line, TA[(b1.0, prev.7, prev.6), b0]
gives bytes 0-3, TB[(next.0, b0.7, b0.6), b1] bytes 3-6 (byte 3 = OR of both); prev/next = 0 at the line ends.
GIME line y starts at y*160+10.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
from ..tables import build as _build  # noqa: E402

TA, TB = (bytes(t) for t in _build())


def row_addr(y):
    return (y & 7) * 0x400 + ((y >> 3) & 7) * 0x80 + (y >> 6) * 0x28


def convert(page):
    g = bytearray(160 * 192)
    for y in range(192):
        r = page[row_addr(y):row_addr(y) + 40]
        for p in range(20):
            x0, x1 = r[2 * p], r[2 * p + 1]
            prev = r[2 * p - 1] if p else 0
            nxt = r[2 * p + 2] if p < 19 else 0
            o = (((x1 & 1) << 2) | ((prev >> 7 & 1) << 1) | (prev >> 6 & 1)) << 10 | x0 << 2
            q = (((nxt & 1) << 2) | ((x0 >> 7 & 1) << 1) | (x0 >> 6 & 1)) << 10 | x1 << 2
            d = y * 160 + 10 + 7 * p
            g[d:d + 3] = TA[o:o + 3]
            g[d + 3] = TB[q] | TA[o + 3]
            g[d + 4:d + 7] = TB[q + 1:q + 4]
    return g


def main(argv=None):
    path = (argv or sys.argv[1:])[0]
    d = open(path, 'rb').read()
    R = 4 + 8192 + 32768
    bad = 0
    for k in range(len(d) // R):
        s = d[k * R:(k + 1) * R]
        sh, gm = s[4:8196], s[8196:8196 + 30720]
        ref = convert(sh)
        diff = [i for i in range(30720) if 10 <= i % 160 < 150 and gm[i] != ref[i]]
        if diff:
            bad += 1
            print(f'T {s[2] + 256 * s[3]} page {s[0]} shown {s[1]}: {len(diff)} bytes, first line {diff[0] // 160} '
                  f'col {diff[0] % 160}')
    print(f'samples {len(d) // R}, inconsistent {bad}')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
