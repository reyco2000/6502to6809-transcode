"""xc65 - static 6502 -> 6809 transcoder (code generator). Game-independent.

Machine mapping (the transcoder ABI, see docs/METHOD.md):
  6502 A -> 6809 A;  6502 X -> 6809 B;  6502 Y -> byte RY (= RYW+1, RYW is a word whose high byte is always 0)
  6502 stack -> 6809 S (JSR/RTS native: return addresses are 6809 addresses), DP = 0 (6502 zero page),
  6809 X and U are scratch.  Flags: N, Z, C (6502 polarity) in CC at every label; V is not modelled (the
  input must not read it: CLV/PHP/PLP/RTI are rejected; BVC/BVS only right after ADC/SBC).
Memory: Apple addresses are used unchanged (data stays where the 6502 had it).  I/O ($C000-$C0FF) goes through
  handlers (value in IOV), ROM reads through constants, code addresses become labels <prefix><HHHH>.

translate(code, prefix, ctx) -> list of lwasm lines (without org)
  code   discover.Code (insns, targets, dead_fall ...)
  ctx    Ctx: label/transfer resolution and spec (I/O, ROM constants, natives, SMC operand sites)
"""
from .opcodes import BRANCH

A_, X_, Y_, N_, Z_, C_ = 1, 2, 4, 8, 16, 32
REGS, FLAGS, NZ = A_ | X_ | Y_, N_ | Z_ | C_, N_ | Z_
ALL = REGS | FLAGS


class XError(Exception):
    pass


class Native:
    """contract of a native routine called (JSR) or jumped to from translated code.
    use: registers/flags it reads; defs: registers/flags it may change (everything else is kept);
    outputs (filled by the emitter): defs that are live at some return site = must be exact"""
    def __init__(self, sym, use=REGS, defs=ALL, noreturn=False):
        self.sym, self.use, self.defs, self.noreturn = sym, use, defs, noreturn
        self.outputs = 0


def routine_contract(mem, entry, sym, natives=None, code_ranges=((0, 0xBFFF),)):
    """derive a Native contract from the 6502 routine that the native code replaces (calls to other
    natives use their contracts)"""
    from .discover import discover
    natives = {a: n for a, n in (natives or {}).items() if a != entry}
    inside = lambda a: any(lo <= a <= hi for lo, hi in code_ranges)
    c = discover(mem, [entry], {a: n.sym for a, n in natives.items()}, code_ranges=code_ranges,
                 outside=lambda a: not inside(a))
    if c.errors:
        raise XError(f'{sym}: cannot analyse 6502 routine at ${entry:04X}: {c.errors[:3]}')
    ctx = Ctx(natives=natives, ext_ret={entry: 0}, ext_live=lambda a: ALL)
    lv = Live(c, ctx)
    defs = ALL if c.outside else 0           # calls back into translated code: anything may change
    for i in c.insns.values():
        if i.mn in ('JSR', 'JMP') and i.arg in natives:
            defs |= natives[i.arg].defs
            continue
        if i.mn in ('JSR', 'JMP', 'RTS') or i.mode == 'rel':
            continue
        if i.mn in REJECT:
            defs |= ALL
            continue
        defs |= use_def(i)[1]
    return Native(sym, use=lv.before[entry], defs=defs)


class Ctx:
    """spec + resolution of transfers that leave this code set"""
    def __init__(self, natives=None, io=None, romconst=None, smc_imm=(), outside=None, traps=(),
                 ext_live=None, ext_ret=None, roots=(), io_mem=None, hires=None, romdata=(), ea=None, zp_wrap=()):
        self.natives = natives or {}      # addr -> Native
        self.io_mem = io_mem or {}        # addr ($C0xx) -> symbol: side-effect-free read of a runtime latch byte
        self.io = io or {}                # addr ($C0xx) -> (read handler or None, write handler or None)
        self.romconst = romconst or {}    # addr -> byte value
        self.smc_imm = set(smc_imm)       # addresses of immediate-mode instructions whose operand is patched
        self.outside = outside or (lambda a: None)   # addr -> asm symbol for a transfer leaving the set
        self.traps = set(traps)           # addresses that are dead in this code set (JSR/JMP -> x_trap)
        self.ext_live = ext_live or (lambda a: ALL)  # live before a transfer target outside the set
        self.ext_ret = ext_ret or {}      # routine entry -> live mask at return sites of callers outside
        self.roots = set(roots)           # entries reached with an unknown caller (snapshot entry, RTS chain)
        # HIRES_ACCESS: None = translated code never touches the hi-res pages (natives do, between HRON/HROFF);
        # else {addr: EA-class mask} from the profile: accesses that may reach $2000-$5FFF go through hr_rd/hr_wr
        self.hires = hires
        self.romdata = list(romdata)      # [(lo, hi)]: ROM ranges read as data, copied to ROM_<lo> tables (gen)
        self.ea = ea or {}                # {addr: EA-class mask} from the profile (used when hires is None)
        self.zp_wrap = set(zp_wrap)       # zp,X / zp,Y instructions the profile saw wrap past $FF


