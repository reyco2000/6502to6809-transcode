"""Build a port's CoCo 3 128K disk (game-independent).  A port's build128.py runs kit.build128.main(spec).

The port's spec supplies, besides the generator inputs (kit/gen.py): BASE (boot image name, default 'cold'),
STAGES (ordered overlay image names; the native stage loader gets their number 1..n), title(ram) (loading screen
lines), NATIVE_SRC(ram) (the port's native .asm files), START_LABEL (native entry after the runtime init),
BLOCKB_RESERVE (bytes kept at the top of code block B for the port's natives: equate BLOCKB_TOP).
See docs/SPEC.md.

Needs the snapshots in <build>/snap (the port's snapshots.py, made from the user's disk).
Everything generated goes to <build>/t128/ and the .dsk.

Physical memory (8K blocks, 128K = $30-$3F; the same blocks exist on a 512K machine):
  $30-$33  the single GIME buffer (30720 bytes); the video converter sits in the tail of $33 (task 1 slot 6)
  $34      the shadow (also the disk track buffer while loading)
  $35-$36  conversion tables TA/TB
  $37-$38  engine code (logical $2000-$5FFF); the current stage's code follows it in $38 (OVORG), then nb_sh
  $39-$3E  Apple RAM $0000-$BFFF ($3A/$3B = hi-res bitmaps)
  $3F      resident runtime (logical $E000), with the disk streamer
Nothing is kept compressed in RAM: the runtime streams the boot data, the cold image (again on a reboot) and
each stage straight from the disk (rt/t128/dstream.asm), while the screen keeps the last frame.

Disk: <loader>.BIN (fast loader with a title screen: reads the runtime, kit/fastload512.py, kit/fastload128.py) + <loader>.DAT.  Then `LOADM"<loader>":EXEC`.
"""
import argparse
import json
import os
import sys

from .asm import assemble
from . import fastload128 as fastload
from . import gen as kgen

HERE = os.path.dirname(os.path.abspath(__file__))
from .paths import RT as RT_ROOT  # noqa: E402
from .disk import decb, lzss, mkdsk  # noqa: E402,F401
from . import tables as gen_tables  # noqa: E402

RT = os.path.join(RT_ROOT, 't128')
spec = OUT = TITLE = SETS = STAGES = None


def use(port_spec):
    """select the port (its spec module)"""
    global spec, OUT, TITLE, SETS, STAGES
    spec = port_spec
    kgen.use(spec)
    OUT = spec.PORT.out(128)
    TITLE = spec.title(128)
    STAGES = list(spec.STAGES)
    SETS = [getattr(spec, 'BASE', 'cold')] + STAGES
    global BLOCKB_TOP
    BLOCKB_TOP = 0xE000 - getattr(spec, 'BLOCKB_RESERVE', 0)   # top of code block B left to the port


def snap_file(name, ext):
    return os.path.join(spec.SNAP, f'{spec.IMAGES[name]}.{ext}')
LAYOUT = {'BLK_GIME1': 0x30, 'BLK_VID': 0x33, 'BLK_SH1': 0x34, 'BLK_TA': 0x35, 'BLK_TB': 0x36,
          'BLK_EC1': 0x37, 'BLK_EC2': 0x38, 'BLK_ARAM': 0x39, 'BLK_RT': 0x3F, 'DS_TBLK': 0x30, 'DS_BARATTR': 0x01}
assert LAYOUT['BLK_SH1'] == LAYOUT['BLK_GIME1'] + 4 and LAYOUT['BLK_VID'] == LAYOUT['BLK_GIME1'] + 3
assert LAYOUT['BLK_ARAM'] + 6 == LAYOUT['BLK_RT'] and LAYOUT['BLK_TB'] == LAYOUT['BLK_TA'] + 1
BAR = (21, 5, 30)            # progress bar: row, column, length
# --vid plain: the converter writes straight to the screen (no log + commit; vid.asm VDIRECT)
VDIRECT_EQU = ['VDIRECT\tequ\t1']
VID_ORG = 0xD800             # converter: tail of block BLK_VID, task 1 slot 6
RT_LIMIT = 0xFE00


