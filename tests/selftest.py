"""Kit self-test (no game data needed):  python3 -m tests.selftest

  * 6502 decoder: 151 documented opcodes
  * LZSS round trip; RS-DOS image build
  * hi-res -> GIME tables build
  * a tiny 6502 routine translated by xc65 and run against the 6502 reference in cc3sim
  * the scaffolder writes an importable port
For a full regression run the reference port: ports/karateka (needs the user's disk).
"""
import os
import random
import sys
import tempfile

from kit.opcodes import OPS, decode
from kit.disk import lzss, mkdsk
from kit import tables, newport
from kit.disk import a2disk


def check(name, ok):
    print(f'{"ok  " if ok else "FAIL"} {name}')
    return ok


def main():
    good = True
    good &= check('opcodes: 151 documented', len(OPS) == 151)
    rnd = random.Random(1)
    data = bytes(rnd.choice(b'abcabcabd\x00\xff') for _ in range(20000))
    good &= check('lzss round trip', lzss.decompress(lzss.compress(data)) == data)
    ta, tb = tables.build()
    good &= check('GIME tables 8K each', len(ta) == 8192 and len(tb) == 8192)
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'A.BIN')
        open(p, 'wb').write(bytes([0, 0, 4, 0x0E, 0, 1, 2, 3, 4, 0xFF, 0, 0, 0x0E, 0]))
        img, free = mkdsk.build([(p, 2, False)])
        good &= check('RS-DOS image', len(img) == 35 * 18 * 256 and free > 60)
        d = os.path.join(td, 'demo-coco3')
        newport.scaffold('demo', d, disk='/nonexistent.dsk')
        sys.path.insert(0, d)
        import demo.spec as ds              # noqa: E402
        good &= check('scaffolded port imports', ds.PORT.name == 'demo' and ds.STAGES == [])
    good &= check('xc65 + cc3sim difftest', _difftest())
    good &= _formats()
    print('ALL OK' if good else 'FAILURES')
    sys.exit(0 if good else 1)


def _difftest():
    """small routines translated and compared with the 6502 on random inputs"""
    sum_tab = bytes([0xA0, 0x00,             # $0800 LDY #0
                     0x18,                   # CLC
                     0x71, 0x10,             # L: ADC ($10),Y
                     0xC8,                   # INY
                     0xC0, 0x08,             # CPY #8
                     0xD0, 0xF9,             # BNE L
                     0xAA,                   # TAX
                     0x60])                  # RTS

    def ptr_setup(rnd, ram):
        ram[0x10], ram[0x11] = 0x00, rnd.choice([0x30, 0x60, 0x90])

    # (zp,X) and operand SMC on abs / abs,X / abs,Y (the operands are patched: random RAM addresses)
    smc = bytes([0x8A,                       # $0800 TXA
                 0x29, 0x0E,                 # AND #$0E
                 0xAA,                       # TAX
                 0xA1, 0x20,                 # LDA ($20,X)
                 0x59, 0x00, 0x90,           # $0806 EOR $9000,Y   (operand SMC)
                 0x8D, 0x00, 0x90,           # $0809 STA $9000     (operand SMC)
                 0x1D, 0x00, 0x90,           # $080C ORA $9000,X   (operand SMC)
                 0x60])

    def smc_setup(rnd, ram):
        for z in range(0x20, 0x30):
            ram[z] = rnd.randrange(256) if z % 2 == 0 else rnd.randrange(0x30, 0x90)
        for o in (0x807, 0x80A, 0x80D):
            ram[o], ram[o + 1] = rnd.randrange(256), rnd.randrange(0x30, 0x8F)

    # JMP with a patched operand (SMC_JMP): a jump into an ASL chain, like a computed shift count
    jtab = bytes([0x4C, 0x05, 0x08,         # $0800 JMP $0805 (operand patched: $0803/$0804/$0805)
                  0x0A, 0x0A,               # $0803 ASL A / $0804 ASL A
                  0x0A,                     # $0805 ASL A
                  0x60])

    def jtab_setup(rnd, ram):
        ram[0x801], ram[0x802] = rnd.choice([3, 4, 5]), 0x08

    # opcode SMC (SMC_OPS): AND/ORA/EOR #imm and BPL/BMI chosen by a patched opcode byte
    ops = bytes([0x29, 0x8F,                # $0800 AND #$8F   (opcode patched: AND / ORA / EOR)
                 0x10, 0x02,                # $0802 BPL $0806  (opcode patched: BPL / BMI)
                 0xA2, 0x01,                # LDX #1
                 0x60])                     # $0806 RTS

    def ops_setup(rnd, ram):
        ram[0x800], ram[0x802] = rnd.choice([0x29, 0x09, 0x49]), rnd.choice([0x10, 0x30])

    # BVC/BVS right after ADC/SBC (the 6809 sets V like the 6502 there)
    vflag = bytes([0x65, 0x10,              # $0800 ADC $10
                   0x50, 0x02,              # BVC $0806
                   0xE6, 0x11,              # INC $11
                   0xE5, 0x12,              # $0806 SBC $12
                   0x70, 0x02,              # BVS $080C
                   0xE6, 0x13,              # INC $13
                   0x60])                   # $080C RTS

    def v_setup(rnd, ram):
        for z in (0x10, 0x11, 0x12, 0x13):
            ram[z] = rnd.randrange(256)

    # zp,X that wraps past $FF (the profile flags it): stays in zero page
    zwrap = bytes([0xB5, 0xF0,              # $0800 LDA $F0,X
                   0x95, 0x80,              # STA $80,X
                   0x60])

    def zw_setup(rnd, ram):
        pass

    return (_difftest1(zwrap, zw_setup, (), zp_wrap={0x800, 0x802}) and
            _difftest1(vflag, v_setup, ()) and
            _difftest1(ops, ops_setup, (), smc_ops={0x800: [0x29, 0x09, 0x49], 0x802: [0x10, 0x30]}) and
            _difftest1(sum_tab, ptr_setup, ()) and
            _difftest1(smc, smc_setup, (0x806, 0x809, 0x80C)) and
            _difftest1(jtab, jtab_setup, (), {0x800: [0x803, 0x804, 0x805]}))


