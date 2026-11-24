"""Fast loader disk layout (game-independent): a small LOADM program (rt/loader.asm) plus a raw data file read
sector by sector with DSKCON straight into 8K blocks.

    img, info = fastload.build_disk(segments, title, rt_block, bin_name='GAME', title_block=0x36, ...)

segments: [(block, bytes)], each <= 8K, loaded in order (bytes go to the start of the block).
title: [(row, text, attr)]; text is centred when the column is None.
The data file's granules come from the same allocation order mkdsk uses (<loader>.BIN first, then the data file),
so the loader gets a fixed track/sector list.
"""
import os
import sys
import tempfile

from .asm import assemble

HERE = os.path.dirname(os.path.abspath(__file__))
from .paths import RT  # noqa: E402
from .disk import decb  # noqa: E402
from .disk import mkdsk  # noqa: E402

GRAN_BYTES = mkdsk.GRAN_SECT * mkdsk.SECSIZE

# palette: 0-7 backgrounds, 8-15 foregrounds (attribute: bits 5-3 foreground, bits 2-0 background).
# Values chosen to read alike on an RGB monitor and on composite (MAME's default): white $3F and black $00 are
# the same on both, $26 is orange on both, $34 gold, $1C blue (composite) / teal (RGB).
PALETTE = [0x00, 0x26, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,     # bg: black, orange (progress bar)
           0x3F, 0x26, 0x34, 0x3F, 0x1C, 0x00, 0x00, 0x00]     # fg: white, orange, gold, white, blue


def alloc_order(ngran=mkdsk.NGRAN):
    """mkdsk's granule allocation order: 34, 33, 35, 32, ..."""
    order, lo, hi = [], 33, 34
    while lo >= 0 or hi < ngran:
        if hi < ngran:
            order.append(hi)
            hi += 1
        if lo >= 0:
            order.append(lo)
            lo -= 1
    return order


def gran_ts(g):
    """granule -> (track, first sector 1/10)"""
    t = g // 2
    if t >= mkdsk.DIR_TRACK:
        t += 1
    return t, 1 + (g % 2) * mkdsk.GRAN_SECT


def _loader(title, tracks, total, rt_block, title_block, bar, workdir):
    row, col, length = bar
    lines = [f'LD_TBLK\tequ\t${title_block:02X}', f'LD_RTBLK\tequ\t${rt_block:02X}',
             f'LD_TOTAL\tequ\t{total}', f'LD_BAR_ROW\tequ\t{row}', f'LD_BAR_COL\tequ\t{col}',
             f'LD_BAR_LEN\tequ\t{length}', 'LD_BARATTR\tequ\t$01', 'LD_ERRATTR\tequ\t$08']
    lines += open(os.path.join(RT, 't512', 'loader.asm')).read().splitlines()
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


def _tracks(grans, segs):
    """{track: [(sector, block, page)]}: where each data-file sector goes"""
    dest = [(blk, 0x40 + i) for blk, n in segs for i in range(n)]
    out = {}
    for k, (blk, page) in enumerate(dest):
        t, s0 = gran_ts(grans[k // mkdsk.GRAN_SECT])
        out.setdefault(t, []).append((s0 + k % mkdsk.GRAN_SECT, blk, page))
    return out


def build_disk(segments, title, rt_block, bin_name='GAME', dat_name='GAME', title_block=0x36,
               bar=(18, 5, 30), workdir=None):
    """-> (disk image bytes, info dict)"""
    workdir = workdir or tempfile.mkdtemp(prefix='k3fl_')
    title = [(r, t, a, c) for (r, t, a, *rest) in title for c in [rest[0] if rest else None]]
    dat = bytearray()
    segs = []
    for blk, data in segments:
        assert len(data) <= 0x2000 and blk != title_block
        n = -(-len(data) // 256)
        segs.append((blk, n))
        dat += data + bytes(n * 256 - len(data))
    total = len(dat) // 256
    order = alloc_order()
    # <loader>.BIN size does not depend on the granule values: assemble once with placeholders to size it
    ndat = -(-len(dat) // GRAN_BYTES)
    # the loader's size depends on its track table, which depends on where the data file lands (after <loader>.BIN)
    nbin = 1
    for _ in range(5):
        grans = order[nbin:nbin + ndat]
        code = _loader(title, _tracks(grans, segs), total, rt_block, title_block, bar, workdir)
        need = -(-(len(code) + 10) // GRAN_BYTES)
        if need == nbin:
            break
        nbin = need
    else:
        raise RuntimeError('loader size did not settle')
    binf = decb.segment(0x0E00, code) + decb.postamble(0x0E00)
    pb = os.path.join(workdir, f'{bin_name}.BIN')
    pd = os.path.join(workdir, f'{dat_name}.DAT')
    open(pb, 'wb').write(binf)
    open(pd, 'wb').write(bytes(dat))
    img, free = mkdsk.build([(pb, 2, False), (pd, 1, False)])
    # check: the data file is where the loader will read it
    for i, g in enumerate(grans):
        off = mkdsk.gran_offset(g)
        assert img[off:off + GRAN_BYTES][:len(dat) - i * GRAN_BYTES] == bytes(dat[i * GRAN_BYTES:(i + 1) * GRAN_BYTES])
    return img, {'free': free, 'bin': len(binf), 'dat': len(dat), 'sectors': total, 'granules': grans}
