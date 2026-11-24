"""Generic Apple II (II+/IIe-class, 64K with language card) reference machine for host-side tracing.

ROMs and disk images are supplied by the user at run time; none are bundled.
"""
from .cpu6502 import CPU6502

CYCLES_PER_FRAME = 17030
VBL_START = 12480          # 192 lines * 65 cycles

# DOS 3.3 order: .dsk logical sector stored in physical sector p
PHYS_TO_DOS = [0, 7, 14, 6, 13, 5, 12, 4, 11, 3, 10, 2, 9, 1, 8, 15]

WRITE_TABLE = [
    0x96, 0x97, 0x9A, 0x9B, 0x9D, 0x9E, 0x9F, 0xA6, 0xA7, 0xAB, 0xAC, 0xAD, 0xAE, 0xAF, 0xB2, 0xB3,
    0xB4, 0xB5, 0xB6, 0xB7, 0xB9, 0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF, 0xCB, 0xCD, 0xCE, 0xCF, 0xD3,
    0xD6, 0xD7, 0xD9, 0xDA, 0xDB, 0xDC, 0xDD, 0xDE, 0xDF, 0xE5, 0xE6, 0xE7, 0xE9, 0xEA, 0xEB, 0xEC,
    0xED, 0xEE, 0xEF, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF7, 0xF9, 0xFA, 0xFB, 0xFC, 0xFD, 0xFE, 0xFF]


def encode_sector(data):
    """6-and-2 encode 256 bytes into 343 disk nibbles (342 + checksum)."""
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
        out.append(WRITE_TABLE[v ^ prev]); prev = v
    out.append(WRITE_TABLE[prev])
    return out


def fourfour(v):
    return [(v >> 1) | 0xAA, v | 0xAA]


def nibblize_track(dsk, track, volume=254):
    t = []
    t += [0xFF] * 48
    for p in range(16):
        s = PHYS_TO_DOS[p]
        t += [0xD5, 0xAA, 0x96] + fourfour(volume) + fourfour(track) + fourfour(p) + \
             fourfour(volume ^ track ^ p) + [0xDE, 0xAA, 0xEB]
        t += [0xFF] * 6
        off = (track * 16 + s) * 256
        t += [0xD5, 0xAA, 0xAD] + encode_sector(dsk[off:off + 256]) + [0xDE, 0xAA, 0xEB]
        t += [0xFF] * 20
    return bytes(t)