def layout_lines():
    return [f'{k}\tequ\t${v:02X}' for k, v in LAYOUT.items()]


def src(path):
    return open(path).read().splitlines()


def row_addr(y):
    return (y & 7) * 0x400 + ((y >> 3) & 7) * 0x80 + (y >> 6) * 0x28


def rowtab_lines():
    out = ['rowtab']
    for y in range(192):
        d = y * 160 + 10
        out += [f'\tfdb\t${0x2000 + row_addr(y):04X}', f'\tfcb\t{d // 8192}', f'\tfdb\t${0x4000 + d % 8192:04X}']
    return out


def read_decb(path):
    d = open(path, 'rb').read()
    mem, i = {}, 0
    while i < len(d):
        t, n, a = d[i], d[i + 1] << 8 | d[i + 2], d[i + 3] << 8 | d[i + 4]
        if t == 0xFF:
            break
        for k in range(n):
            mem[a + k] = d[i + 5 + k]
        i += 5 + n
    return mem


def symbols(path):
    out = {}
    for ln in open(path):
        p = ln.split()
        if len(p) == 3 and p[1] == 'EQU':
            out[p[0]] = int(p[2].lstrip('$'), 16)
    return out


def io_defaults(lines_sets, defined):
    """I/O handlers the generated code references but the runtime does not define: reads give 0, writes do
    nothing"""
    import re
    used = set()
    for lines in lines_sets:
        for ln in lines:
            used |= set(re.findall(r'\bio_(?:rdx?|wrx?)_C0[0-9A-F]{2}\b', ln))
    out = []
    for s in sorted(used - defined):
        if s.startswith('io_rd'):
            out += [f'{s}\tpshs\tcc', '\tclr\tIOV', '\tpuls\tcc,pc']
        else:
            out += [f'{s}\trts']
    return out


def start_lines(name, pfx, eng):
    """<pfx>START: the snapshot's stack (6502 return frames as native labels), registers, flags, entry"""
    st = spec.entry_state(name)
    lab = lambda a: f'E{a:04X}' if a in eng else f'{pfx}{a:04X}'
    s = st['s']
    out = [f'{pfx}START', f'\tlds\t#${0x100 + s + 1:04X}']
    for k, r in enumerate(st.get('ret_chain', [])):
        out += [f'\tldx\t#{lab((r + 1) & 0xFFFF)}', f'\tstx\t${0x100 + s + 1 + 2 * k:04X}']
    p = st['p']
    cc = (0x08 if p & 0x80 else 0) | (0x04 if p & 0x02 else 0) | (0x01 if p & 0x01 else 0) | 0x40  # FIRQ off
    out += ['\tclr\tRYW', f'\tlda\t#${st["y"]:02X}', '\tsta\tRY', f'\tlda\t#${cc:02X}', '\tpshs\ta',
            f'\tlda\t#${st["a"]:02X}', f'\tldb\t#${st["x"]:02X}', '\tpuls\tcc', f'\tjmp\t{lab(st["pc"])}']
    return out


def stage_runs(n):
    """[(page, bytes)]: the pages the real loader writes for stage n, as contiguous runs"""
    if spec.is_call(STAGES[n - 1]):
        raise SystemExit(f'stage {STAGES[n - 1]} is a call stage (loader returns to the game): the 128K target does '
                         'not stream call stages yet (docs/MEMORY.md); build the 512K target')
    mem = open(snap_file(STAGES[n - 1], 'bin'), 'rb').read()
    mask = open(snap_file(STAGES[n - 1], 'mask'), 'rb').read()
    runs = []
    for p in range(0xC0):
        if any(mask[p * 256:(p + 1) * 256]):
            if runs and runs[-1][1] == p:
                runs[-1][1] = p + 1
            else:
                runs.append([p, p + 1])
    return [(p0, mem[p0 * 256:p1 * 256]) for p0, p1 in runs]