def _difftest1(code, setup, smc_imm, smc_jmp=None, smc_ops=None, zp_wrap=()):
    from kit.discover import discover
    from kit.xc65 import Ctx, Live, translate, xiy_helpers
    from kit.difftest import Harness, reference, compare
    mem = bytearray(0x10000)
    mem[0x800:0x800 + len(code)] = code
    c = discover(mem, [0x800], {}, code_ranges=((0x800, 0x8FF),), outside=lambda a: not 0x800 <= a <= 0x8FF,
                 smc_jmp=smc_jmp, smc_ops=smc_ops)
    ctx = Ctx(ext_ret={0x800: 0}, ext_live=lambda a: 0x3F, roots={0x800}, smc_imm=smc_imm, zp_wrap=zp_wrap)
    lines, em = translate(c, 'T', ctx, live=Live(c, ctx))
    H = Harness(lines, {}, extra_lines=xiy_helpers(em.ptrs) + ['x_trap\tbra\tx_trap'])
    rnd = random.Random(2)
    for _ in range(50):
        ram = bytearray(rnd.randrange(256) for _ in range(0xC000))
        ram[0x800:0x800 + len(code)] = code
        setup(rnd, ram)
        a, x, y, p = (rnd.randrange(256) for _ in range(4))
        ref = reference(ram, 0x800, a, x, y, p, set(c.insns), {})
        got = H.run(ram, 'T0800', a, x, y, p)
        if compare(ref, got, 0x3F):
            print('   ', compare(ref, got, 0x3F))
            return False
    return True


def _woz2(tracks):
    """minimal WOZ2 image from nibble tracks (each nibble = 8 bits + 2 sync zero bits after FF gaps)"""
    import struct
    blobs, tmap = [], bytearray([0xFF] * 160)
    for t, tr in enumerate(tracks):
        bits = []
        for n in tr:
            bits += [(n >> (7 - k)) & 1 for k in range(8)] + ([0, 0] if n == 0xFF else [])
        raw = bytearray((len(bits) + 7) // 8)
        for i, b in enumerate(bits):
            raw[i >> 3] |= b << (7 - (i & 7))
        blobs.append((raw + bytes(-len(raw) % 512), len(bits)))
        tmap[t * 4] = t
    info = bytes([2, 1, 0, 0, 0]) + b'selftest'.ljust(32) + bytes(23)
    trk, data, blk = bytearray(1280), bytearray(), 3
    for i, (raw, nb) in enumerate(blobs):
        struct.pack_into('<HHI', trk, i * 8, blk, len(raw) // 512, nb)
        data += raw
        blk += len(raw) // 512
    out = bytearray(b'WOZ2\xff\x0a\x0d\x0a' + bytes(4))
    out += b'INFO' + struct.pack('<I', 60) + info
    out += b'TMAP' + struct.pack('<I', 160) + tmap
    out += b'TRKS' + struct.pack('<I', len(trk) + len(data))
    hdr = out + trk
    pad = 3 * 512 - len(hdr)
    assert pad >= 0
    # the track data starts at block 3 (the TRKS chunk is padded up to it)
    return bytes(hdr + bytes(pad) + data), pad


def _formats():
    rnd = random.Random(3)
    dsk = bytes(rnd.randrange(256) for _ in range(a2disk.DSK_SIZE))
    ok = True
    po = a2disk.dsk_to_po(dsk)
    ok &= check('formats: .po <-> .dsk', a2disk.po_to_dsk(po) == dsk and a2disk.detect(po, 'x.po')[0] == 'po')
    nib = b''.join(a2disk.nibblize(dsk))
    img, bad = a2disk.sector_image(nib, 'x.nib')
    ok &= check('formats: .nib decode', img == dsk and not bad)
    two = b'2IMG' + b'XXXX' + bytes([64, 0, 1, 0]) + (1).to_bytes(4, 'little') + bytes(4) + \
        (280).to_bytes(4, 'little') + (64).to_bytes(4, 'little') + len(po).to_bytes(4, 'little')
    two = two.ljust(64, b'\0') + po
    ok &= check('formats: .2mg (ProDOS order)', a2disk.sector_image(two, 'x.2mg')[0] == dsk)
    tracks = a2disk.nibblize(dsk)
    woz, pad = _woz2(tracks)
    # the TRKS chunk length covers the padding and the track data
    import struct
    i = woz.index(b'TRKS')
    woz = bytearray(woz)
    struct.pack_into('<I', woz, i + 4, len(woz) - (i + 8))
    img, bad = a2disk.sector_image(bytes(woz), 'x.woz')
    ok &= check('formats: .woz (WOZ2 bit stream)', img == dsk and not bad)
    return ok


if __name__ == '__main__':
    main()
