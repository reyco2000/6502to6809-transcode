#!/usr/bin/env python3
"""mkdsk.py - build a 35-track single-sided RS-DOS (Disk Extended Color BASIC) image.

    mkdsk.py out.dsk FILE[:type[:a]] ...

type: bas (0, tokenized BASIC), dat (1), ml (2, LOADM binary), txt (3)
      default: .BAS -> bas, .BIN -> ml, else dat.  Add ':a' for an ASCII file
      (e.g. an ASCII BASIC program, which RUN"..." accepts).
The disk name is the upper-cased basename (8.3).  Fails if it does not fit.
"""
import os
import sys

TRACKS, SECTORS, SECSIZE = 35, 18, 256
DIR_TRACK = 17
GRAN_SECT = 9
NGRAN = 68


def gran_offset(g):
    track = g // 2
    if track >= DIR_TRACK:
        track += 1
    return (track * SECTORS + (g % 2) * GRAN_SECT) * SECSIZE


def ngran(tracks):
    return 2 * (tracks - 1)


def build(files, tracks=TRACKS):
    """-> (disk image, free granules); tracks 35 (standard) or 40"""
    NGRAN = ngran(tracks)
    img = bytearray(b'\xff' * (tracks * SECTORS * SECSIZE))
    fat = bytearray(b'\xff' * NGRAN)
    dirents = []
    # allocate granules starting next to the directory track (shorter seeks),
    # in the order RS-DOS itself uses: 32,33,34,35,31,30,36,...
    order = []
    lo, hi = 33, 34
    while lo >= 0 or hi < NGRAN:
        if hi < NGRAN:
            order.append(hi); hi += 1
        if lo >= 0:
            order.append(lo); lo -= 1
    free = order
    for path, ftype, ascii_ in files:
        data = open(path, 'rb').read()
        name, ext = os.path.splitext(os.path.basename(path))
        name = name.upper()[:8].ljust(8)
        ext = ext[1:].upper()[:3].ljust(3)
        ngr = max(1, -(-len(data) // (GRAN_SECT * SECSIZE)))
        if ngr > len(free):
            sys.exit(f'mkdsk: {path} does not fit ({len(data)} bytes, {len(free)} granules free)')
        grans = free[:ngr]
        del free[:ngr]
        for i, g in enumerate(grans):
            chunk = data[i * GRAN_SECT * SECSIZE:(i + 1) * GRAN_SECT * SECSIZE]
            off = gran_offset(g)
            img[off:off + len(chunk)] = chunk
            if i + 1 < ngr:
                fat[g] = grans[i + 1]
            else:
                rem = len(data) - i * GRAN_SECT * SECSIZE
                nsec = max(1, -(-rem // SECSIZE))
                fat[g] = 0xC0 + nsec
                last_bytes = rem - (nsec - 1) * SECSIZE
        ent = bytearray(32)
        ent[0:8] = name.encode()
        ent[8:11] = ext.encode()
        ent[11] = ftype
        ent[12] = 0xFF if ascii_ else 0
        ent[13] = grans[0]
        ent[14:16] = last_bytes.to_bytes(2, 'big')
        dirents.append(ent)
    if len(dirents) > 72:
        sys.exit('mkdsk: too many files')
    base = DIR_TRACK * SECTORS * SECSIZE
    img[base:base + SECTORS * SECSIZE] = b'\x00' * (SECTORS * SECSIZE)
    # sector 2 = FAT, sectors 3-11 = directory (unused entries 0xFF)
    img[base + SECSIZE:base + SECSIZE + NGRAN] = fat
    d = base + 2 * SECSIZE
    img[d:d + 9 * SECSIZE] = b'\xff' * (9 * SECSIZE)
    for i, ent in enumerate(dirents):
        img[d + 32 * i:d + 32 * i + 32] = ent
    return img, len(free)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    files = []
    for arg in sys.argv[2:]:
        parts = arg.split(':')
        path = parts[0]
        ext = os.path.splitext(path)[1].lower()
        t = parts[1] if len(parts) > 1 and parts[1] else {'.bas': 'bas', '.bin': 'ml'}.get(ext, 'dat')
        ftype = {'bas': 0, 'dat': 1, 'ml': 2, 'txt': 3}[t]
        files.append((path, ftype, len(parts) > 2 and parts[2] == 'a'))
    img, free = build(files)
    open(sys.argv[1], 'wb').write(img)
    print(f'{sys.argv[1]}: {len(files)} files, {free} of {NGRAN} granules free '
          f'({free * GRAN_SECT * SECSIZE} bytes)')


if __name__ == '__main__':
    main()
