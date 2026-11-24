"""Apple II disk images: identify, list, extract, convert (game-independent).

    python3 -m kit.disk.a2disk info IMAGE              format, sector order, file system, boot sector, protection hints
    python3 -m kit.disk.a2disk cat IMAGE               catalog (DOS 3.3 or ProDOS, with types and load addresses)
    python3 -m kit.disk.a2disk get IMAGE NAME OUT      extract one file (DOS 3.3 B files: the 4-byte header is
                                                       stripped and the load address printed)
    python3 -m kit.disk.a2disk todsk IMAGE OUT.dsk     convert to a DOS-order 140K .dsk (decodes .nib/.woz; lists
                                                       sectors that do not decode = copy protection)
    python3 -m kit.disk.a2disk tonib IMAGE OUT.nib     convert to 35 x 6656 nibble tracks

Formats:
  .dsk/.do  140K, DOS 3.3 sector order          .po    140K, ProDOS block order (detected by content too)
  .nib      35 x 6656 raw nibbles (keeps most protection)
  .woz      WOZ 1/2 bit streams (keeps protection; whole tracks only: quarter tracks are reported, not used)
  .2mg      2IMG header + DOS/ProDOS/NIB payload    .d13   13-sector DOS 3.2 (identified only: not decoded)
  others    AppleSingle / ShrinkIt (.shk/.sdk) / Binary II (.bny/.bqy): identified, extract them with
            CiderPress II or AppleCommander first (an AppleSingle file goes straight to the a2-disassemble skill)
The host emulator (kit/host/apple2.py) boots every format here through host_tracks(): sector images are
nibblized, nibble/bit images are used as they are (a protected disk boots as on the real drive).
"""
import struct
import sys

DSK_SIZE = 143360
NIB_TRACK = 6656
TRACKS = 35

# logical sector -> physical sector
DOS_L2P = [0x0, 0xD, 0xB, 0x9, 0x7, 0x5, 0x3, 0x1, 0xE, 0xC, 0xA, 0x8, 0x6, 0x4, 0x2, 0xF]
PRODOS_L2P = [0x0, 0x2, 0x4, 0x6, 0x8, 0xA, 0xC, 0xE, 0x1, 0x3, 0x5, 0x7, 0x9, 0xB, 0xD, 0xF]
DOS_P2L = [DOS_L2P.index(p) for p in range(16)]
PO_TO_DO = [DOS_P2L[PRODOS_L2P[i]] for i in range(16)]       # .po sector i -> .dsk sector

WT = [0x96, 0x97, 0x9A, 0x9B, 0x9D, 0x9E, 0x9F, 0xA6, 0xA7, 0xAB, 0xAC, 0xAD, 0xAE, 0xAF, 0xB2, 0xB3,
      0xB4, 0xB5, 0xB6, 0xB7, 0xB9, 0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF, 0xCB, 0xCD, 0xCE, 0xCF, 0xD3,
      0xD6, 0xD7, 0xD9, 0xDA, 0xDB, 0xDC, 0xDD, 0xDE, 0xDF, 0xE5, 0xE6, 0xE7, 0xE9, 0xEA, 0xEB, 0xEC,
      0xED, 0xEE, 0xEF, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD, 0xFE, 0xFF]
RT = {v: i for i, v in enumerate(WT)}

DOS_TYPES = {0x00: 'T', 0x01: 'I', 0x02: 'A', 0x04: 'B', 0x08: 'S', 0x10: 'R', 0x20: 'a', 0x40: 'b'}
PRODOS_TYPES = {0x04: 'TXT', 0x06: 'BIN', 0x0F: 'DIR', 0xFA: 'INT', 0xFC: 'BAS', 0xFF: 'SYS', 0xB3: 'S16'}


