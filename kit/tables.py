"""Generate the hi-res -> GIME 320x192x16 conversion tables.

A hi-res row is converted in byte pairs (columns 2k, 2k+1 = 14 pixels = 7 GIME bytes).
  TA[ctx][b0] -> 4 bytes : pixels 0..6  (byte 3 holds pixel 6 in its high nibble)
  TB[ctx][b1] -> 4 bytes : pixels 7..13 (byte 0 holds pixel 7 in its low nibble)
Context (3 bits, same encoding for both tables; it is what LSRB/ROLA x3/ANDA #7 yields on the 6809):
  bit0 = left byte bit6, bit1 = left byte bit7 (palette), bit2 = right neighbour bit0
  TA: left = previous byte, right = b1      TB: left = b0, right = next byte
Layout in memory: table[ctx*1024 + b*4 + i] -> one 8K page per table.
Pairs start at an even pixel, so column parity inside the tables is fixed.
"""
import os, sys, random

from .host.hires import decode_row, colour_of, BLACK, WHITE


def pixels_for(bits, pal, x):
    """Colour of pixel x given bit/palette lists (same rules as hires.decode_row)."""
    n = len(bits)
    l = bits[x - 1] if x > 0 else 0
    r = bits[x + 1] if x < n - 1 else 0
    if not bits[x]:
        return colour_of(x - 1, pal[x - 1]) if (l and r) else BLACK
    return WHITE if (l or r) else colour_of(x, pal[x])


def expand(b):
    return [(b >> i) & 1 for i in range(7)], [b >> 7] * 7


def entry_a(ctx, b0):
    pb = ctx & 1; phb = (ctx >> 1) & 1; nb = (ctx >> 2) & 1
    bits0, pal0 = expand(b0)
    # synthetic row: [prev pixel 6] + b0 pixels + [next pixel 0]; x offset so b0 pixel0 is even (=14k)
    bits = [pb] + bits0 + [nb]
    pal = [phb] + pal0 + [b0 >> 7]  # next pixel's palette irrelevant for b0's pixels except via black rule (uses x-1)
    # parity: pixel index i of b0 sits at x = 14k + i  -> even when i even. Offset list index by 1.
    cols = [pixels_for_parity(bits, pal, i + 1, i) for i in range(7)]
    return pack(cols, first_low=False)


def entry_b(ctx, b1):
    pb = ctx & 1; phb = (ctx >> 1) & 1; nb = (ctx >> 2) & 1
    bits1, pal1 = expand(b1)
    bits = [pb] + bits1 + [nb]
    pal = [phb] + pal1 + [b1 >> 7]
    cols = [pixels_for_parity(bits, pal, i + 1, 7 + i) for i in range(7)]
    return pack(cols, first_low=True)


def pixels_for_parity(bits, pal, idx, xpos):
    """Like pixels_for but colour parity taken from the real screen x (xpos within the pair)."""
    l = bits[idx - 1]; r = bits[idx + 1]
    if not bits[idx]:
        return colour_of(xpos - 1, pal[idx - 1]) if (l and r) else BLACK
    return WHITE if (l or r) else colour_of(xpos, pal[idx])


def pack(cols, first_low):
    nib = ([None] if first_low else []) + cols + ([] if first_low else [None])
    out = []
    for i in range(0, 8, 2):
        hi = nib[i] or 0; lo = nib[i + 1] or 0
        out.append(((hi & 15) << 4) | (lo & 15))
    return out


def build():
    ta = bytearray(8192); tb = bytearray(8192)
    for ctx in range(8):
        for b in range(256):
            ta[ctx * 1024 + b * 4: ctx * 1024 + b * 4 + 4] = bytes(entry_a(ctx, b))
            tb[ctx * 1024 + b * 4: ctx * 1024 + b * 4 + 4] = bytes(entry_b(ctx, b))
    return ta, tb


def convert_row(row40, ta, tb):
    """Reference model of the 6809 converter: 40 bytes -> 140 GIME bytes (280 px)."""
    out = bytearray()
    for k in range(20):
        b0 = row40[2 * k]; b1 = row40[2 * k + 1]
        prev = row40[2 * k - 1] if k else 0
        nxt = row40[2 * k + 2] if k < 19 else 0
        ca = ((b1 & 1) << 2) | (prev >> 6)
        cb = ((nxt & 1) << 2) | (b0 >> 6)
        a = ta[ca * 1024 + b0 * 4: ca * 1024 + b0 * 4 + 4]
        b = tb[cb * 1024 + b1 * 4: cb * 1024 + b1 * 4 + 4]
        out += bytes([a[0], a[1], a[2], a[3] | b[0], b[1], b[2], b[3]])
    return out


def unpack(gime):
    px = []
    for v in gime:
        px += [v >> 4, v & 15]
    return px


def selftest(n=3000):
    ta, tb = build()
    rnd = random.Random(1)
    for t in range(n):
        row = bytes(rnd.choice([0, 0x7F, 0xFF, 0x80, 0x2A, 0x55, 0xAA, 0xD5, rnd.randrange(256)]) for _ in range(40))
        want = decode_row(row)
        got = unpack(convert_row(row, ta, tb))
        if want != got:
            bad = [i for i in range(280) if want[i] != got[i]]
            raise SystemExit(f'mismatch row {row.hex()} at {bad[:8]}')
    print(f'selftest ok: {n} rows identical to hires.decode_row')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        selftest()
    else:
        out = sys.argv[1] if len(sys.argv) > 1 else '.'
        ta, tb = build()
        open(os.path.join(out, 'tab_a.bin'), 'wb').write(ta)
        open(os.path.join(out, 'tab_b.bin'), 'wb').write(tb)