# ------------------------------------------------------------------------------------------- use / def
def _idx(ins):
    m = ins.mode
    return X_ if m in ('zpx', 'absx', 'indx') else Y_ if m in ('zpy', 'absy', 'indy') else 0


UD = {  # mnemonic -> (use, def) not counting index registers
    'LDA': (0, A_ | NZ), 'LDX': (0, X_ | NZ), 'LDY': (0, Y_ | NZ),
    'STA': (A_, 0), 'STX': (X_, 0), 'STY': (Y_, 0),
    'ORA': (A_, A_ | NZ), 'AND': (A_, A_ | NZ), 'EOR': (A_, A_ | NZ),
    'ADC': (A_ | C_, A_ | FLAGS), 'SBC': (A_ | C_, A_ | FLAGS),
    'CMP': (A_, FLAGS), 'CPX': (X_, FLAGS), 'CPY': (Y_, FLAGS), 'BIT': (A_, NZ),
    'INC': (0, NZ), 'DEC': (0, NZ), 'INX': (X_, X_ | NZ), 'DEX': (X_, X_ | NZ),
    'INY': (Y_, Y_ | NZ), 'DEY': (Y_, Y_ | NZ),
    'TAX': (A_, X_ | NZ), 'TXA': (X_, A_ | NZ), 'TAY': (A_, Y_ | NZ), 'TYA': (Y_, A_ | NZ),
    'PHA': (A_, 0), 'PLA': (0, A_ | NZ), 'CLC': (0, C_), 'SEC': (0, C_),
    'NOP': (0, 0), 'CLD': (0, 0), 'CLI': (0, 0), 'SEI': (0, 0),
    'BPL': (N_, 0), 'BMI': (N_, 0), 'BNE': (Z_, 0), 'BEQ': (Z_, 0), 'BCC': (C_, 0), 'BCS': (C_, 0),
    'BVC': (0, 0), 'BVS': (0, 0),     # V is not tracked: allowed only right after ADC/SBC (see emit_branch)
}
SHIFTS = ('ASL', 'LSR', 'ROL', 'ROR')
REJECT = ('CLV', 'PHP', 'PLP', 'RTI', 'BRK', 'SED', 'TSX', 'TXS')


def use_def(ins):
    mn = ins.mn
    if mn in SHIFTS:
        u = (C_ if mn in ('ROL', 'ROR') else 0) | (A_ if ins.mode == 'acc' else 0)
        return u | _idx(ins), (A_ if ins.mode == 'acc' else 0) | FLAGS
    u, d = UD[mn]
    return u | _idx(ins), d


# ------------------------------------------------------------------------------------------- liveness
class Live:
    def __init__(self, code, ctx):
        self.code, self.ctx = code, ctx
        ins = code.insns
        callers = {}
        for a, i in ins.items():
            if i.mn == 'JSR' and i.arg in ins:
                callers.setdefault(i.arg, []).append(a)
        self.callers = callers
        self.rts_of = {}      # rts addr -> set of routine entries
        for e in set(callers) | set(ctx.ext_ret) | (ctx.roots & set(ins)):
            seen, work = set(), [e]
            while work:
                a = work.pop()
                if a in seen or a not in ins:
                    continue
                seen.add(a)
                i = ins[a]
                if i.mn == 'RTS':
                    self.rts_of.setdefault(a, set()).add(e)
                    continue
                if i.mn == 'JSR':
                    work.append(i.next)
                elif i.mn == 'JMP':
                    work += getattr(code, 'jmp_multi', {}).get(a, [i.arg])
                elif i.mode == 'rel':
                    work.append(i.target)
                    if a not in code.dead_fall:
                        work.append(i.next)
                else:
                    work.append(i.next)
                for vi in getattr(code, 'op_variants', {}).get(a, ()):
                    if vi.mode == 'rel':
                        work += [vi.target, vi.next]
        self.before = {a: 0 for a in ins}
        self._solve()

    def lb(self, a):
        """live before address a (an instruction of the set, or a transfer leaving it)"""
        if a in self.code.insns:
            return self.before[a]
        if a in self.ctx.traps:
            return 0
        if a in self.ctx.natives:
            return self.ctx.natives[a].use
        return self.ctx.ext_live(a)

    def after(self, a):
        """live after the instruction at a (for the fall-through / non-transfer effect)"""
        i = self.code.insns[a]
        vs = getattr(self.code, 'op_variants', {}).get(a)
        if vs:
            v = 0
            for vi in vs:
                v |= (self.lb(vi.target) | self.lb(vi.next)) if vi.mode == 'rel' else self.lb(vi.next)
            return v
        mn = i.mn
        if mn == 'RTS':
            return self.rts_live(a)
        if mn == 'JMP':
            multi = getattr(self.code, 'jmp_multi', {}).get(a)
            if multi:
                v = 0
                for t in multi:
                    v |= self.lb(t)
                return v
            return self.lb(i.arg)
        if mn == 'JSR':
            return self.lb(i.next)
        if i.mode == 'rel':
            s = self.lb(i.target)
            if a not in self.code.dead_fall:
                s |= self.lb(i.next)
            return s
        return self.lb(i.next)

    def rts_live(self, a):
        es = self.rts_of.get(a)
        if not es:
            return ALL                    # RTS of code with no known entry: be safe
        s = 0
        for e in es:
            if e in self.ctx.roots:
                s |= ALL
            s |= self.ctx.ext_ret.get(e, 0)
            for j in self.callers.get(e, ()):
                s |= self.lb(self.code.insns[j].next)
        return s

    def _solve(self):
        ins = self.code.insns
        order = sorted(ins, reverse=True)
        changed = True
        while changed:
            changed = False
            for a in order:
                i = ins[a]
                mn = i.mn
                if mn == 'JSR':
                    t, r = i.arg, i.next
                    if t in ins:
                        v = self.before[t] | self.lb(r)
                    elif t in self.ctx.traps:
                        v = 0
                    elif t in self.ctx.natives:
                        n = self.ctx.natives[t]
                        v = n.use if n.noreturn else n.use | (self.lb(r) & ~n.defs)
                    else:
                        v = self.ctx.ext_live(t) | self.lb(r)
                elif mn in ('RTS', 'JMP'):
                    v = self.after(a)
                elif mn in REJECT or i.mode == 'ind':
                    v = ALL
                elif a in getattr(self.code, 'op_variants', {}):
                    v = 0
                    for vi in self.code.op_variants[a]:
                        u, d = use_def(vi)
                        aft = (self.lb(vi.target) | self.lb(vi.next)) if vi.mode == 'rel' else self.lb(vi.next)
                        v |= u | (aft & ~d)
                else:
                    u, d = use_def(i)
                    v = u | (self.after(a) & ~d)
                if v != self.before[a]:
                    self.before[a] = v
                    changed = True


