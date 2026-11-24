"""NMOS 6502 opcode table (documented opcodes) and decoder. Game-independent."""

MODE_LEN = {'imp': 1, 'acc': 1, 'imm': 2, 'zp': 2, 'zpx': 2, 'zpy': 2, 'abs': 3, 'absx': 3, 'absy': 3,
            'ind': 3, 'indx': 2, 'indy': 2, 'rel': 2}

OPS = {}  # opcode -> (mnemonic, mode)


def _build():
    g1 = {'indx': 0x01, 'zp': 0x05, 'imm': 0x09, 'abs': 0x0D, 'indy': 0x11, 'zpx': 0x15, 'absy': 0x19, 'absx': 0x1D}
    for mn, base in (('ORA', 0x00), ('AND', 0x20), ('EOR', 0x40), ('ADC', 0x60), ('STA', 0x80), ('LDA', 0xA0),
                     ('CMP', 0xC0), ('SBC', 0xE0)):
        for mode, off in g1.items():
            if mn == 'STA' and mode == 'imm':
                continue
            OPS[base + off] = (mn, mode)
    for mn, base in (('ASL', 0x00), ('ROL', 0x20), ('LSR', 0x40), ('ROR', 0x60)):
        OPS[base + 0x06] = (mn, 'zp'); OPS[base + 0x0A] = (mn, 'acc')
        OPS[base + 0x0E] = (mn, 'abs'); OPS[base + 0x16] = (mn, 'zpx'); OPS[base + 0x1E] = (mn, 'absx')
    for mn, base in (('DEC', 0xC0), ('INC', 0xE0)):
        OPS[base + 0x06] = (mn, 'zp'); OPS[base + 0x0E] = (mn, 'abs')
        OPS[base + 0x16] = (mn, 'zpx'); OPS[base + 0x1E] = (mn, 'absx')
    OPS.update({
        0xA2: ('LDX', 'imm'), 0xA6: ('LDX', 'zp'), 0xAE: ('LDX', 'abs'), 0xB6: ('LDX', 'zpy'), 0xBE: ('LDX', 'absy'),
        0xA0: ('LDY', 'imm'), 0xA4: ('LDY', 'zp'), 0xAC: ('LDY', 'abs'), 0xB4: ('LDY', 'zpx'), 0xBC: ('LDY', 'absx'),
        0x86: ('STX', 'zp'), 0x8E: ('STX', 'abs'), 0x96: ('STX', 'zpy'),
        0x84: ('STY', 'zp'), 0x8C: ('STY', 'abs'), 0x94: ('STY', 'zpx'),
        0xE0: ('CPX', 'imm'), 0xE4: ('CPX', 'zp'), 0xEC: ('CPX', 'abs'),
        0xC0: ('CPY', 'imm'), 0xC4: ('CPY', 'zp'), 0xCC: ('CPY', 'abs'),
        0x24: ('BIT', 'zp'), 0x2C: ('BIT', 'abs'),
        0x4C: ('JMP', 'abs'), 0x6C: ('JMP', 'ind'), 0x20: ('JSR', 'abs'),
        0x60: ('RTS', 'imp'), 0x40: ('RTI', 'imp'), 0x00: ('BRK', 'imp'),
        0x10: ('BPL', 'rel'), 0x30: ('BMI', 'rel'), 0x50: ('BVC', 'rel'), 0x70: ('BVS', 'rel'),
        0x90: ('BCC', 'rel'), 0xB0: ('BCS', 'rel'), 0xD0: ('BNE', 'rel'), 0xF0: ('BEQ', 'rel'),
    })
    for op, mn in ((0x18, 'CLC'), (0x38, 'SEC'), (0x58, 'CLI'), (0x78, 'SEI'), (0xB8, 'CLV'), (0xD8, 'CLD'),
                   (0xF8, 'SED'), (0xAA, 'TAX'), (0x8A, 'TXA'), (0xA8, 'TAY'), (0x98, 'TYA'), (0xBA, 'TSX'),
                   (0x9A, 'TXS'), (0xE8, 'INX'), (0xCA, 'DEX'), (0xC8, 'INY'), (0x88, 'DEY'), (0x48, 'PHA'),
                   (0x68, 'PLA'), (0x08, 'PHP'), (0x28, 'PLP'), (0xEA, 'NOP')):
        OPS[op] = (mn, 'imp')


_build()

BRANCH = {'BPL': ('N', 0), 'BMI': ('N', 1), 'BVC': ('V', 0), 'BVS': ('V', 1),
          'BCC': ('C', 0), 'BCS': ('C', 1), 'BNE': ('Z', 0), 'BEQ': ('Z', 1)}


class Insn:
    __slots__ = ('addr', 'op', 'mn', 'mode', 'len', 'arg', 'raw')

    def __init__(self, addr, raw):
        self.addr = addr
        self.raw = bytes(raw)
        self.op = raw[0]
        self.mn, self.mode = OPS[self.op]
        self.len = MODE_LEN[self.mode]
        if self.len == 2:
            self.arg = raw[1]
        elif self.len == 3:
            self.arg = raw[1] | raw[2] << 8
        else:
            self.arg = None

    @property
    def next(self):
        return (self.addr + self.len) & 0xFFFF

    @property
    def target(self):
        """static control-transfer target (branch / JMP abs / JSR) or None"""
        if self.mode == 'rel':
            return (self.addr + 2 + (self.arg - 256 if self.arg >= 128 else self.arg)) & 0xFFFF
        if self.mn in ('JMP', 'JSR') and self.mode == 'abs':
            return self.arg
        return None

    def text(self):
        m, a = self.mode, self.arg
        if m in ('imp',):
            return self.mn
        if m == 'acc':
            return self.mn + ' A'
        if m == 'rel':
            return f'{self.mn} ${self.target:04X}'
        fmt = {'imm': '#${:02X}', 'zp': '${:02X}', 'zpx': '${:02X},X', 'zpy': '${:02X},Y', 'abs': '${:04X}',
               'absx': '${:04X},X', 'absy': '${:04X},Y', 'ind': '(${:04X})', 'indx': '(${:02X},X)',
               'indy': '(${:02X}),Y'}[m]
        return self.mn + ' ' + fmt.format(a)


def decode(mem, addr):
    """Insn at addr in a 64K-indexable byte buffer, or None for an undocumented opcode"""
    op = mem[addr]
    if op not in OPS:
        return None
    n = MODE_LEN[OPS[op][1]]
    return Insn(addr, [mem[(addr + i) & 0xFFFF] for i in range(n)])