# ------------------------------------------------------------------------------------------------ format
def detect(data, name=''):
    """-> (kind, payload): kind in dsk, po, nib, woz1, woz2, d13, applesingle, shrinkit, binary2, unknown"""
    low = name.lower()
    if data[:4] == b'2IMG':
        fmt = struct.unpack_from('<I', data, 12)[0]           # 0 DOS order, 1 ProDOS order, 2 NIB
        off, ln = struct.unpack_from('<II', data, 24)
        inner = data[off:off + ln]
        return ({0: 'dsk', 1: 'po', 2: 'nib'}.get(fmt, 'unknown'), inner)
    if data[:4] in (b'WOZ1', b'WOZ2') and data[4:8] == b'\xff\x0a\x0d\x0a':
        return data[:4].decode().lower(), data
    if data[:4] == b'\x00\x05\x16\x00':
        return 'applesingle', data
    if data[:6] == b'\x4e\xf5\x46\xe9\x6c\xe5' or data[:2] == b'\x4e\xf5':
        return 'shrinkit', data
    if data[:3] == b'\x0a\x47\x4c':
        return 'binary2', data
    if len(data) == TRACKS * NIB_TRACK:
        return 'nib', data
    if len(data) == 116480:
        return 'd13', data
    if len(data) == DSK_SIZE:
        if low.endswith('.po'):
            return 'po', data
        if low.endswith(('.dsk', '.do')):
            # a .dsk can still be ProDOS-ordered: check the content
            if not _dos_vtoc(data) and _prodos_vol(po_to_dsk(data)):
                return 'po', data
            return 'dsk', data
        return ('po' if _prodos_vol(data) and not _dos_vtoc(data) else 'dsk'), data
    return 'unknown', data


def po_to_dsk(po):
    out = bytearray(DSK_SIZE)
    for t in range(TRACKS):
        for i in range(16):
            d = PO_TO_DO[i]
            out[(t * 16 + d) * 256:(t * 16 + d + 1) * 256] = po[(t * 16 + i) * 256:(t * 16 + i + 1) * 256]
    return bytes(out)


def dsk_to_po(dsk):
    out = bytearray(DSK_SIZE)
    for t in range(TRACKS):
        for i in range(16):
            d = PO_TO_DO[i]
            out[(t * 16 + i) * 256:(t * 16 + i + 1) * 256] = dsk[(t * 16 + d) * 256:(t * 16 + d + 1) * 256]
    return bytes(out)


# ------------------------------------------------------------------------------------------------ nibbles
def _fourfour(v):
    return [(v >> 1) | 0xAA, v | 0xAA]


def _encode62(data):
    buf = [0] * 342
    for i in range(256):
        buf[86 + i] = data[i] >> 2
    for i in range(86):
        v = 0
        for k, j in enumerate((i, i + 86, i + 172)):
            if j < 256:
                b = data[j] & 3
                v |= (((b & 1) << 1) | (b >> 1)) << (2 * k)
        buf[i] = v
    out, prev = [], 0
    for v in buf:
        out.append(WT[v ^ prev])
        prev = v
    out.append(WT[prev])
    return out


def nibblize(dsk, volume=254):
    """DOS-order sector image -> 35 nibble tracks of 6656 bytes (standard 16-sector format)"""
    tracks = []
    for t in range(TRACKS):
        tr = [0xFF] * 48
        for p in range(16):
            s = DOS_P2L[p]
            tr += [0xD5, 0xAA, 0x96] + _fourfour(volume) + _fourfour(t) + _fourfour(p) + \
                _fourfour(volume ^ t ^ p) + [0xDE, 0xAA, 0xEB] + [0xFF] * 6
            tr += [0xD5, 0xAA, 0xAD] + _encode62(dsk[(t * 16 + s) * 256:(t * 16 + s + 1) * 256]) + \
                [0xDE, 0xAA, 0xEB] + [0xFF] * 20
        tracks.append(bytes(tr + [0xFF] * (NIB_TRACK - len(tr))))
    return tracks