# ------------------------------------------------------------------------------------------- emitter
class _St:
    __slots__ = ('cinv', 'nzok', 'rec')

    def __init__(self, cinv=False, nzok=True, rec=None):
        self.cinv, self.nzok, self.rec = cinv, nzok, rec


class Emitter:
    def __init__(self, code, prefix, ctx, live=None, comments=True):
        self.code, self.pfx, self.ctx = code, prefix, ctx
        self.live = live or Live(code, ctx)
        self.out = []
        self.nlab = 0
        self.comments = comments
        self.labels = set(code.targets) | {i.target for i in code.insns.values()
                                           if i.mode == 'rel' or i.mn in ('JMP', 'JSR')}
        self.labels &= set(code.insns)
        self.ptrs = set()            # zero-page pointers used by (zp),Y -> xiy_ZZ helpers
        self.skip = set()            # instructions folded into a pushed return address (pushed_return)
        self.pushed = []             # (address, return target) of the pushed return addresses
        self._xfer = None

    # ---- helpers
    def lab(self, a):
        return f'{self.pfx}{a:04X}'

    def e(self, op, arg=''):
        self.out.append(f'\t{op}\t{arg}' if arg else f'\t{op}')

    def tmp(self):
        self.nlab += 1
        return f'{self.pfx}_{self.nlab}'

    def regen_nz(self):
        r = self.st.rec
        if r == 'a':
            self.e('tsta')
        elif r == 'b':
            self.e('tstb')
        elif r == 'ry':
            self.e('tst', '>RY')
        else:
            raise XError(f'${self.cur:04X}: N/Z needed but not recoverable')
        self.st.nzok = True

    def flip_c(self):
        l1, l2 = self.tmp(), self.tmp()
        self.e('bcs', l1)
        self.e('orcc', '#$01')
        self.e('bra', l2)
        self.out.append(f'{l1}\tandcc\t#$FE')
        self.out.append(f'{l2}')
        self.st.cinv = not self.st.cinv

    def canon(self, mask):
        if mask & NZ and not self.st.nzok:
            self.regen_nz()
        if mask & C_ and self.st.cinv:
            self.flip_c()

    def target_sym(self, t, kind):
        """asm symbol for a JSR/JMP/branch target"""
        if t in self.code.insns:
            return self.lab(t)
        if t in self.ctx.traps:
            return None
        if t in self.ctx.natives:
            return self.ctx.natives[t].sym
        s = self.ctx.outside(t)
        if s is None:
            raise XError(f'${self.cur:04X}: transfer to ${t:04X} not resolvable')
        return s

    def pushed_return(self, addrs, idx):
        """LDA #hi / PHA / LDA #lo / PHA (no entry inside) pushing (hi:lo) for an RTS to hi:lo + 1 that is code:
        the 6809 RTS needs the native label there, high byte first.  Called at the first LDA: returns the label
        (the caller emits LDX # / PSHS X for LDA #hi / PHA; LDA #lo is translated as usual, the second PHA is
        skipped), else None."""
        if idx + 3 >= len(addrs):
            return None
        g = [self.code.insns[addrs[idx + k]] for k in range(4)]
        if not (g[0].mn == 'LDA' and g[0].mode == 'imm' and g[1].mn == 'PHA' and g[2].mn == 'LDA'
                and g[2].mode == 'imm' and g[3].mn == 'PHA'):
            return None
        if self._xfer is None:       # entries a transfer in this set reaches (labels may list every instruction)
            self._xfer = {i.target for i in self.code.insns.values() if i.mode == 'rel' or i.mn in ('JMP', 'JSR')}
        if any(addrs[idx + k + 1] != g[k].next for k in range(3)) or any(addrs[idx + k] in self._xfer for k in (1, 2, 3)):
            return None
        t = ((g[0].arg << 8 | g[2].arg) + 1) & 0xFFFF
        if t not in self.code.insns and t not in self.ctx.natives and self.ctx.outside(t) is None:
            return None
        try:
            sym = self.target_sym(t, 'jmp')
        except XError:
            return None
        if sym is None:
            return None
        self.skip.add(addrs[idx + 1])
        self.skip.add(addrs[idx + 3])
        self.pushed.append((addrs[idx], t))
        return sym

    # ---- operands
    def hires_access(self, ins):
        """may this memory instruction reach the Apple hi-res pages ($2000-$5FFF)?"""
        if self.ctx.hires is None:
            return False
        m, a = ins.mode, ins.arg
        if m in ('imm', 'zp', 'zpx', 'zpy') or (m in ('abs', 'absx', 'absy') and ins.addr not in self.ctx.smc_imm
                                                  and a >= 0xC000):
            return False
        if m == 'abs' and ins.addr not in self.ctx.smc_imm:
            return 0x2000 <= a < 0x6000
        if m in ('absx', 'absy') and ins.addr not in self.ctx.smc_imm and (a + 255 < 0x2000 or a >= 0x6000):
            return False
        ea = self.ctx.hires.get(ins.addr)
        # not profiled: assume it may (the helper is right for any RAM).  ROM (64): hr_wr drops writes there like
        # the Apple (a plain store would overwrite the CoCo runtime / code banks at $C000-$FFFF)
        return ea is None or bool(ea & (8 | 64))

    def rom_access(self, ins):
        """may this indirect / patched read reach a ROMDATA range (the profile saw a ROM access, or never ran it)?"""
        if not self.ctx.romdata or ins.mode not in ('indy', 'indx') and ins.addr not in self.ctx.smc_imm:
            return False
        ea = (self.ctx.hires or {}).get(ins.addr) if self.ctx.hires is not None else self.ctx.ea.get(ins.addr)
        return ea is None or bool(ea & 64)

    def operand(self, ins, kind='r'):
        """-> (pre lines as (op,arg) list, 6809 operand text, io (rd, wr) or None).  kind r/w/m"""
        pre, opd, io = self._operand(ins, kind)
        if io is None and kind == 'r' and opd == ',x' and self.rom_access(ins):
            # xrd (gen: ROMDATA tables, then hi-res, then plain RAM): one byte through IOV
            return pre + [('jsr', 'xrd')], '>IOV', None
        if io is None and opd not in ('#', ) and not opd.startswith('#') and not opd.startswith('<') \
                and self.hires_access(ins):
            # one byte through IOV with the hi-res blocks mapped in (runtime hr_rd / hr_wr); X = the address
            if opd.startswith('>'):
                pre = pre + [('ldx', '#' + opd[1:])]
            elif opd != ',x':
                pre = pre + [('leax', opd)]
            if kind in ('r', 'm'):
                pre = pre + [('jsr', 'hr_rd')]
            return pre, '>IOV', ((None, 'hr_wr') if kind in ('w', 'm') else None)
        return pre, opd, io

    def _operand(self, ins, kind='r'):
        m, a = ins.mode, ins.arg
        if kind == 'r' and m in ('abs', 'absx', 'absy') and ins.addr not in self.ctx.smc_imm:
            for lo, hi in self.ctx.romdata:              # ROM bytes read as data (spec ROMDATA)
                if lo <= a <= hi:
                    lab = f'ROM_{lo:04X}+{a - lo}'
                    if m == 'abs':
                        return [], f'>{lab}', None
                    if m == 'absx':
                        return [('ldx', f'#{lab}'), ('abx', '')], ',x', None
                    return [('ldx', '>RYW')], f'{lab},x', None
        if m == 'imm':
            if ins.addr in self.ctx.smc_imm:
                return [], f'>${(ins.addr + 1) & 0xFFFF:04X}', None
            return [], f'#${a:02X}', None
        if m == 'zp':
            return [], f'<${a:02X}', None
        if m in ('abs', 'absx', 'absy') and ins.addr in self.ctx.smc_imm:
            # operand SMC on an address: the effective address comes from the (little-endian) operand bytes in
            # memory at run time (helper xsm<a|x|y>_HHHH, like the (zp),Y helpers; A, B, CC kept).  A patched
            # address must stay in RAM: a native has to replace code whose patched operand points at I/O.
            k = {'abs': 'a', 'absx': 'x', 'absy': 'y'}[m]
            self.ptrs.add((k, (ins.addr + 1) & 0xFFFF))
            return [('jsr', f'xsm{k}_{(ins.addr + 1) & 0xFFFF:04X}')], ',x', None
        if m == 'abs':
            if kind == 'r' and a in self.ctx.io_mem:
                return [], f'>{self.ctx.io_mem[a]}', None
            if 0xC000 <= a <= 0xC0FF:
                if a not in self.ctx.io:
                    raise XError(f'${ins.addr:04X}: I/O ${a:04X} not mapped')
                return [], '>IOV', self.ctx.io[a]
            if a >= 0xC000:
                if kind != 'r' or a not in self.ctx.romconst:
                    raise XError(f'${ins.addr:04X}: ROM/IO access ${a:04X} ({kind})')
                return [], f'#${self.ctx.romconst[a]:02X}', None
            return [], f'>${a:04X}', None
        if m == 'absx' and kind == 'r' and a in self.ctx.io_mem:   # latch bytes are consecutive like the I/O
            return [('ldx', f'#{self.ctx.io_mem[a]}'), ('abx', '')], ',x', None
        if m == 'absx' and 0xC000 <= a <= 0xC0FF:      # indexed I/O: handler gets the 6502 X in B
            return [], '>IOV', (f'io_rdx_{a:04X}', f'io_wrx_{a:04X}')
        if m in ('zpx', 'zpy') and ins.addr in self.ctx.zp_wrap:
            # the 6502 index wraps inside zero page: X = (zp + index) & $FF (A, B, CC kept)
            ld = [('ldb', '>RY')] if m == 'zpy' else []
            return [('pshs', 'cc,b')] + ld + [('addb', f'#${a:02X}'), ('ldx', '#0'), ('abx', ''),
                                              ('puls', 'cc,b')], ',x', None
        if m in ('zpx', 'absx'):
            if m == 'absx' and a >= 0xC000:
                raise XError(f'${ins.addr:04X}: indexed I/O/ROM ${a:04X},X')
            return [('ldx', f'#${a:04X}'), ('abx', '')], ',x', None
        if m in ('zpy', 'absy'):
            if m == 'absy' and a >= 0xC000:
                raise XError(f'${ins.addr:04X}: indexed I/O/ROM ${a:04X},Y')
            return [('ldx', '>RYW')], f'${a:04X},x', None
        if m == 'indy':
            self.ptrs.add(a)
            return [('jsr', f'xiy_{a:02X}')], ',x', None
        if m == 'indx':                                # (zp,X): pointer at zp + X (6502 X = B)
            self.ptrs.add(('i', a))
            return [('jsr', f'xix_{a:02X}')], ',x', None
        raise XError(f'${ins.addr:04X}: mode {m} not supported')

    # ---- instruction emission
    def emit_core(self, ins, after):
        """emit one non-control instruction; returns nothing; updates self.st"""
        mn, st = ins.mn, self.st
        u, d = use_def(ins) if mn not in ('JSR', 'JMP', 'RTS') else (0, 0)

        def wrap_needed(mods, nz_from):
            """the 6502 instruction does not define N/Z but the template clobbers them"""
            if not after & NZ:
                return False
            if st.nzok and nz_from is not None and nz_from == st.rec and nz_from not in mods:
                return False
            if st.rec in ('a', 'b', 'ry') and st.rec not in mods:
                st.nzok = False          # recover lazily
                return False
            return True

        def body(lines, mods, nz_from, defines_nz, io=None, kind='r'):
            """lines: list of (op,arg). mods: locations changed; nz_from: what CC N/Z reflect afterwards"""
            if defines_nz:
                if io and io[0] and kind in ('r', 'm'):
                    self.e('jsr', io[0])
                for op, arg in lines:
                    self.e(op, arg)
                if io and io[1] and kind in ('w', 'm'):
                    self.e('jsr', io[1])
                st.nzok, st.rec = True, nz_from
                return
            wrap = wrap_needed(mods, nz_from)
            if wrap:
                self.e('pshs', 'cc')
            if io and io[0] and kind in ('r', 'm'):
                self.e('jsr', io[0])
            for op, arg in lines:
                self.e(op, arg)
            if io and io[1] and kind in ('w', 'm'):
                self.e('jsr', io[1])
            if wrap:
                self.e('puls', 'cc')
            elif st.nzok and nz_from == st.rec and nz_from is not None:
                pass
            elif after & NZ:
                st.nzok = False
            if st.rec in mods and not wrap:
                if after & NZ and not st.nzok:
                    raise XError(f'${ins.addr:04X}: lost N/Z source')
                st.rec = None if not st.nzok else st.rec

        def free_reg():
            if not after & A_:
                return 'a'
            if not after & X_:
                return 'b'
            return None

        if mn in ('LDA', 'ORA', 'AND', 'EOR', 'ADC', 'SBC', 'CMP', 'LDX', 'CPX', 'BIT'):
            pre, opd, io = self.operand(ins)
            if mn == 'ADC' and st.cinv:
                self.flip_c()
            if mn == 'SBC' and not st.cinv:
                self.flip_c()
            if mn == 'BIT':
                if after & N_:
                    r = free_reg()
                    lines = pre + ([] if r == 'b' else [('pshs', 'b')]) + [('ldb', opd), ('jsr', 'xbitb')] + \
                        ([] if r == 'b' else [('puls', 'b')])
                    body(lines, {'b'} if r == 'b' else set(), None, True, io)
                else:
                    body(pre + [('bita', opd)], set(), None, True, io)
                return
            op6809 = {'LDA': 'lda', 'ORA': 'ora', 'AND': 'anda', 'EOR': 'eora', 'ADC': 'adca', 'SBC': 'sbca',
                      'CMP': 'cmpa', 'LDX': 'ldb', 'CPX': 'cmpb'}[mn]
            reg = 'b' if mn in ('LDX', 'CPX') else 'a'
            body(pre + [(op6809, opd)], {reg} if mn not in ('CMP', 'CPX') else set(),
                 None if mn in ('CMP', 'CPX') else reg, True, io)
            if mn in ('CMP', 'CPX', 'SBC'):
                st.cinv = True
            elif mn == 'ADC':
                st.cinv = False
            return
        if mn in ('LDY', 'CPY'):
            pre, opd, io = self.operand(ins)
            if mn == 'LDY':
                if ins.mode == 'imm' and ins.addr not in self.ctx.smc_imm and (ins.arg < 0x80 or not after & N_):
                    body([('ldx', f'#${ins.arg:02X}'), ('stx', '>RYW')], {'ry'}, 'ry', True)
                    return
                r = free_reg()
                if r:
                    body(pre + [(f'ld{r}', opd), (f'st{r}', '>RY')], {r, 'ry'}, 'ry', True, io)
                else:
                    body(pre + [('pshs', 'a'), ('lda', opd), ('sta', '>RY'), ('puls', 'a')], {'ry'}, 'ry', True, io)
                return
            r = free_reg()
            if r:
                body(pre + [(f'ld{r}', '>RY'), (f'cmp{r}', opd)], {r}, None, True, io)
            else:
                body(pre + [('pshs', 'b'), ('ldb', '>RY'), ('cmpb', opd), ('puls', 'b')], set(), None, True, io)
            st.cinv = True
            return
        if mn in ('STA', 'STX', 'STY'):
            pre, opd, io = self.operand(ins, 'w')
            if mn == 'STY':
                r = free_reg()
                if r:
                    body(pre + [(f'ld{r}', '>RY'), (f'st{r}', opd)], {r}, 'ry', False, io, 'w')
                else:
                    body(pre + [('pshs', 'a'), ('lda', '>RY'), ('sta', opd), ('puls', 'a')], set(), 'ry', False,
                         io, 'w')
                return
            reg = 'a' if mn == 'STA' else 'b'
            body(pre + [(f'st{reg}', opd)], set(), reg, False, io, 'w')
            return
        if mn in ('INC', 'DEC') or (mn in SHIFTS and ins.mode != 'acc'):
            pre, opd, io = self.operand(ins, 'm')
            if mn in ('ROL', 'ROR') and st.cinv:
                self.flip_c()
            body(pre + [(mn.lower(), opd)], set(), None, True, io, 'm')
            if mn in SHIFTS:
                st.cinv = False
            return
        if mn in SHIFTS:
            if mn in ('ROL', 'ROR') and st.cinv:
                self.flip_c()
            body([(mn.lower() + 'a', '')], {'a'}, 'a', True)
            st.cinv = False
            return
        simple = {
            'INX': ([('incb', '')], {'b'}, 'b', True), 'DEX': ([('decb', '')], {'b'}, 'b', True),
            'INY': ([('inc', '>RY')], {'ry'}, 'ry', True), 'DEY': ([('dec', '>RY')], {'ry'}, 'ry', True),
            'TAY': ([('sta', '>RY')], {'ry'}, 'a', True), 'TYA': ([('lda', '>RY')], {'a'}, 'a', True),
        }
        if mn in simple:
            body(*simple[mn])
            return
        if mn in ('TAX', 'TXA', 'PLA'):
            self.e(*{'TAX': ('tfr', 'a,b'), 'TXA': ('tfr', 'b,a'), 'PLA': ('puls', 'a')}[mn])
            st.rec, st.nzok = ('b' if mn == 'TAX' else 'a'), False
            return
        if mn == 'PHA':
            self.e('pshs', 'a')
            return
        if mn in ('CLC', 'SEC'):
            if after & C_:
                self.e('andcc', '#$FE') if mn == 'CLC' else self.e('orcc', '#$01')
            st.cinv = False
            return
        if mn in ('NOP', 'CLD', 'CLI', 'SEI'):
            return
        raise XError(f'${ins.addr:04X}: {mn} {ins.mode} not supported')

    def emit_branch(self, ins):
        """a conditional branch to ins.target (the fall-through is the caller's business)"""
        L = self.live
        flag, val = BRANCH[ins.mn]
        t = ins.target
        sym = self.target_sym(t, 'br')
        if flag == 'V':
            # 6809 ADCA/SBCA set V exactly like 6502 ADC/SBC (signed overflow; SBC's inverted borrow does not
            # change it).  Allowed when the branch directly follows ADC/SBC (only way in) and nothing between
            # touches V: canon may flip C (ORCC/ANDCC) but must not regenerate N/Z (TST clears V)
            p = next((q for q in (self.code.insns.get(ins.addr - k) for k in (2, 3)) if q is not None
                      and q.next == ins.addr), None)
            other_in = self.code.targets.get(ins.addr, set()) - {'prof'}
            if p is None or p.mn not in ('ADC', 'SBC') or other_in:
                raise XError(f'${ins.addr:04X}: {ins.mn} supported only right after ADC/SBC')
            if L.lb(t) & NZ and not self.st.nzok:
                raise XError(f'${ins.addr:04X}: {ins.mn}: N/Z would have to be regenerated (clears V)')
            self.canon(L.lb(t) & C_)
            if sym:
                self.e(ins.mn.lower(), sym)
            else:
                skip = self.tmp()
                self.e('bvs' if ins.mn == 'BVC' else 'bvc', skip)
                self.e('jsr', 'x_trap')
                self.out.append(f'\tfdb\t${t:04X}')
                self.out.append(skip)
            return
        need = (N_ if flag == 'N' else Z_ if flag == 'Z' else C_)
        tl = L.lb(t) & FLAGS
        if need & NZ and not self.st.nzok:
            self.regen_nz()
        self.canon(tl)
        if flag == 'C':
            taken_if_cs = (val == 1) != self.st.cinv
            op = 'bcs' if taken_if_cs else 'bcc'
        else:
            op = ins.mn.lower()
        if sym is None:
            skip = self.tmp()
            inv = {'bcs': 'bcc', 'bcc': 'bcs', 'beq': 'bne', 'bne': 'beq', 'bpl': 'bmi', 'bmi': 'bpl'}
            self.e(inv[op], skip)
            self.e('jsr', 'x_trap')
            self.out.append(f'\tfdb\t${t:04X}')
            self.out.append(skip)
        else:
            self.e(op, sym)

    def translate(self):
        code, L = self.code, self.live
        addrs = sorted(code.insns)
        self.st = _St()
        prev_flow = False          # previous instruction falls through into this one
        for idx, a in enumerate(addrs):
            ins = code.insns[a]
            self.cur = a
            if a in self.labels or not prev_flow:
                if prev_flow:
                    self.canon(L.lb(a))
                self.out.append(f'{self.lab(a)}')
                self.st = _St()
            if self.comments:
                self.out.append(f'* {a:04X}  {ins.text()}')
            if a in self.skip:             # second PHA of a pushed return address (below)
                continue
            if ins.mn in REJECT or ins.mode == 'ind':
                raise XError(f'${a:04X}: {ins.text()} not supported')
            pr = self.pushed_return(addrs, idx)
            if pr:
                self.e('ldx', f'#{pr}')
                self.e('pshs', 'x')
                prev_flow = True
                continue
            after = L.after(a)
            mn = ins.mn
            nxt_cont = idx + 1 < len(addrs) and addrs[idx + 1] == ins.next
            if mn == 'JSR':
                t = ins.arg
                sym = self.target_sym(t, 'jsr')
                if sym is None:
                    self.e('jsr', 'x_trap')
                    self.out.append(f'\tfdb\t${t:04X}')
                else:
                    self.canon(L.lb(t) & FLAGS)
                    self.e('jsr', sym)
                    nt = self.ctx.natives.get(t)
                    if nt and not nt.noreturn:
                        nt.outputs |= after & nt.defs
                    if nt and nt.noreturn:
                        prev_flow = False
                        continue
                self.st = _St()
                prev_flow = True
                if not nxt_cont:
                    self.canon(L.lb(ins.next))
                    sym2 = self.target_sym(ins.next, 'jmp')
                    self.e('jmp', sym2) if sym2 else (self.e('jsr', 'x_trap'), self.out.append(f'\tfdb\t${ins.next:04X}'))
                    prev_flow = False
                continue
            if mn == 'JMP' and a in getattr(code, 'jmp_multi', {}):
                # patched JMP operand: dispatch on the operand word in memory (little endian) to the known targets
                self.canon(after & FLAGS)
                op = (a + 1) & 0xFFFF
                labs = []
                self.e('pshs', 'cc,d')
                self.e('ldd', f'>${op:04X}')
                for t in code.jmp_multi[a]:
                    lab = self.tmp()
                    labs.append((t, lab))
                    self.e('cmpd', f'#${(t & 0xFF) << 8 | t >> 8:04X}')
                    self.e('lbeq', lab)
                self.e('puls', 'cc,d')
                self.e('jsr', 'x_trap')
                self.out.append(f'\tfdb\t${a:04X}')
                for t, lab in labs:
                    self.out.append(lab)
                    self.e('puls', 'cc,d')
                    sym = self.target_sym(t, 'jmp')
                    if sym:
                        self.e('jmp', sym)
                    else:
                        self.e('jsr', 'x_trap')
                        self.out.append(f'\tfdb\t${t:04X}')
                prev_flow = False
                continue
            if mn == 'JMP':
                t = ins.arg
                sym = self.target_sym(t, 'jmp')
                if sym is None:
                    self.e('jsr', 'x_trap')
                    self.out.append(f'\tfdb\t${t:04X}')
                else:
                    self.canon(L.lb(t) & FLAGS)
                    self.e('jmp', sym)
                    nt = self.ctx.natives.get(t)
                    if nt and not nt.noreturn:
                        nt.outputs |= nt.defs        # tail call: its RTS returns to our caller
                prev_flow = False
                continue
            if mn == 'RTS':
                self.canon(L.rts_live(a) & FLAGS)
                self.e('rts')
                prev_flow = False
                continue
            if a in getattr(code, 'op_variants', {}):
                # opcode SMC (spec SMC_OPS): dispatch on the opcode byte in memory to each observed variant
                self.canon(L.lb(a))
                join = self.tmp()
                labs = [(vi, self.tmp()) for vi in code.op_variants[a]]
                self.e('pshs', 'cc,a')
                self.e('lda', f'>${a:04X}')
                for vi, lab in labs:
                    self.e('cmpa', f'#${vi.op:02X}')
                    self.e('lbeq', lab)
                self.e('puls', 'cc,a')
                self.e('jsr', 'x_trap')
                self.out.append(f'\tfdb\t${a:04X}')
                for vi, lab in labs:
                    self.out.append(lab)
                    self.e('puls', 'cc,a')
                    self.st = _St()
                    if vi.mode == 'rel':
                        self.emit_branch(vi)
                    else:
                        self.emit_core(vi, (self.live.lb(vi.next)))
                    self.canon(L.lb(ins.next))
                    self.e('jmp', join)
                self.out.append(join)
                self.st = _St()
                prev_flow = True
                if not nxt_cont:
                    sym2 = self.target_sym(ins.next, 'jmp')
                    if sym2:
                        self.e('jmp', sym2)
                    else:
                        self.e('jsr', 'x_trap')
                        self.out.append(f'\tfdb\t${ins.next:04X}')
                    prev_flow = False
                continue
            if ins.mode == 'rel':
                self.emit_branch(ins)
                if a in code.dead_fall:
                    prev_flow = False
                    continue
                prev_flow = True
                if not nxt_cont:
                    self.canon(L.lb(ins.next))
                    sym2 = self.target_sym(ins.next, 'jmp')
                    if sym2:
                        self.e('jmp', sym2)
                    else:
                        self.e('jsr', 'x_trap')
                        self.out.append(f'\tfdb\t${ins.next:04X}')
                    prev_flow = False
                continue
            self.emit_core(ins, after)
            prev_flow = True
            if not nxt_cont:
                self.canon(L.lb(ins.next))
                sym2 = self.target_sym(ins.next, 'jmp')
                if sym2:
                    self.e('jmp', sym2)
                else:
                    self.e('jsr', 'x_trap')
                    self.out.append(f'\tfdb\t${ins.next:04X}')
                prev_flow = False
        return self.out


