"""Build a port's CoCo 3 512K disk (game-independent).  A port's build512.py runs kit.build512.main(spec).

The port's spec supplies, besides the generator inputs (kit/gen.py): BASE (boot image name, default 'cold'),
STAGES (ordered overlay image names; the native stage loader gets their number 1..n), title(ram) (loading screen
lines), NATIVE_SRC(ram) (the port's native .asm files), START_LABEL (native entry after the runtime init).
See docs/SPEC.md.

Needs the snapshots in <build>/snap (the port's snapshots.py, made from the user's disk).
Everything generated goes to <build>/t512/ and the .dsk.

Physical memory (8K blocks, 512K):
  $00-$07  GIME buffers (page 1: $00-$03, page 2: $04-$07)
  $08-$09  shadows
  $0A-$0B  conversion tables TA/TB
  $10-$15  Apple RAM $0000-$BFFF ($11/$12 = hi-res bitmaps)
  $16-$17  engine code (logical $2000-$5FFF)
  $18-$1D  code banks at $C000: cold, st1..st5
  $1E      video converter (task 1 slot 6)
  $20-$36  compressed blobs, unpacked at start (stage images stay packed until loaded)
  $37      resident runtime (logical $E000)
  $38-$3B  staging for stage loads (free after boot: Disk BASIC's 64K)

Disk: <loader>.BIN (fast loader with a title screen, kit/fastload512.py, kit/fastload128.py) + <loader>.DAT.  Then `LOADM"<loader>":EXEC`.
"""
import argparse
import json
import os
import sys

from .asm import assemble
from . import fastload512 as fastload
from . import gen as kgen

HERE = os.path.dirname(os.path.abspath(__file__))
from .paths import RT as RT_ROOT  # noqa: E402
from .disk import decb, lzss, mkdsk  # noqa: E402,F401
from . import tables as gen_tables  # noqa: E402

RT = os.path.join(RT_ROOT, 't512')
spec = OUT = TITLE = SETS = STAGES = None


def use(port_spec):
    """select the port (its spec module)"""
    global spec, OUT, TITLE, SETS, STAGES
    spec = port_spec
    kgen.use(spec)
    OUT = spec.PORT.out(512)
    TITLE = spec.title(512)
    STAGES = list(spec.STAGES)
    SETS = [getattr(spec, 'BASE', 'cold')] + STAGES


def snap_file(name, ext):
    return os.path.join(spec.SNAP, f'{spec.IMAGES[name]}.{ext}')
LAYOUT = {'BLK_GIME1': 0x00, 'BLK_GIME2': 0x04, 'BLK_SH1': 0x08, 'BLK_TA': 0x0A, 'BLK_TB': 0x0B,
          'BLK_ARAM': 0x10, 'BLK_EC1': 0x16, 'BLK_EC2': 0x17, 'BLK_BANK0': 0x18, 'BLK_VID': 0x1E,
          'BLK_DATA': 0x20, 'BLK_RT': 0x37, 'BLK_STG': 0x38}
DATA_END = 0x37
OVBASE = 0xC003
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
    """I/O handlers the generated code references but the runtime does not define: stubs for the hardware map's
    Ignore / Latch entries; anything else stops the build (kit/hw/iocheck.py)"""
    from .hw import iocheck
    return iocheck.stubs(iocheck.used_symbols(lines_sets), defined, getattr(spec, 'HW_OVERRIDES', {}))


def start_lines(name, pfx, eng):
    """<pfx>START: the snapshot's stack (6502 return frames as native labels), registers, flags, entry"""
    st = spec.entry_state(name)
    if st.get('call') and name != getattr(spec, 'BASE', 'cold'):
        return [f'{pfx}START\tjsr\tx_trap', '\tfdb\t0']        # a call stage is never started (nt_ovl returns)
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


def stage_blob(n):
    """restart stage (nt_load): 256-byte header (count, page numbers), then every page the loader touched, whole.
    Call stage (nt_ovl): the same for the pages the loader wrote completely, then byte runs for the others:
    (addr hi, addr lo, length 1-255, bytes)..., ended by 0, 0, 0 (call_blob)"""
    mem = open(snap_file(STAGES[n - 1], 'bin'), 'rb').read()
    mask = open(snap_file(STAGES[n - 1], 'mask'), 'rb').read()
    if spec.is_call(STAGES[n - 1]):
        return call_blob(mem, mask)
    pages = [p for p in range(0xC0) if any(mask[p * 256:(p + 1) * 256])]
    hdr = bytes([len(pages)] + pages).ljust(256, b'\0')
    return hdr + b''.join(mem[p * 256:(p + 1) * 256] for p in pages)


