#!/usr/bin/env python3
"""lzss.py - tiny LZSS for the CoCo loader (decoder: src/unlz.asm).

Stream: a flag byte, then 8 items (LSB first): bit 1 = literal byte,
bit 0 = match: 2 bytes  hhhhllll llllllll -> offset 1..4096 (12 bits + 1),
length = hhhh + 3 (3..18).  A match with offset field 0 and length field 0
(two zero bytes) ends the stream.  Matches may overlap (byte copy).
"""
import sys


def compress(data):
    out = bytearray()
    i, n = 0, len(data)
    head = {}
    items = []
    while i < n:
        best_len, best_off = 0, 0
        key = data[i:i + 3]
        cands = head.get(key, [])
        for p in reversed(cands[-64:]):
            if i - p > 4095:
                continue
            l = 0
            while l < 18 and i + l < n and data[p + l] == data[i + l]:
                l += 1
            if l > best_len:
                best_len, best_off = l, i - p
                if l == 18:
                    break
        if best_len == 3 and best_off == 1:
            best_len = 0                 # would encode as the end marker
        if best_len >= 3:
            items.append((best_off, best_len))
            step = best_len
        else:
            items.append(data[i])
            step = 1
        for k in range(i, min(i + step, n - 2)):
            head.setdefault(data[k:k + 3], []).append(k)
        i += step
    items.append((0, 0))                 # end marker (offset field 0)
    for g in range(0, len(items), 8):
        grp = items[g:g + 8]
        flags = 0
        body = bytearray()
        for b, it in enumerate(grp):
            if isinstance(it, int):
                flags |= 1 << b
                body.append(it)
            else:
                off, ln = it
                if ln == 0:
                    body += b'\x00\x00'
                else:
                    v = (ln - 3) << 12 | (off - 1)
                    # offset field 0 with length 0 is reserved: off-1 == 0 and ln == 3 -> v = 0
                    if v == 0:
                        raise SystemExit('reserved code')   # avoided below
                    body += bytes([v >> 8, v & 0xFF])
        out.append(flags)
        out += body
    return bytes(out)


def decompress(src):
    out = bytearray()
    i = 0
    while True:
        flags = src[i]
        i += 1
        for b in range(8):
            if flags >> b & 1:
                out.append(src[i])
                i += 1
            else:
                v = src[i] << 8 | src[i + 1]
                i += 2
                if v == 0:
                    return bytes(out)
                off, ln = (v & 0xFFF) + 1, (v >> 12) + 3
                for _ in range(ln):
                    out.append(out[-off])


if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read()
    c = compress(d)
    assert decompress(c) == d
    open(sys.argv[2], 'wb').write(c)
    print('%s: %d -> %d' % (sys.argv[1], len(d), len(c)))
