"""Fast loader disk layout (game-independent): a small LOADM program (rt/loader.asm, <loader>.BIN) and a raw data file
(<loader>.DAT) that is contiguous on the disk in ascending tracks (skipping the directory track 17), so the runtime's
disk streamer (rt/dstream.asm) can read it track by track.

    pos = fastload.dat_pos(k)                       # data-file sector k -> (track, sector)
    img, info = fastload.build_disk(dat, rt_block, rt_sectors, boot_sectors, title, ...)

<loader>.BIN sits in the first granules of track 0, <loader>.DAT right after it.  The loader reads the first rt_sectors
sectors of <loader>.DAT (the resident runtime) into rt_block, then jumps to it; the runtime streams the rest.
title: [(row, text, attr)]; text is centred when the column is None.
"""
import os
import sys
import tempfile

from .asm import assemble

HERE = os.path.dirname(os.path.abspath(__file__))
from .paths import RT  # noqa: E402
from .disk import decb  # noqa: E402
from .disk import mkdsk  # noqa: E402

GRAN_SECT, SECSIZE = mkdsk.GRAN_SECT, mkdsk.SECSIZE
GRAN_BYTES = GRAN_SECT * SECSIZE
NBIN = 1                     # granules reserved for <loader>.BIN (track 0, sectors 1-9)

# palette: 0-7 backgrounds, 8-15 foregrounds (attribute: bits 5-3 foreground, bits 2-0 background).
# Values chosen to read alike on an RGB monitor and on composite (MAME's default): white $3F and black $00 are
# the same on both, $26 is orange on both, $34 gold, $1C blue (composite) / teal (RGB).
PALETTE = [0x00, 0x26, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,     # bg: black, orange (progress bar)
           0x3F, 0x26, 0x34, 0x3F, 0x1C, 0x00, 0x00, 0x00]     # fg: white, orange, gold, white, blue


def gran_ts(g):
    """granule -> (track, first sector 1/10)"""
    t = g // 2
    if t >= mkdsk.DIR_TRACK:
        t += 1
    return t, 1 + (g % 2) * GRAN_SECT


