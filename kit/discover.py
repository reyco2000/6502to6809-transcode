"""Code discovery by recursive descent over a 6502 memory image. Game-independent.

discover(mem, roots, native, native_ranges, code_ranges) -> Code
  mem            64K bytearray (Apple RAM image; anything above $BFFF is never translated)
  roots          iterable of addresses known to be instruction starts (entries, profile instruction starts)
  native         {addr: symbol}: transfers to addr call / jump to a native routine; descent stops there
  native_ranges  [(lo, hi)] inclusive: replaced code; a transfer into one that is not in `native` is an error
  code_ranges    [(lo, hi)] inclusive: where translatable code may live (e.g. $0200-$BFFF without hi-res)
  traps          addresses known dead (spec TRAPS): a transfer there becomes `jsr x_trap` and descent stops
  smc_ops        {site: [opcodes]} (spec SMC_OPS): opcode SMC; each observed opcode is translated and chosen at run
                 time by the opcode byte in memory (unknown value -> x_trap)
  smc_jmp        {JMP abs site: [targets]} (spec SMC_JMP): a JMP whose operand is patched, with every target the
                 profile saw; the translation dispatches on the operand at run time (unknown value -> x_trap)
Code.insns {addr: Insn}, Code.targets {addr: set of kinds}, Code.errors [str], Code.preds {addr: [from]}
"""
from .opcodes import decode

ENDS = ('JMP', 'RTS', 'RTI', 'BRK')


class Code:
    def __init__(self):
        self.insns = {}
        self.targets = {}
        self.errors = []
        self.preds = {}
        self.native_refs = {}   # native symbol -> [sites]
        self.outside = {}       # addr -> [(kind, site)] transfers to valid targets descended elsewhere
        self.dead_fall = set()  # branch addresses whose fall-through is never taken
        self.bad = set()        # addresses that stopped descent with an error (undecodable, BRK/RTI, outside)
        self.trap_refs = {}     # trap addr -> [(kind, site)]
        self.jmp_multi = {}     # JMP abs with a patched operand (spec SMC_JMP): site -> [known targets]
        self.op_variants = {}   # opcode SMC site (spec SMC_OPS) -> [Insn per observed opcode]


def _in(ranges, a):
    return any(lo <= a <= hi for lo, hi in ranges)


def discover(mem, roots, native=None, native_ranges=(), code_ranges=((0x0000, 0xBFFF),), outside=None, prof=(),
             traps=(), smc_jmp=None, smc_ops=None):
    """prof: extra instruction starts (profile) that are not transfer targets.
    outside(a) -> True: a is a valid target that is not descended here (recorded in Code.outside)"""
    native = native or {}
    dead = set()
    while True:
        c = _discover(mem, roots, prof, native, native_ranges, code_ranges, outside, dead, set(traps), smc_jmp or {},
                      smc_ops or {})
        nd = always_taken(c, mem)
        if nd <= dead:
            c.dead_fall = dead
            return c
        dead |= nd


def always_taken(c, mem):
    """branches whose fall-through is dead: preceded (only way in) by LDA/LDX/LDY #imm or CLC/SEC that
    decides the tested flag"""
    dead = set()
    for a, ins in c.insns.items():
        if ins.mode != 'rel' or c.targets.get(a, {'prof'}) - {'prof'}:
            continue
        flag, val = {'BNE': ('Z', 0), 'BEQ': ('Z', 1), 'BPL': ('N', 0), 'BMI': ('N', 1),
                     'BCC': ('C', 0), 'BCS': ('C', 1)}.get(ins.mn, (None, None))
        if flag is None:
            continue
        # look back over stores (they do not change flags) to the instruction that sets the tested flag
        b = a
        while True:
            st = next((c.insns.get(b - k) for k in (2, 3) if c.insns.get(b - k) is not None
                       and c.insns[b - k].next == b and c.insns[b - k].mn in ('STA', 'STX', 'STY')
                       and not (c.targets.get(b, set()) - {'prof'})), None)
            if st is None:
                break
            b = st.addr
        p = c.insns.get(b - 2)
        q = c.insns.get(b - 1)
        if p is not None and p.len == 2 and p.mn in ('LDA', 'LDX', 'LDY') and p.mode == 'imm' and flag != 'C':
            v = (p.arg == 0) if flag == 'Z' else (p.arg >= 0x80)
        elif q is not None and q.mn in ('CLC', 'SEC') and flag == 'C':
            v = q.mn == 'SEC'
        else:
            continue
        if bool(v) == bool(val):
            dead.add(a)
    return dead


