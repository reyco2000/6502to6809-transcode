"""Apple II hi-res decoder: 280x192, six-colour artifact model.

The same rules are used to generate the CoCo 3 lookup tables (kit/tables.py),
so the host frames are the golden reference for the GIME output.
"""

# Colour indices (also the GIME palette slots used by the runtime)
BLACK, PURPLE, GREEN, BLUE, ORANGE, WHITE = 0, 1, 2, 3, 4, 5
RGB = {BLACK: (0, 0, 0), PURPLE: (255, 68, 253), GREEN: (20, 245, 60),
       BLUE: (20, 207, 253), ORANGE: (255, 106, 60), WHITE: (255, 255, 255)}


def row_addr(y, base=0x2000):
    return base + (y & 7) * 0x400 + ((y >> 3) & 7) * 0x80 + (y >> 6) * 0x28


def decode_row(bytes40):
    """Return 280 colour indices for one scan line of 40 hi-res bytes."""
    bits = []
    pal = []
    for b in bytes40:
        hb = b >> 7
        for i in range(7):
            bits.append((b >> i) & 1)
            pal.append(hb)
    out = [BLACK] * 280
    for x in range(280):
        if not bits[x]:
            # a black pixel between two lit pixels of the same column parity shows colour
            l = bits[x - 1] if x > 0 else 0
            r = bits[x + 1] if x < 279 else 0
            if l and r:
                out[x] = colour_of(x - 1, pal[x - 1])
            continue
        l = bits[x - 1] if x > 0 else 0
        r = bits[x + 1] if x < 279 else 0
        if l or r:
            out[x] = WHITE
        else:
            out[x] = colour_of(x, pal[x])
    return out


def colour_of(x, hb):
    even = (x & 1) == 0
    if hb:
        return BLUE if even else ORANGE
    return PURPLE if even else GREEN


def decode_page(mem8k):
    rows = []
    for y in range(192):
        o = row_addr(y, 0)
        rows.append(decode_row(mem8k[o:o + 40]))
    return rows


def save_png(rows, path, scale=2):
    from PIL import Image
    img = Image.new('RGB', (280, 192))
    px = img.load()
    for y, row in enumerate(rows):
        for x, c in enumerate(row):
            px[x, y] = RGB[c]
    if scale != 1:
        img = img.resize((280 * scale, 192 * scale * 1), Image.NEAREST)
    img.save(path)