def call_blob(mem, mask):
    full = [p for p in range(0xC0) if all(mask[p * 256:(p + 1) * 256])]
    out = bytearray(bytes([len(full)] + full).ljust(256, b'\0'))
    for p in full:
        out += mem[p * 256:(p + 1) * 256]
    a = 0
    while a < 0xC000:
        if mask[a] and (a >> 8) not in full:
            e = a
            while e < 0xC000 and mask[e] and e - a < 255 and (e >> 8) not in full:
                e += 1
            out += bytes([a >> 8, a & 0xFF, e - a]) + mem[a:e]
            a = e
        else:
            a += 1
    return bytes(out + bytes(3))


def build(out_dsk, verbose=True):
    say = print if verbose else (lambda *a: None)
    sdir = os.path.join(OUT, 'src')
    P = kgen.build(sdir)
    eng = set(P.engine.insns)
    bdir = os.path.join(OUT, 'bin')
    os.makedirs(bdir, exist_ok=True)

    # ---- video block
    vid = os.path.join(bdir, 'vid.bin')
    vid_lines = lambda rs: (['\tpragma\tnoautobranchlength'] + layout_lines() +
                            ['VVPAGE\tequ\t$FE', 'VR_MAX\tequ\t24'] + rs + src(os.path.join(RT, 'vid.asm')) + rowtab_lines())

    # ---- main image: engine $2000-$5FFF + runtime $E000
    ovt = [f'OVBASE\tequ\t${OVBASE:04X}'] + src(os.path.join(sdir, 'ovt.inc'))
    ovl_src = {n: src(os.path.join(sdir, f'ovl_{n}.asm')) for n in SETS}
    eng_src = src(os.path.join(sdir, 'engine.asm'))
    rt_src = src(os.path.join(RT, 'rt.asm'))
    nat_src = []
    for f in spec.NATIVE_SRC(512):
        nat_src += src(f)
    if os.path.exists(os.path.join(sdir, 'hw_inst.asm')):     # accepted hardware idioms (spec HW_IDIOMS)
        nat_src += [ln for ln in src(os.path.join(sdir, 'hw_inst.asm')) if ln.strip()]
    xiy = src(os.path.join(sdir, 'xiy.asm'))
    import re
    from .hw import iocheck
    for f in iocheck.runtime_extras(iocheck.used_symbols([eng_src] + list(ovl_src.values())),
                                    getattr(spec, 'HW_OVERRIDES', {})):
        rt_src = rt_src + src(os.path.join(RT_ROOT, f))     # handlers only some ports use (kit/hw/apple2.py)
    defined = set(re.findall(r'^(io_\w+)', '\n'.join(rt_src), re.M))
    iod = io_defaults([eng_src] + list(ovl_src.values()), defined)

    def main_lines(blobs, stg):
        return (['\tpragma\tnoautobranchlength', '\tsetdp\t0', 'XMMU\tequ\t1', f'NSTAGES\tequ\t{len(STAGES)}'] +
                (['HRACC\tequ\t1'] if getattr(spec, 'HIRES_ACCESS', False) else []) + layout_lines() + ovt +
                ['vid_conv\tequ\t$C000', 'vid_scroll\tequ\t$C003', 'RT_START_GAME\tequ\t' + spec.START_LABEL, '\torg\t$2000'] + eng_src +
                ['eng_end'] + rt_src + nat_src + xiy + iod +
                ['rt_blobs'] + [f'\tfcb\t${b:02X}\n\tfdb\t${o:04X}\n\tfcb\t${d:02X}' for b, o, d in blobs] +
                ['\tfcb\t0', 'stg_tab'] + [f'\tfcb\t${b:02X}\n\tfdb\t${o:04X}\n\tfcb\t${k:02X}' for b, o, k in stg] +
                ['rt_end'])

    def assemble_main(blobs, stg):
        mb, ms = os.path.join(bdir, 'main.bin'), os.path.join(bdir, 'main.sym')
        assemble(main_lines(blobs, stg), mb, fmt='decb', symbols=ms, listing=os.path.join(bdir, 'main.lst'))
        return read_decb(mb), symbols(ms)

    nblobs = 1 + 1 + 1 + len(SETS) + 1         # tables, video, engine, banks, cold RAM
    mem, sym = assemble_main([(0, 0, 0)] * nblobs, [(0, 0, 0)] * len(STAGES))
    say(f'engine ${0x2000:04X}-${sym["eng_end"] - 1:04X} ({sym["eng_end"] - 0x2000} bytes), '
        f'runtime $E000-${sym["rt_end"] - 1:04X} ({sym["rt_end"] - 0xE000} bytes)')
    assert sym['eng_end'] <= 0x6000, 'engine does not fit in $2000-$5FFF'
    assemble(vid_lines([f'{k}\tequ\t${sym[k]:04X}' for k in ('vr_n1', 'vr_n2', 'vr_l1', 'vr_l2', 'vr_rfon', 'vr_rflag')]), vid,
             listing=os.path.join(bdir, 'vid.lst'))
    vid_b = open(vid, 'rb').read()
    assert len(vid_b) <= 0x2000, 'video block too big'
    assert sym['rt_end'] <= RT_LIMIT, f'runtime overlaps the constant page (${sym["rt_end"]:04X})'

    # ---- code banks
    banks = {}
    exp = [f'{k}\tequ\t${v:04X}' for k, v in sorted(sym.items())]
    for n in SETS:
        pfx = n.upper() + '_'
        b = os.path.join(bdir, f'bank_{n}.bin')
        assemble(['\tpragma\tnoautobranchlength', '\tsetdp\t0'] + exp + ['\torg\t$C000', f'\tjmp\t{pfx}START']
                 + ovl_src[n] + start_lines(n, pfx, eng) + [f'{pfx}END'], b, symbols=b + '.sym')
        banks[n] = open(b, 'rb').read()
        assert len(banks[n]) <= 0x2000, f'bank {n} too big'
        say(f'bank {n}: {len(banks[n])} bytes')

    # ---- blobs
    ta, tb = gen_tables.build()
    engine_b = bytes(mem.get(a, 0) for a in range(0x2000, sym['eng_end']))
    cold = open(snap_file(SETS[0], 'bin'), 'rb').read()[:0xC000]
    boot_blobs = [(bytes(ta) + bytes(tb), LAYOUT['BLK_TA']), (vid_b, LAYOUT['BLK_VID']),
                  (engine_b, LAYOUT['BLK_EC1'])] + \
        [(banks[n], LAYOUT['BLK_BANK0'] + k) for k, n in enumerate(SETS)] + [(cold, LAYOUT['BLK_ARAM'])]
    data = bytearray()
    blobs, stg = [], []
    for raw, dest in boot_blobs:
        z = lzss.compress(raw)
        blobs.append((LAYOUT['BLK_DATA'] + len(data) // 0x2000, len(data) % 0x2000, dest))
        data += z
    for n in range(1, len(STAGES) + 1):
        z = lzss.compress(stage_blob(n))
        stg.append((LAYOUT['BLK_DATA'] + len(data) // 0x2000, len(data) % 0x2000, LAYOUT['BLK_BANK0'] + n))
        data += z
    say(f'compressed data {len(data)} bytes, blocks ${LAYOUT["BLK_DATA"]:02X}-'
        f'${LAYOUT["BLK_DATA"] + (len(data) - 1) // 0x2000:02X}')
    assert LAYOUT['BLK_DATA'] + (len(data) - 1) // 0x2000 < DATA_END, 'data area full'
    mem2, sym2 = assemble_main(blobs, stg)
    assert sym2 == sym and all(mem2.get(a) == mem.get(a) for a in range(0x2000, sym['eng_end'])), \
        'second pass moved code'
    rt_b = bytes(mem2.get(a, 0) for a in range(0xE000, sym['rt_end']))

    # ---- disk: fast loader (<loader>.BIN, title screen) + raw data file (<loader>.DAT) read with DSKCON
    segments = [(LAYOUT['BLK_DATA'] + k, bytes(data[k * 0x2000:(k + 1) * 0x2000]))
                for k in range(-(-len(data) // 0x2000))] + [(LAYOUT['BLK_RT'], rt_b)]
    img, info = fastload.build_disk(segments, TITLE, LAYOUT['BLK_RT'], bin_name=spec.PORT.loader, dat_name=spec.PORT.loader,
                                    bar=(21, 5, 30), workdir=OUT)
    open(out_dsk, 'wb').write(img)
    say(f"{out_dsk}: {spec.PORT.loader}.BIN {info['bin']} bytes + {spec.PORT.loader}.DAT {info['dat']} bytes ({info['sectors']} sectors), "
        f"{info['free']} granules free")
    json.dump({'blobs': blobs, 'stg': stg, 'symbols': {k: v for k, v in sym.items()}},
              open(os.path.join(OUT, 'build.json'), 'w'))
    return sym


def main(port_spec, argv=None):
    use(port_spec)
    ap = argparse.ArgumentParser()
    ap.add_argument('-o', default=spec.PORT.dsk(512))
    a = ap.parse_args(argv)
    build(a.o)

