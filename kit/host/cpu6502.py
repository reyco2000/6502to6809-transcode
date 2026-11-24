"""Generic NMOS 6502 core (documented opcodes, decimal mode). Memory via bus.read/bus.write."""

C, Z, I, D, B, U, V, N = 1, 2, 4, 8, 16, 32, 64, 128


class CPU6502:
    def __init__(self, bus):
        self.bus = bus
        self.a = self.x = self.y = 0
        self.s = 0xFD
        self.p = U | I
        self.pc = 0
        self.cycles = 0
        self.ops = self._build()

    # ---- helpers
    def r(self, a):
        return self.bus.read(a & 0xFFFF)

    def w(self, a, v):
        self.bus.write(a & 0xFFFF, v & 0xFF)

    def r16(self, a):
        return self.r(a) | (self.r(a + 1) << 8)

    def r16zp(self, a):
        return self.r(a & 0xFF) | (self.r((a + 1) & 0xFF) << 8)

    def push(self, v):
        self.w(0x100 | self.s, v)
        self.s = (self.s - 1) & 0xFF

    def pull(self):
        self.s = (self.s + 1) & 0xFF
        return self.r(0x100 | self.s)

    def nz(self, v):
        self.p = (self.p & ~(N | Z)) | (v & N) | (0 if v else Z)
        return v

    def reset(self):
        self.pc = self.r16(0xFFFC)
        self.s = 0xFD
        self.p = U | I

    # ---- addressing modes: return effective address
    def imm(self):
        a = self.pc; self.pc = (self.pc + 1) & 0xFFFF; return a

    def zp(self):
        a = self.r(self.pc); self.pc = (self.pc + 1) & 0xFFFF; return a

    def zpx(self):
        return (self.zp() + self.x) & 0xFF

    def zpy(self):
        return (self.zp() + self.y) & 0xFF

    def abs_(self):
        a = self.r16(self.pc); self.pc = (self.pc + 2) & 0xFFFF; return a

    def absx(self):
        b = self.abs_(); a = (b + self.x) & 0xFFFF
        if (a ^ b) & 0xFF00: self.cycles += 1
        return a

    def absy(self):
        b = self.abs_(); a = (b + self.y) & 0xFFFF
        if (a ^ b) & 0xFF00: self.cycles += 1
        return a

    def indx(self):
        return self.r16zp(self.zp() + self.x)

    def indy(self):
        b = self.r16zp(self.zp()); a = (b + self.y) & 0xFFFF
        if (a ^ b) & 0xFF00: self.cycles += 1
        return a

    # ---- ALU
    def adc(self, m):
        a, c = self.a, self.p & C
        if self.p & D:
            lo = (a & 0xF) + (m & 0xF) + c
            hi = (a >> 4) + (m >> 4)
            if lo > 9: lo += 6
            if lo > 0xF: hi += 1
            z = ((a + m + c) & 0xFF) == 0
            nv = (hi << 4) & 0xFF
            v = (~(a ^ m) & (a ^ nv)) & 0x80
            if hi > 9: hi += 6
            r = ((hi << 4) | (lo & 0xF)) & 0xFF
            self.p = (self.p & ~(N | V | Z | C)) | (nv & N) | (V if v else 0) | (Z if z else 0) | (C if hi > 0xF else 0)
            self.a = r
        else:
            s = a + m + c
            r = s & 0xFF
            v = (~(a ^ m) & (a ^ r)) & 0x80
            self.p = (self.p & ~(V | C)) | (V if v else 0) | (C if s > 0xFF else 0)
            self.a = self.nz(r)

    def sbc(self, m):
        a, c = self.a, self.p & C
        s = a - m - (1 - c)
        r = s & 0xFF
        v = ((a ^ m) & (a ^ r)) & 0x80
        if self.p & D:
            lo = (a & 0xF) - (m & 0xF) - (1 - c)
            hi = (a >> 4) - (m >> 4)
            if lo < 0: lo -= 6; hi -= 1
            if hi < 0: hi -= 6
            res = ((hi << 4) | (lo & 0xF)) & 0xFF
        else:
            res = r
        self.p = (self.p & ~(V | C)) | (V if v else 0) | (C if s >= 0 else 0)
        self.nz(r)
        self.a = res

    def cmp(self, reg, m):
        t = reg - m
        self.p = (self.p & ~C) | (C if t >= 0 else 0)
        self.nz(t & 0xFF)

    def branch(self, cond):
        off = self.r(self.pc); self.pc = (self.pc + 1) & 0xFFFF
        if cond:
            if off & 0x80: off -= 256
            t = (self.pc + off) & 0xFFFF
            self.cycles += 1 + (1 if (t ^ self.pc) & 0xFF00 else 0)
            self.pc = t

    # ---- RMW helpers
    def asl(self, v):
        self.p = (self.p & ~C) | (C if v & 0x80 else 0); return self.nz((v << 1) & 0xFF)

    def lsr(self, v):
        self.p = (self.p & ~C) | (v & 1); return self.nz(v >> 1)

    def rol(self, v):
        c = self.p & C
        self.p = (self.p & ~C) | (C if v & 0x80 else 0); return self.nz(((v << 1) | c) & 0xFF)

    def ror(self, v):
        c = self.p & C
        self.p = (self.p & ~C) | (v & 1); return self.nz((v >> 1) | (0x80 if c else 0))

    def _build(self):
        ops = [None] * 256
        modes = {'imm': self.imm, 'zp': self.zp, 'zpx': self.zpx, 'zpy': self.zpy, 'abs': self.abs_,
                 'absx': self.absx, 'absy': self.absy, 'indx': self.indx, 'indy': self.indy}

        def ld(reg, mode, cyc):
            m = modes[mode]
            def f():
                v = self.r(m()); setattr(self, reg, self.nz(v)); self.cycles += cyc
            return f

        def st(reg, mode, cyc):
            m = modes[mode]
            def f():
                self.w(m(), getattr(self, reg)); self.cycles += cyc
            return f

        def alu(fn, mode, cyc):
            m = modes[mode]
            def f():
                fn(self.r(m())); self.cycles += cyc
            return f

        def rmw(fn, mode, cyc):
            m = modes[mode]
            def f():
                a = m(); v = self.r(a); self.w(a, v); self.w(a, fn(v)); self.cycles += cyc
            return f

        def ora(v): self.a = self.nz(self.a | v)
        def and_(v): self.a = self.nz(self.a & v)
        def eor(v): self.a = self.nz(self.a ^ v)
        def cmpa(v): self.cmp(self.a, v)
        def cpx(v): self.cmp(self.x, v)
        def cpy(v): self.cmp(self.y, v)
        def bit(v):
            self.p = (self.p & ~(N | V | Z)) | (v & (N | V)) | (0 if self.a & v else Z)
        def inc(v): return self.nz((v + 1) & 0xFF)
        def dec(v): return self.nz((v - 1) & 0xFF)

        table = {
            'ora': (ora, {0x09: 'imm2', 0x05: 'zp3', 0x15: 'zpx4', 0x0D: 'abs4', 0x1D: 'absx4', 0x19: 'absy4', 0x01: 'indx6', 0x11: 'indy5'}),
            'and': (and_, {0x29: 'imm2', 0x25: 'zp3', 0x35: 'zpx4', 0x2D: 'abs4', 0x3D: 'absx4', 0x39: 'absy4', 0x21: 'indx6', 0x31: 'indy5'}),
            'eor': (eor, {0x49: 'imm2', 0x45: 'zp3', 0x55: 'zpx4', 0x4D: 'abs4', 0x5D: 'absx4', 0x59: 'absy4', 0x41: 'indx6', 0x51: 'indy5'}),
            'adc': (self.adc, {0x69: 'imm2', 0x65: 'zp3', 0x75: 'zpx4', 0x6D: 'abs4', 0x7D: 'absx4', 0x79: 'absy4', 0x61: 'indx6', 0x71: 'indy5'}),
            'sbc': (self.sbc, {0xE9: 'imm2', 0xE5: 'zp3', 0xF5: 'zpx4', 0xED: 'abs4', 0xFD: 'absx4', 0xF9: 'absy4', 0xE1: 'indx6', 0xF1: 'indy5'}),
            'cmp': (cmpa, {0xC9: 'imm2', 0xC5: 'zp3', 0xD5: 'zpx4', 0xCD: 'abs4', 0xDD: 'absx4', 0xD9: 'absy4', 0xC1: 'indx6', 0xD1: 'indy5'}),
            'cpx': (cpx, {0xE0: 'imm2', 0xE4: 'zp3', 0xEC: 'abs4'}),
            'cpy': (cpy, {0xC0: 'imm2', 0xC4: 'zp3', 0xCC: 'abs4'}),
            'bit': (bit, {0x24: 'zp3', 0x2C: 'abs4'}),
        }
        import re
        def split(s):
            mo = re.match(r'([a-z]+)(\d)', s); return mo.group(1), int(mo.group(2))
        for name, (fn, codes) in table.items():
            for op, ms in codes.items():
                mode, cyc = split(ms); ops[op] = alu(fn, mode, cyc)
        for reg, codes in {'a': {0xA9: 'imm2', 0xA5: 'zp3', 0xB5: 'zpx4', 0xAD: 'abs4', 0xBD: 'absx4', 0xB9: 'absy4', 0xA1: 'indx6', 0xB1: 'indy5'},
                           'x': {0xA2: 'imm2', 0xA6: 'zp3', 0xB6: 'zpy4', 0xAE: 'abs4', 0xBE: 'absy4'},
                           'y': {0xA0: 'imm2', 0xA4: 'zp3', 0xB4: 'zpx4', 0xAC: 'abs4', 0xBC: 'absx4'}}.items():
            for op, ms in codes.items():
                mode, cyc = split(ms); ops[op] = ld(reg, mode, cyc)
        for reg, codes in {'a': {0x85: 'zp3', 0x95: 'zpx4', 0x8D: 'abs4', 0x9D: 'absx5', 0x99: 'absy5', 0x81: 'indx6', 0x91: 'indy6'},
                           'x': {0x86: 'zp3', 0x96: 'zpy4', 0x8E: 'abs4'},
                           'y': {0x84: 'zp3', 0x94: 'zpx4', 0x8C: 'abs4'}}.items():
            for op, ms in codes.items():
                mode, cyc = split(ms)
                # stores never take the page-cross penalty
                if mode in ('absx', 'absy', 'indy'):
                    base = {'absx': lambda: (self.abs_() + self.x) & 0xFFFF,
                            'absy': lambda: (self.abs_() + self.y) & 0xFFFF,
                            'indy': lambda: (self.r16zp(self.zp()) + self.y) & 0xFFFF}[mode]
                    def mk(base=base, reg=reg, cyc=cyc):
                        def f():
                            self.w(base(), getattr(self, reg)); self.cycles += cyc
                        return f
                    ops[op] = mk()
                else:
                    ops[op] = st(reg, mode, cyc)
        rmws = {'asl': (self.asl, 0x06, 0x16, 0x0E, 0x1E), 'lsr': (self.lsr, 0x46, 0x56, 0x4E, 0x5E),
                'rol': (self.rol, 0x26, 0x36, 0x2E, 0x3E), 'ror': (self.ror, 0x66, 0x76, 0x6E, 0x7E),
                'inc': (inc, 0xE6, 0xF6, 0xEE, 0xFE), 'dec': (dec, 0xC6, 0xD6, 0xCE, 0xDE)}
        for name, (fn, z, zx, ab, abx) in rmws.items():
            ops[z] = rmw(fn, 'zp', 5); ops[zx] = rmw(fn, 'zpx', 6); ops[ab] = rmw(fn, 'abs', 6)
            def mkx(fn=fn):
                def f():
                    a = (self.abs_() + self.x) & 0xFFFF; v = self.r(a); self.w(a, v); self.w(a, fn(v)); self.cycles += 7
                return f
            ops[abx] = mkx()
        def acc(fn):
            def f():
                self.a = fn(self.a); self.cycles += 2
            return f
        ops[0x0A] = acc(self.asl); ops[0x4A] = acc(self.lsr); ops[0x2A] = acc(self.rol); ops[0x6A] = acc(self.ror)
        for op, flag, val in ((0x10, N, 0), (0x30, N, 1), (0x50, V, 0), (0x70, V, 1), (0x90, C, 0), (0xB0, C, 1), (0xD0, Z, 0), (0xF0, Z, 1)):
            def mkb(flag=flag, val=val):
                def f():
                    self.cycles += 2; self.branch(bool(self.p & flag) == bool(val))
                return f
            ops[op] = mkb()
        def flag(op, mask, setv):
            def f():
                self.p = (self.p | mask) if setv else (self.p & ~mask); self.cycles += 2
            ops[op] = f
        flag(0x18, C, 0); flag(0x38, C, 1); flag(0x58, I, 0); flag(0x78, I, 1)
        flag(0xB8, V, 0); flag(0xD8, D, 0); flag(0xF8, D, 1)
        def impl(op, fn, cyc):
            def f():
                fn(); self.cycles += cyc
            ops[op] = f
        impl(0xAA, lambda: setattr(self, 'x', self.nz(self.a)), 2)
        impl(0x8A, lambda: setattr(self, 'a', self.nz(self.x)), 2)
        impl(0xA8, lambda: setattr(self, 'y', self.nz(self.a)), 2)
        impl(0x98, lambda: setattr(self, 'a', self.nz(self.y)), 2)
        impl(0xBA, lambda: setattr(self, 'x', self.nz(self.s)), 2)
        impl(0x9A, lambda: setattr(self, 's', self.x), 2)
        impl(0xE8, lambda: setattr(self, 'x', self.nz((self.x + 1) & 0xFF)), 2)
        impl(0xCA, lambda: setattr(self, 'x', self.nz((self.x - 1) & 0xFF)), 2)
        impl(0xC8, lambda: setattr(self, 'y', self.nz((self.y + 1) & 0xFF)), 2)
        impl(0x88, lambda: setattr(self, 'y', self.nz((self.y - 1) & 0xFF)), 2)
        impl(0xEA, lambda: None, 2)
        impl(0x48, lambda: self.push(self.a), 3)
        impl(0x08, lambda: self.push(self.p | B | U), 3)
        impl(0x68, lambda: setattr(self, 'a', self.nz(self.pull())), 4)
        impl(0x28, lambda: setattr(self, 'p', (self.pull() & ~B) | U), 4)
        def jmp():
            self.pc = self.r16(self.pc); self.cycles += 3
        def jmpi():
            a = self.r16(self.pc)
            self.pc = self.r(a) | (self.r((a & 0xFF00) | ((a + 1) & 0xFF)) << 8); self.cycles += 5
        def jsr():
            t = self.r16(self.pc); r = (self.pc + 1) & 0xFFFF
            self.push(r >> 8); self.push(r & 0xFF); self.pc = t; self.cycles += 6
        def rts():
            lo = self.pull(); hi = self.pull(); self.pc = ((hi << 8 | lo) + 1) & 0xFFFF; self.cycles += 6
        def rti():
            self.p = (self.pull() & ~B) | U; lo = self.pull(); hi = self.pull(); self.pc = hi << 8 | lo; self.cycles += 6
        def brk():
            r = (self.pc + 1) & 0xFFFF
            self.push(r >> 8); self.push(r & 0xFF); self.push(self.p | B | U)
            self.p |= I; self.pc = self.r16(0xFFFE); self.cycles += 7
        ops[0x4C] = jmp; ops[0x6C] = jmpi; ops[0x20] = jsr; ops[0x60] = rts; ops[0x40] = rti; ops[0x00] = brk
        return ops

    def irq(self):
        if self.p & I: return
        self.push(self.pc >> 8); self.push(self.pc & 0xFF); self.push((self.p & ~B) | U)
        self.p |= I; self.pc = self.r16(0xFFFE); self.cycles += 7

    def step(self):
        op = self.r(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        f = self.ops[op]
        if f is None:
            raise IllegalOpcode(op, (self.pc - 1) & 0xFFFF)
        f()
        return op


class IllegalOpcode(Exception):
    def __init__(self, op, pc):
        super().__init__(f"illegal opcode ${op:02X} at ${pc:04X}")
        self.op, self.pc = op, pc