def aram_dest(addr):
    """Apple address -> (block, $8000 + offset) for the streamer"""
    return LAYOUT['BLK_ARAM'] + (addr >> 13), 0x8000 + (addr & 0x1FFF)


def file_lines(label, pos, run_lists):
    """streamer file: fcb track,sector / per run: fcb block / fdb dest ... fcb 0 (one 0 per run list)"""
    out = [label, f'\tfcb\t{pos[0]},{pos[1]}']
    for runs in run_lists:
        out += [f'\tfcb\t${b:02X}\n\tfdb\t${d:04X}' for b, d in runs] + ['\tfcb\t0']
    return out


def build(out_dsk, verbose=True, plain=False):
    """plain: the converter writes straight to the screen instead of log + commit (vid.asm VDIRECT)"""
    say = print if verbose else (lambda *a: None)
    sdir = os.path.join(OUT, 'src')
    P = kgen.build(sdir)
    eng = set(P.engine.insns)
    bdir = os.path.join(OUT, 'bin_plain' if plain else 'bin')
    os.makedirs(bdir, exist_ok=True)

    # ---- video converter
    vid = os.path.join(bdir, 'vid.bin')
    vid_lines = lambda rs: (['\tpragma\tnoautobranchlength'] + layout_lines() +
                            ['VVPAGE\tequ\t$FE', 'VR_MAX\tequ\t32'] + VDIRECT_EQU * plain + rs + src(os.path.join(RT, 'vid.asm')) + ['vid_end'])

    # ---- main image: engine $2000-$5FFF + runtime $E000
    ovt_inc = src(os.path.join(sdir, 'ovt.inc'))
    ovl_src = {n: src(os.path.join(sdir, f'ovl_{n}.asm')) for n in SETS}
    eng_src = src(os.path.join(sdir, 'engine.asm'))
    rt_src = src(os.path.join(RT, 'rt.asm')) + src(os.path.join(RT, 'dstream.asm'))
    nat_src = []
    for f in spec.NATIVE_SRC(128):
        nat_src += src(f)
    xiy = src(os.path.join(sdir, 'xiy.asm'))
    import re
    defined = set(re.findall(r'^(io_\w+)', '\n'.join(rt_src), re.M))
    iod = io_defaults([eng_src] + list(ovl_src.values()), defined)
    stg_runs = {n: stage_runs(n) for n in range(1, len(STAGES) + 1)}

    def main_lines(files, ovorg, total):
        """files: {'cold': (pos, [cold runs], [boot runs]), n: (pos, [runs])} with runs = [(block, dest)]"""
        tab = file_lines('ds_cold', files['cold'][0], files['cold'][1:]) + \
            ['ds_stg\tfdb\t' + ','.join(f'ds_st{n}' for n in range(1, len(STAGES) + 1))]
        for n in range(1, len(STAGES) + 1):
            tab += file_lines(f'ds_st{n}', files[n][0], files[n][1:])
        return (['\tpragma\tnoautobranchlength', '\tsetdp\t0', 'XMMU\tequ\t1', f'NSTAGES\tequ\t{len(STAGES)}'] + layout_lines() +
                [f'OVORG\tequ\t${ovorg:04X}', f'OVBASE\tequ\t${ovorg + 3:04X}', f'BLOCKB_TOP\tequ\t${BLOCKB_TOP:04X}',
                 f'DS_TOTAL\tequ\t{total}', f'DS_BARLEN\tequ\t{BAR[2]}'] + ovt_inc +
                [f'vid_conv\tequ\t${VID_ORG:04X}', f'vid_scroll\tequ\t${VID_ORG + 3:04X}', 'RT_START_GAME\tequ\t' + spec.START_LABEL,
                 '\torg\t$2000'] + eng_src +
                ['eng_end'] + rt_src + nat_src + xiy + iod + rowtab_lines() + tab + ['rt_end'])

    def assemble_main(files, ovorg, total=1):
        mb, ms = os.path.join(bdir, 'main.bin'), os.path.join(bdir, 'main.sym')
        assemble(main_lines(files, ovorg, total), mb, fmt='decb', symbols=ms, listing=os.path.join(bdir, 'main.lst'))
        return read_decb(mb), symbols(ms)

    def files_of(pos, ovorg):
        code = (LAYOUT['BLK_EC2'], 0x8000 + ovorg - 0xC000)
        f = {'cold': (pos['cold'], [aram_dest(0), code],
                      [(LAYOUT['BLK_TA'], 0x8000), (LAYOUT['BLK_EC1'], 0x8000),
                       (LAYOUT['BLK_VID'], 0x8000 + VID_ORG - 0xC000)])}
        for n in range(1, len(STAGES) + 1):
            f[n] = (pos[n], [aram_dest(p0 * 256) for p0, _ in stg_runs[n]] + [code])
        return f

    nopos = {k: (0, 0) for k in ['cold'] + list(range(1, len(STAGES) + 1))}
    _, sym0 = assemble_main(files_of(nopos, 0xC000), 0xC000)
    ovorg = 0xC000 + ((sym0['eng_end'] - 0x4000 + 15) & ~15)    # stage code: code block B after the engine
    assert ovorg >= 0xC000, 'engine shorter than one block (OVORG layout assumes it spills into block B)'
    mem, sym = assemble_main(files_of(nopos, ovorg), ovorg)
    assert sym['eng_end'] == sym0['eng_end'], 'engine size depends on OVORG'
    say(f'engine ${0x2000:04X}-${sym["eng_end"] - 1:04X} ({sym["eng_end"] - 0x2000} bytes), '
        f'runtime $E000-${sym["rt_end"] - 1:04X} ({sym["rt_end"] - 0xE000} bytes, {RT_LIMIT - sym["rt_end"]} free)')
    assert sym['eng_end'] <= 0x6000, 'engine does not fit in $2000-$5FFF'
    assemble(vid_lines([f'{k}\tequ\t${sym[k]:04X}' for k in ('vr_n1', 'vr_n2', 'vr_l1', 'vr_l2', 'vr_rflag', 'rowtab', 'vm_rect', 'vr_y0', 'vr_c0', 'vr_clr', 'vbl_over', 'rt_tick', 'vr_merge', 'vr_sort')]), vid,
             listing=os.path.join(bdir, 'vid.lst'))
    vid_b = open(vid, 'rb').read()
    assert len(vid_b) <= 0xE000 - VID_ORG, f'converter too big ({len(vid_b)} bytes, room {0xE000 - VID_ORG})'
    say(f'converter {len(vid_b)} bytes at ${VID_ORG:04X} (room {0xE000 - VID_ORG})')
    assert sym['rt_end'] <= RT_LIMIT, f'runtime overlaps the constant page (${sym["rt_end"]:04X})'

    # ---- code banks (copied into code block B at OVORG by the streamer)
    banks = {}
    exp = [f'{k}\tequ\t${v:04X}' for k, v in sorted(sym.items())]
    for n in SETS:
        pfx = n.upper() + '_'
        b = os.path.join(bdir, f'bank_{n}.bin')
        assemble(['\tpragma\tnoautobranchlength', '\tsetdp\t0'] + exp + [f'\torg\t${ovorg:04X}', f'\tjmp\t{pfx}START']
                 + ovl_src[n] + start_lines(n, pfx, eng) + [f'{pfx}END'], b, symbols=b + '.sym')
        banks[n] = open(b, 'rb').read()
        assert ovorg + len(banks[n]) <= BLOCKB_TOP, f'bank {n} too big ({len(banks[n])}, room {BLOCKB_TOP - ovorg})'
        say(f'bank {n}: {len(banks[n])} bytes (room {BLOCKB_TOP - ovorg} at ${ovorg:04X})')

    # ---- data file: runtime (raw, read by <loader>.BIN), then the streamer files, each from a sector boundary
    ta, tb = gen_tables.build()
    engine_b = bytes(mem.get(a, 0) for a in range(0x2000, sym['eng_end']))
    cold = open(snap_file(SETS[0], 'bin'), 'rb').read()[:0xC000]
    rt_len = sym['rt_end'] - 0xE000
    rt_sectors = -(-rt_len // 256)
    contents = {'cold': [cold, banks['cold'], bytes(ta) + bytes(tb), engine_b, vid_b]}
    for n in range(1, len(STAGES) + 1):
        contents[n] = [r for _, r in stg_runs[n]] + [banks[SETS[n]]]
    zdata, pos, ends = {}, {}, {}
    sec = rt_sectors
    for k in ['cold'] + list(range(1, len(STAGES) + 1)):
        z = [lzss.compress(r) for r in contents[k]]
        assert all(lzss.decompress(zz) == r for zz, r in zip(z, contents[k]))
        zdata[k] = b''.join(z)
        pos[k] = fastload.dat_pos(sec)
        ends[k] = sec * 256 + len(zdata[k])              # byte after the file
        say(f'file {k}: {len(zdata[k])} bytes at track {pos[k][0]} sector {pos[k][1]} '
            f'(runs {", ".join(str(len(r)) for r in contents[k])})')
        sec += -(-len(zdata[k]) // 256)
    # sectors the runtime reads at boot: from the cold file's first sector to the end of the track of its last byte
    first, last = rt_sectors, (ends['cold'] - 1) // 256
    boot_read = sum(1 for k in range(first, last + 1)) + (18 - fastload.dat_pos(last)[1])
    total = rt_sectors + boot_read
    mem2, sym2 = assemble_main(files_of(pos, ovorg), ovorg, total)
    sym2.pop('DS_TOTAL', None)
    sym.pop('DS_TOTAL', None)
    assert sym2 == sym and all(mem2.get(a) == mem.get(a) for a in range(0x2000, sym['eng_end'])), \
        'second pass moved code'
    rt_b = bytes(mem2.get(a, 0) for a in range(0xE000, sym['rt_end']))
    dat = bytearray(rt_b.ljust(rt_sectors * 256, b'\0'))
    for k in ['cold'] + list(range(1, len(STAGES) + 1)):
        dat += zdata[k] + bytes(-len(zdata[k]) % 256)

    # ---- disk: fast loader (<loader>.BIN, title screen) + data file (<loader>.DAT)
    img, info = fastload.build_disk(bytes(dat), LAYOUT['BLK_RT'], rt_sectors, total, TITLE,
                                    bin_name=spec.PORT.loader, dat_name=spec.PORT.loader,
                                    title_block=LAYOUT['DS_TBLK'], bar=BAR, workdir=OUT)
    open(out_dsk, 'wb').write(img)
    say(f"{out_dsk}: {spec.PORT.loader}.BIN {info['bin']} bytes + {spec.PORT.loader}.DAT {info['dat']} bytes ({info['sectors']} sectors, "
        f"boot reads {total}), {info['free']} granules free")
    json.dump({'pos': {str(k): v for k, v in pos.items()}, 'symbols': {k: v for k, v in sym.items()}},
              open(os.path.join(OUT, 'build.json'), 'w'))
    return sym


def main(port_spec, argv=None):
    use(port_spec)
    ap = argparse.ArgumentParser()
    ap.add_argument('-o', default=spec.PORT.dsk(128))
    ap.add_argument('--vid', choices=['stage', 'plain', 'both'], default='stage',
                    help='stage: log + commit (default); plain: straight to the screen (-o gets _plain); both')
    a = ap.parse_args(argv)
    if a.vid in ('stage', 'both'):
        build(a.o)
    if a.vid in ('plain', 'both'):
        root, ext = os.path.splitext(a.o)
        build(root + '_plain' + ext, verbose=a.vid == 'plain', plain=True)