def dat_pos(k):
    """data-file sector k -> (track, sector 1-18)"""
    t, s0 = gran_ts(NBIN + k // GRAN_SECT)
    return t, s0 + k % GRAN_SECT


def dat_capacity():
    return (mkdsk.NGRAN - NBIN) * GRAN_BYTES


def _loader(title, tracks, total, rt_block, title_block, bar, workdir):
    row, col, length = bar
    lines = [f'LD_TBLK\tequ\t${title_block:02X}', f'LD_RTBLK\tequ\t${rt_block:02X}',
             f'LD_TOTAL\tequ\t{total}', f'LD_BAR_ROW\tequ\t{row}', f'LD_BAR_COL\tequ\t{col}',
             f'LD_BAR_LEN\tequ\t{length}', 'LD_BARATTR\tequ\t$01', 'LD_ERRATTR\tequ\t$08']
    lines += open(os.path.join(RT, "t128", "loader.asm")).read().splitlines()
    lines.append('ld_pal\tfcb\t' + ','.join(f'${v:02X}' for v in PALETTE))
    lines.append('ld_title')
    for r, text, attr, c in title:
        if c is None:
            c = (40 - len(text)) // 2
        assert '"' not in text and len(text) + c <= 40
        lines += [f'\tfcb\t{r},{c},${attr:02X}', f'\tfcc\t"{text}"', '\tfcb\t0']
    lines.append('\tfcb\t$FF')
    lines.append('ld_trks')
    for t in sorted(tracks):
        lines.append(f'\tfcb\t{t},{len(tracks[t])}')
        lines += [f'\tfcb\t{sec},${blk:02X},${page:02X}' for sec, blk, page in tracks[t]]
    lines.append('\tfcb\t$FF')
    lines.append('ld_end')
    out = os.path.join(workdir, 'loader.bin')
    assemble(lines, out, symbols=out + '.sym')
    code = open(out, 'rb').read()
    assert 0x0E00 + len(code) <= 0x2600, 'loader too big for the graphics pages'
    return code


def _image(files):
    """files: [(name, ext, type, data, granules)] -> RS-DOS disk image (35 tracks)"""
    img = bytearray(b'\xff' * (mkdsk.TRACKS * mkdsk.SECTORS * SECSIZE))
    fat = bytearray(b'\xff' * mkdsk.NGRAN)
    base = mkdsk.DIR_TRACK * mkdsk.SECTORS * SECSIZE
    img[base:base + mkdsk.SECTORS * SECSIZE] = bytes(mkdsk.SECTORS * SECSIZE)
    d = base + 2 * SECSIZE
    img[d:d + 9 * SECSIZE] = b'\xff' * (9 * SECSIZE)
    for i, (name, ext, ftype, data, grans) in enumerate(files):
        assert len(grans) * GRAN_BYTES >= len(data) > 0
        for k, g in enumerate(grans):
            off = mkdsk.gran_offset(g)
            chunk = data[k * GRAN_BYTES:(k + 1) * GRAN_BYTES]
            img[off:off + len(chunk)] = chunk
            if k + 1 < len(grans):
                fat[g] = grans[k + 1]
            else:
                rem = len(data) - k * GRAN_BYTES
                nsec = -(-rem // SECSIZE)
                fat[g] = 0xC0 + nsec
                last = rem - (nsec - 1) * SECSIZE
        ent = bytearray(32)
        ent[0:8] = name.ljust(8).encode()
        ent[8:11] = ext.ljust(3).encode()
        ent[11] = ftype
        ent[13] = grans[0]
        ent[14:16] = last.to_bytes(2, 'big')
        img[d + 32 * i:d + 32 * i + 32] = ent
    img[base + SECSIZE:base + SECSIZE + mkdsk.NGRAN] = fat
    return img, list(fat).count(0xFF)


def build_disk(dat, rt_block, rt_sectors, boot_sectors, title, bin_name='GAME', dat_name='GAME', title_block=0x30,
               bar=(18, 5, 30), workdir=None):
    """dat: the data file (the runtime block's first rt_sectors sectors first).  boot_sectors: all sectors read
    at boot (loader + runtime streamer), for the progress bar.  -> (disk image bytes, info dict)"""
    workdir = workdir or tempfile.mkdtemp(prefix='k3fl_')
    title = [(r, t, a, c) for (r, t, a, *rest) in title for c in [rest[0] if rest else None]]
    assert len(dat) <= dat_capacity(), f'data file too big ({len(dat)} bytes, room {dat_capacity()})'
    tracks = {}
    for k in range(rt_sectors):
        t, s = dat_pos(k)
        tracks.setdefault(t, []).append((s, rt_block, 0x40 + k))
    code = _loader(title, tracks, boot_sectors, rt_block, title_block, bar, workdir)
    binf = decb.segment(0x0E00, code) + decb.postamble(0x0E00)
    assert len(binf) <= NBIN * GRAN_BYTES, f'<loader>.BIN too big ({len(binf)} bytes)'
    open(os.path.join(workdir, f'{bin_name}.BIN'), 'wb').write(binf)
    open(os.path.join(workdir, f'{dat_name}.DAT'), 'wb').write(bytes(dat))
    ndat = -(-len(dat) // GRAN_BYTES)
    img, free = _image([(bin_name, 'BIN', 2, binf, list(range(NBIN))),
                        (dat_name, 'DAT', 1, bytes(dat), list(range(NBIN, NBIN + ndat)))])
    for k in range(-(-len(dat) // SECSIZE)):          # check: every data sector is where dat_pos says
        t, s = dat_pos(k)
        off = (t * mkdsk.SECTORS + s - 1) * SECSIZE
        assert img[off:off + SECSIZE][:len(dat) - k * SECSIZE] == bytes(dat[k * SECSIZE:(k + 1) * SECSIZE])
    return img, {'free': free, 'bin': len(binf), 'dat': len(dat), 'sectors': -(-len(dat) // SECSIZE)}