class Disk2:
    def __init__(self, dsk):
        """dsk: a 140K DOS-order image, a list of 35 nibble tracks, or any image kit/disk/a2disk.py reads
        (.po, .nib, .woz, .2mg): nibble and bit images keep their non-standard (protected) tracks"""
        if isinstance(dsk, list):
            self.tracks = [t if t else bytes([0xFF]) * 6400 for t in dsk]
        elif len(dsk) == 143360 and dsk[:4] != b'2IMG':
            from ..disk.a2disk import detect, po_to_dsk
            if detect(bytes(dsk))[0] == 'po':        # ProDOS block order (found by content)
                dsk = po_to_dsk(bytes(dsk))
            self.tracks = [nibblize_track(dsk, t) for t in range(35)]
        else:
            from ..disk.a2disk import host_tracks
            self.tracks = [t if t else bytes([0xFF]) * 6400 for t in host_tracks(bytes(dsk))]
        self.phases = 0
        self.qtrack = 0          # half-track position * 2 (quarter-track units)
        self.pos = 0
        self.motor = False
        self.q6 = self.q7 = False
        self.latch = 0
        self.reads = 0           # nibble read counter (statistics)
        self.motor_off_at = -10**9
        self.last_n = -1
        self.fresh = False
        self.clock = lambda: 0

    def access(self, a):
        a &= 0xF
        if a < 8:
            ph = a >> 1
            on = a & 1
            if on:
                self.phases |= 1 << ph
            else:
                self.phases &= ~(1 << ph)
            self._step()
        elif a == 8:
            if self.motor: self.motor_off_at = self.clock()
            self.motor = False
        elif a == 9: self.motor = True
        elif a == 0xC: self.q6 = False
        elif a == 0xD: self.q6 = True
        elif a == 0xE: self.q7 = False
        elif a == 0xF: self.q7 = True
        if a == 0xC and not self.q7:
            now = self.clock()
            spinning = self.motor or now - self.motor_off_at < 1000000
            if not spinning:
                return self.latch
            trk = self.tracks[min(self.qtrack // 4, 34)]
            # one nibble every 32 cycles; a read returns the latch once, then 0 until the next nibble
            n = now // 32
            if n != self.last_n:
                self.last_n = n
                self.pos = (self.pos + 1) % len(trk)
                self.latch = trk[self.pos]
                self.reads += 1
                self.fresh = True
            if self.fresh:
                self.fresh = False
                return self.latch
            return self.latch & 0x7F
        if a == 0xE and self.q6:
            return 0x00          # write-protect sense: not protected
        return 0

    def _step(self):
        # quarter-track stepping: magnet "ph" pulls the head to qtrack = ph*2 (half-tracks *2)
        cur = (self.qtrack // 2) & 3   # current phase index (half-track units)
        for d in (1, -1):
            if self.phases & (1 << ((cur + d) & 3)):
                self.qtrack = max(0, min(34 * 4, self.qtrack + d * 2))
                break


class Apple2:
    def __init__(self, rom12k, disk_prom, dsk, cxrom=None):
        assert len(rom12k) == 0x3000 and len(disk_prom) == 256
        self.cxrom = cxrom          # optional IIe internal $C000-$CFFF ROM (4K)
        self.intcx = False; self.slotc3 = False; self.intc8 = False
        self.ram = bytearray(0x10000)
        self.lc = bytearray(0x4000)    # $D000-$FFFF language card RAM (bank1 $D000 at 0x0000, bank2 at 0x1000? simplified)
        self.lc_bank2 = bytearray(0x1000)
        self.rom = bytes(rom12k)
        self.prom = bytes(disk_prom)
        self.disk = Disk2(dsk)
        self.cpu = CPU6502(self)
        self.disk.clock = lambda: self.cpu.cycles
        self.kbd = 0
        self.buttons = 0
        self.paddle = [128, 128]
        self.ptrig_at = -10**9
        self.speaker_clicks = []
        self.sw = dict(text=True, mixed=False, page2=False, hires=False)
        self.lc_read = False; self.lc_write = False; self.lc_b2 = True; self.lc_prewrite = 0
        self.on_hires_write = None
        self.io_log = None

    # ---- memory
    def read(self, a):
        if a < 0xC000:
            return self.ram[a]
        if a < 0xC100:
            return self.io(a, None)
        if self.cxrom is not None:
            pg = a >> 8
            if pg == 0xC3 and not self.slotc3:
                self.intc8 = True
            if a == 0xCFFF:
                self.intc8 = False
            if self.intcx or (pg == 0xC3 and not self.slotc3) or (pg >= 0xC8 and self.intc8):
                if a < 0xD000:
                    return self.cxrom[a - 0xC000]
        if 0xC600 <= a < 0xC700:
            return self.prom[a - 0xC600]
        if a < 0xD000:
            return 0
        if self.lc_read:
            if a < 0xE000 and self.lc_b2:
                return self.lc_bank2[a - 0xD000]
            return self.lc[a - 0xD000]
        return self.rom[a - 0xD000]

    def write(self, a, v):
        if a < 0xC000:
            self.ram[a] = v
            if 0x2000 <= a < 0x6000 and self.on_hires_write:
                self.on_hires_write(a, v)
            return
        if a < 0xC100:
            if a == 0xC006: self.intcx = False
            elif a == 0xC007: self.intcx = True
            elif a == 0xC00A: self.slotc3 = False
            elif a == 0xC00B: self.slotc3 = True
            self.io(a, v); return
        if a >= 0xD000 and self.lc_write:
            if a < 0xE000 and self.lc_b2:
                self.lc_bank2[a - 0xD000] = v
            else:
                self.lc[a - 0xD000] = v

    def io(self, a, v):
        if self.io_log is not None:
            self.io_log[a] = self.io_log.get(a, 0) + 1
        lo = a & 0xFF
        if lo < 0x10:
            return self.kbd
        if lo < 0x20:
            if lo == 0x10:
                self.kbd &= 0x7F
                return self.kbd
            if lo == 0x15: return 0x80 if self.intcx else 0
            if lo == 0x17: return 0x80 if self.slotc3 else 0
            if lo == 0x19:     # IIe RDVBLBAR: bit7 = 1 when not in VBL
                f = self.cpu.cycles % CYCLES_PER_FRAME
                return 0x00 if f >= VBL_START else 0x80
            return 0
        if 0x30 <= lo < 0x40:
            self.speaker_clicks.append(self.cpu.cycles); return 0
        if 0x50 <= lo < 0x58:
            k = (lo - 0x50) >> 1; on = lo & 1
            if k == 0: self.sw['text'] = bool(on)
            elif k == 1: self.sw['mixed'] = bool(on)
            elif k == 2: self.sw['page2'] = bool(on)
            else: self.sw['hires'] = bool(on)
            return 0
        if 0x60 <= lo < 0x68:
            if lo in (0x61, 0x62, 0x63):
                return 0x80 if self.buttons & (1 << (lo - 0x61)) else 0
            if lo in (0x64, 0x65):
                el = self.cpu.cycles - self.ptrig_at
                return 0x80 if el < self.paddle[lo - 0x64] * 11 else 0
            return 0
        if 0x70 <= lo < 0x80:
            self.ptrig_at = self.cpu.cycles
            return 0
        if 0x80 <= lo < 0x90:
            self._lc(lo & 0xF, v is None); return 0
        if 0xE0 <= lo < 0xF0:
            return self.disk.access(lo)
        return 0

    def _lc(self, n, is_read):
        self.lc_b2 = not (n & 8)
        r = n & 3
        self.lc_read = r in (0, 3)
        if n & 1:
            if is_read:
                self.lc_prewrite += 1
                if self.lc_prewrite >= 2: self.lc_write = True
            # writes to odd switches don't count towards pre-write
        else:
            self.lc_write = False; self.lc_prewrite = 0

    # ---- control
    def boot(self, slot=6):
        self.cpu.reset()
        self.cpu.pc = 0xC600 | ((slot - 6) * 0)

    def run_cycles(self, n, trace=None):
        cpu = self.cpu
        end = cpu.cycles + n
        while cpu.cycles < end:
            if trace is not None:
                trace(cpu.pc)
            cpu.step()

    def hires_page(self):
        base = 0x4000 if self.sw['page2'] else 0x2000
        return self.ram[base:base + 0x2000]