def denibblize(tracks):
    """nibble tracks -> (DOS-order image, [(track, phys sector or None, problem)]).  Sectors that do not
    decode with the standard D5 AA 96 / D5 AA AD 6&2 format are listed: that is where protection lives."""
    out = bytearray(DSK_SIZE)
    bad = []
    d44 = lambda a, b: ((a << 1) | 1) & b
    for t in range(TRACKS):
        tr = tracks[t] if t < len(tracks) else b''
        if not tr:
            bad.append((t, None, 'no track'))
            continue
        tt = tr + tr[:1024]
        got = {}
        i = 0
        while True:
            i = tt.find(b'\xD5\xAA\x96', i)
            if i < 0 or i >= len(tr):
                break
            vol, trk, sec, chk = (d44(tt[i + 3 + 2 * k], tt[i + 4 + 2 * k]) for k in range(4))
            i += 3
            if vol ^ trk ^ sec != chk or trk != t or sec > 15 or sec in got:
                continue
            j = tt.find(b'\xD5\xAA\xAD', i + 8, i + 8 + 64)
            if j < 0:
                continue
            try:
                v = [RT[x] for x in tt[j + 3:j + 3 + 343]]
            except KeyError:
                continue
            buf, p = [], 0
            for x in v[:342]:
                p ^= x
                buf.append(p)
            if len(v) < 343 or p != v[342]:
                continue
            aux, main = buf[:86], buf[86:]
            data = bytearray(256)
            for k in range(256):
                a = aux[k % 86] >> (2 * (k // 86))
                data[k] = (main[k] << 2) | ((a & 1) << 1) | ((a >> 1) & 1)
            got[sec] = bytes(data)
        for p in range(16):
            L = DOS_P2L[p]
            if p in got:
                out[(t * 16 + L) * 256:(t * 16 + L + 1) * 256] = got[p]
            else:
                bad.append((t, p, 'missing'))
    return bytes(out), bad


def _bits_to_nibbles(bits, nbits):
    """one revolution of a WOZ bit stream -> nibble bytes (a read latch: shift until bit 7 is set)"""
    out, reg = bytearray(), 0
    total = nbits + 64             # run a little past the end so the wrap-around nibble completes
    for k in range(total):
        i = k % nbits
        b = (bits[i >> 3] >> (7 - (i & 7))) & 1
        reg = ((reg << 1) | b) & 0xFF
        if reg & 0x80:
            if k < nbits or not out:
                out.append(reg)
            reg = 0
    return bytes(out)


def woz_tracks(data):
    """-> (35 nibble tracks, notes).  Whole tracks only (TMAP entry of quarter track 4t)."""
    ver = data[:4]
    chunks, i = {}, 12
    while i + 8 <= len(data):
        cid, ln = data[i:i + 4], struct.unpack_from('<I', data, i + 4)[0]
        chunks[cid] = (i + 8, ln)
        i += 8 + ln
    tmo, _ = chunks[b'TMAP']
    tmap = data[tmo:tmo + 160]
    tro, _ = chunks[b'TRKS']
    notes = []
    quarter = [q for q in range(160) if q % 4 and tmap[q] != 0xFF and
               tmap[q] not in (tmap[q - (q % 4)], tmap[min(159, q - (q % 4) + 4)])]
    if quarter:
        notes.append(f'quarter/half tracks with their own data: {[q / 4 for q in quarter][:8]} (not used)')
    tracks = []
    for t in range(TRACKS):
        idx = tmap[t * 4]
        if idx == 0xFF:
            tracks.append(b'')
            continue
        if ver == b'WOZ2':
            sb, bc, nbits = struct.unpack_from('<HHI', data, tro + idx * 8)
            bits = data[sb * 512:sb * 512 + bc * 512]
        else:
            o = tro + idx * 6656
            bits = data[o:o + 6646]
            nbits = struct.unpack_from('<H', data, o + 6648)[0]
        tracks.append(_bits_to_nibbles(bits, nbits) if nbits else b'')
    if b'INFO' in chunks:
        io_ = chunks[b'INFO'][0]
        dtype = data[io_ + 1]
        if dtype != 1:
            notes.append('WOZ disk type is not 5.25"')
    return tracks, notes


def host_tracks(data, name=''):
    """any image -> 35 nibble tracks for kit/host Disk2 (the bytes the drive head sees)"""
    kind, payload = detect(data, name)
    if kind == 'dsk':
        return nibblize(payload)
    if kind == 'po':
        return nibblize(po_to_dsk(payload))
    if kind == 'nib':
        return [payload[t * NIB_TRACK:(t + 1) * NIB_TRACK] for t in range(TRACKS)]
    if kind in ('woz1', 'woz2'):
        return woz_tracks(payload)[0]
    raise ValueError(f'{kind}: cannot be booted (see `a2disk info`)')


def sector_image(data, name=''):
    """any image -> (DOS-order 140K image, bad sectors)"""
    kind, payload = detect(data, name)
    if kind == 'dsk':
        return payload, []
    if kind == 'po':
        return po_to_dsk(payload), []
    if kind in ('nib', 'woz1', 'woz2'):
        return denibblize(host_tracks(data, name))
    raise ValueError(f'{kind}: no sector image')


# ------------------------------------------------------------------------------------------------ file systems
def _sec(img, t, s):
    return img[(t * 16 + s) * 256:(t * 16 + s + 1) * 256]


def _dos_vtoc(img):
    v = _sec(img, 17, 0)
    return v[0x27] == 122 and v[0x34] == 35 and v[0x35] == 16 and 0 < v[1] < 35 and v[2] < 16


def _block(po, b):
    return po[b * 512:(b + 1) * 512]


def _prodos_vol(po):
    b = _block(po, 2)
    return b[0:2] == b'\0\0' and b[4] >> 4 == 0xF and 1 <= (b[4] & 15) <= 15 and \
        all(0x2E <= c <= 0x5A or c == 0x2E for c in b[5:5 + (b[4] & 15)])


def dos_catalog(img):
    v = _sec(img, 17, 0)
    t, s, out, seen = v[1], v[2], [], set()
    while 0 < t < 35 and s < 16 and (t, s) not in seen:
        seen.add((t, s))
        c = _sec(img, t, s)
        for i in range(7):
            e = c[11 + i * 35:11 + (i + 1) * 35]
            if e[0] in (0, 0xFF):
                continue
            name = bytes(x & 0x7F for x in e[3:33]).decode('ascii', 'replace').rstrip()
            out.append({'name': name, 'type': DOS_TYPES.get(e[2] & 0x7F, '?'), 'locked': bool(e[2] & 0x80),
                        'tslist': (e[0], e[1]), 'sectors': e[33] | e[34] << 8})
        t, s = c[1], c[2]
    return out


def dos_read(img, entry):
    data, (t, s), seen = bytearray(), entry['tslist'], set()
    while 0 < t < 35 and (t, s) not in seen:
        seen.add((t, s))
        ts = _sec(img, t, s)
        for k in range(122):
            dt, ds = ts[0x0C + 2 * k], ts[0x0D + 2 * k]
            if dt == 0 and ds == 0:
                break
            data += _sec(img, dt, ds)
        t, s = ts[1], ts[2]
    if entry['type'] == 'B':
        addr, ln = struct.unpack_from('<HH', data, 0)
        return bytes(data[4:4 + ln]), addr
    if entry['type'] in ('A', 'I'):
        ln = struct.unpack_from('<H', data, 0)[0]
        return bytes(data[2:2 + ln]), None
    return bytes(data), None


def prodos_catalog(po, key=2, path=''):
    out, b, first = [], key, True
    while b:
        blk = _block(po, b)
        for i in range(13):
            if first and i == 0:
                continue
            e = blk[4 + i * 0x27:4 + (i + 1) * 0x27]
            st, nl = e[0] >> 4, e[0] & 15
            if st == 0:
                continue
            name = path + e[1:1 + nl].decode('ascii', 'replace')
            ent = {'name': name, 'storage': st, 'type': PRODOS_TYPES.get(e[0x10], f'${e[0x10]:02X}'),
                   'key': struct.unpack_from('<H', e, 0x11)[0], 'blocks': struct.unpack_from('<H', e, 0x13)[0],
                   'eof': e[0x15] | e[0x16] << 8 | e[0x17] << 16, 'aux': struct.unpack_from('<H', e, 0x1F)[0]}
            out.append(ent)
            if st == 0xD:
                out += prodos_catalog(po, ent['key'], name + '/')
        first = False
        b = struct.unpack_from('<H', blk, 2)[0]
    return out


def prodos_read(po, e):
    def blk(n):
        return _block(po, n) if n else bytes(512)
    if e['storage'] == 1:
        data = blk(e['key'])
    elif e['storage'] == 2:
        ix = blk(e['key'])
        data = b''.join(blk(ix[k] | ix[256 + k] << 8) for k in range(256))
    elif e['storage'] == 3:
        mi = blk(e['key'])
        data = b''
        for j in range(128):
            ix = blk(mi[j] | mi[256 + j] << 8)
            data += b''.join(blk(ix[k] | ix[256 + k] << 8) for k in range(256))
    else:
        raise ValueError('not a regular file')
    return data[:e['eof']], e['aux']


def boot_kind(img):
    b = _sec(img, 0, 0)
    if b[:5] == bytes.fromhex('01a527c909'):
        return 'DOS 3.3-style boot sector (sector 0 reads more sectors through the PROM)'
    if b[:5] == bytes.fromhex('0138b0034c'):
        return 'ProDOS boot block'
    if b[0] == 0:
        return 'empty boot sector'
    return f'custom boot sector: loads {b[0]} sector(s) at $0800, first bytes {b[:8].hex()}'


# ------------------------------------------------------------------------------------------------ report
def info(data, name=''):
    kind, payload = detect(data, name)
    L = [f'format: {kind} ({len(data)} bytes)']
    if kind in ('applesingle', 'shrinkit', 'binary2', 'unknown', 'd13'):
        L.append({'applesingle': 'one file with its load address: use the a2-disassemble skill directly',
                  'shrinkit': 'NuFX archive: unpack with CiderPress II / nulib2, then run info on the image inside',
                  'binary2': 'Binary II archive: unpack with CiderPress II',
                  'd13': '13-sector DOS 3.2 disk: convert with CiderPress II (not decoded here)',
                  'unknown': 'not an Apple II disk image this tool knows'}[kind])
        return '\n'.join(L)
    notes = []
    if kind in ('woz1', 'woz2'):
        notes += woz_tracks(payload)[1]
    img, bad = sector_image(data, name)
    if kind in ('nib', 'woz1', 'woz2'):
        tr = sorted({t for t, s, w in bad})
        L.append(f'standard 16-sector decode: {560 - len(bad)}/560 sectors' +
                 (f'; non-standard tracks {tr} (copy protection or unused): boot the image as it is'
                  if bad else ''))
    used = sum(1 for t in range(TRACKS) if any(_sec(img, t, s) != bytes(256) for s in range(16)))
    L.append(f'tracks with data: {used}/35')
    L.append(f'boot: {boot_kind(img)}')
    po = dsk_to_po(img)
    if _dos_vtoc(img):
        cat = dos_catalog(img)
        L.append(f'file system: DOS 3.3, {len(cat)} files')
    elif _prodos_vol(po):
        b = _block(po, 2)
        L.append(f'file system: ProDOS volume /{b[5:5 + (b[4] & 15)].decode()}, {len(prodos_catalog(po))} entries')
    else:
        L.append('file system: none found (custom loader: the game reads its own tracks; use the snapshots, not '
                 'files)')
    L += [f'note: {n}' for n in notes]
    return '\n'.join(L)


def catalog(data, name=''):
    img, _ = sector_image(data, name)
    po = dsk_to_po(img)
    if _dos_vtoc(img):
        return [f"{'*' if e['locked'] else ' '}{e['type']} {e['sectors']:03d} {e['name']}" for e in dos_catalog(img)]
    if _prodos_vol(po):
        return [f"{e['type']:4s} {e['blocks']:5d} {e['eof']:7d} aux ${e['aux']:04X}  {e['name']}"
                for e in prodos_catalog(po)]
    return ['(no DOS 3.3 or ProDOS catalog)']


def get(data, fname, name=''):
    """-> (bytes, load address or None)"""
    img, _ = sector_image(data, name)
    po = dsk_to_po(img)
    if _dos_vtoc(img):
        for e in dos_catalog(img):
            if e['name'] == fname:
                return dos_read(img, e)
    elif _prodos_vol(po):
        for e in prodos_catalog(po):
            if e['name'].upper() == fname.upper():
                return prodos_read(po, e)
    raise KeyError(fname)


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    if len(a) < 2:
        sys.exit(__doc__)
    cmd, path = a[0], a[1]
    data = open(path, 'rb').read()
    if cmd == 'info':
        print(info(data, path))
    elif cmd == 'cat':
        print('\n'.join(catalog(data, path)))
    elif cmd == 'get':
        out, addr = get(data, a[2], path)
        open(a[3], 'wb').write(out)
        print(f'{a[2]}: {len(out)} bytes' + (f', load address ${addr:04X}' if addr is not None else ''))
    elif cmd == 'todsk':
        img, bad = sector_image(data, path)
        open(a[2], 'wb').write(img)
        print(f'{a[2]}: DOS order; {len(bad)} sectors not decoded' + (f', e.g. {bad[:6]}' if bad else ''))
    elif cmd == 'tonib':
        open(a[2], 'wb').write(b''.join(t[:NIB_TRACK].ljust(NIB_TRACK, b'\xff') for t in host_tracks(data, path)))
        print(f'{a[2]}: 35 x {NIB_TRACK} nibbles')
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