def suggest_traps(c, executed):
    """for every error stop, the first never-executed instruction on each path from executed code to it: candidates
    for spec TRAPS (paths the profile never took that run into data).  Returns {trap addr: [bad addrs]}, empty for
    a bad address that executed code reaches directly (a real problem, not a dead path)."""
    out = {}
    for b in sorted(c.bad):
        seen, work = set(), [b]
        while work:
            x = work.pop()
            if x in seen:
                continue
            seen.add(x)
            if x in executed:
                continue
            ps = [p for p in c.preds.get(x, []) if p >= 0]
            if any(p in executed for p in ps) or not ps:
                out.setdefault(x, []).append(b)
            work += [p for p in ps if p not in executed]
    return out


def _discover(mem, roots, prof, native, native_ranges, code_ranges, outside, dead, traps=frozenset(), smc_jmp=None,
              smc_ops=None):
    smc_jmp, smc_ops = smc_jmp or {}, smc_ops or {}
    c = Code()
    work = []

    def add(a, kind, frm):
        c.preds.setdefault(a, []).append(frm)
        if kind != 'fall':
            c.targets.setdefault(a, set()).add(kind)
        if a in native:
            c.native_refs.setdefault(native[a], []).append(frm)
            return
        if a in traps:
            c.trap_refs.setdefault(a, []).append((kind, frm))
            return
        if _in(native_ranges, a):
            c.errors.append(f'${frm:04X}: {kind} into replaced range at ${a:04X} (not a native entry)')
            return
        if outside is not None and outside(a):
            c.outside.setdefault(a, []).append((kind, frm))
            return
        if not _in(code_ranges, a):
            c.errors.append(f'${frm:04X}: {kind} to ${a:04X} outside translatable code')
            c.bad.add(frm)
            return
        work.append(a)

    for r in roots:
        add(r, 'root', -1)
    for r in prof:
        add(r, 'prof', -1)
    while work:
        a = work.pop()
        if a in c.insns:
            continue
        ins = decode(mem, a)
        if a in smc_ops:
            # opcode SMC: every observed opcode, with the operand bytes in memory; same length, no JSR/JMP/RTS
            vs = []
            # the image's own byte is a variant too (it may run before the first patch, or be another image's
            # code at the same address): added when it is a compatible instruction
            own = [mem[a]] if mem[a] not in smc_ops[a] and ins is not None and ins.mn not in (
                'JSR', 'JMP', 'RTS', 'RTI', 'BRK') else []
            for op in list(smc_ops[a]) + own:
                vi = decode(bytes([op]) + bytes(mem[a + 1:a + 3]) + bytes(3), 0)
                if vi is None or (ins is not None and vi.len != ins.len) or vi.mn in ('JSR', 'JMP', 'RTS', 'RTI', 'BRK'):
                    c.errors.append(f'${a:04X}: SMC_OPS variant ${op:02X} not supported (length / control flow)')
                    continue
                vi = decode(bytes(mem[:a]) + bytes([op]) + bytes(mem[a + 1:a + 3]) + bytes(max(0, 0x10000 - a - 3)), a)
                vs.append(vi)
            if vs:
                c.op_variants[a] = vs
                ins = vs[0] if ins is None else ins
                for vi in vs:
                    if vi.mode == 'rel':
                        add(vi.target, 'br', a)
        if ins is None:
            c.errors.append(f'${a:04X}: undocumented opcode ${mem[a]:02X} (from {[hex(p) for p in c.preds.get(a, [])][:4]})')
            c.bad.add(a)
            continue
        # overlap check: does this instruction share bytes with an already decoded different instruction?
        for k in range(1, ins.len):
            if a + k in c.insns:
                c.errors.append(f'${a:04X}: overlaps instruction at ${a + k:04X}')
        c.insns[a] = ins
        t = ins.target
        if ins.mn == 'JSR':
            add(t, 'jsr', a)
            add(ins.next, 'ret', a)
        elif ins.mn == 'JMP':
            if ins.mode == 'abs' and a in smc_jmp:
                c.jmp_multi[a] = sorted(smc_jmp[a])
                for t2 in c.jmp_multi[a]:
                    add(t2, 'jmp', a)
            elif ins.mode == 'abs':
                add(t, 'jmp', a)
            else:
                c.errors.append(f'${a:04X}: JMP (${ins.arg:04X}) indirect')
        elif ins.mode == 'rel':
            add(t, 'br', a)
            if a not in dead:
                add(ins.next, 'fall', a)
        elif ins.mn in ('RTS',):
            pass
        elif ins.mn in ('RTI', 'BRK'):
            c.errors.append(f'${a:04X}: {ins.mn}')
            c.bad.add(a)
        else:
            add(ins.next, 'fall', a)
    for a, ins in c.insns.items():
        for k in range(1, ins.len):
            if a + k in c.insns:
                c.errors.append(f'${a:04X}: overlaps instruction at ${a + k:04X}')
    c.errors = sorted(set(c.errors))
    return c