def xiy_helpers(ptrs):
    """(zp),Y address helpers: X = word(zp) + Y; A, B, CC kept.  Operand-SMC helpers (k, operand address):
    xsma_HHHH X = word(HHHH); xsmx_ X = word + 6502 X (B); xsmy_ X = word + Y"""
    out = []
    for k, h in sorted(p for p in ptrs if isinstance(p, tuple) and p[0] == 'i'):
        # (zp,X): X = word at (zp + X) & $FF (the high byte wraps to $00 only when zp + X = $FF, as on the 6502)
        out += [f'xix_{h:02X}\tpshs\tcc,d,y', f'\taddb\t#${h:02X}', '\tclra', '\ttfr\td,x', '\tincb', '\ttfr\td,y',
                '\tlda\t,y', '\tldb\t,x', '\ttfr\td,x', '\tpuls\tcc,d,y,pc']
    for k, h in sorted(p for p in ptrs if isinstance(p, tuple) and p[0] != 'i'):
        add = {'a': [], 'x': ['\taddb\t2,s', '\tadca\t#0'], 'y': ['\taddd\t>RYW']}[k]
        out += [f'xsm{k}_{h:04X}\tpshs\tcc,d', f'\tlda\t>${(h + 1) & 0xFFFF:04X}', f'\tldb\t>${h:04X}'] + add + \
               ['\ttfr\td,x', '\tpuls\tcc,d,pc']
    for z in sorted(p for p in ptrs if not isinstance(p, tuple)):
        out += [f'xiy_{z:02X}\tpshs\tcc,d', f'\tlda\t<${(z + 1) & 0xFF:02X}', f'\tldb\t<${z:02X}',
                '\taddd\t>RYW', '\ttfr\td,x', '\tpuls\tcc,d,pc']
    return out


def translate(code, prefix, ctx, comments=True, live=None):
    em = Emitter(code, prefix, ctx, live=live, comments=comments)
    lines = em.translate()
    return lines, em
