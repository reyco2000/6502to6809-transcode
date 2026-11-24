"""No silent I/O defaults: check the I/O handler symbols generated code uses against the hardware map.

    errors, conds = check(used, overrides, io_mem)     (kit/gen.py, after writing the sources)
    lines = stubs(used, defined, overrides)            (kit/build512.py, kit/build128.py)

A symbol io_<mode>_<ADDR> is fine when its map action is Runtime (the runtime defines it; Runtime(cond) only when
the build sets the equate `cond`), Latch or Ignore (the build emits a stub).  Trap, IdiomOnly and unmapped
addresses are errors; a port allows them with spec HW_OVERRIDES = {addr: Ignore() | Latch(sym) | Runtime()}.
"""
import re

from . import Runtime, Latch, Ignore, Trap, IdiomOnly, parse_io_symbol
from . import apple2

_USED = re.compile(r'\bio_(?:rdx?|wrx?)_C0[0-9A-F]{2}\b')


def used_symbols(lines_sets):
    """the I/O handler symbols the generated source references"""
    used = set()
    for lines in lines_sets:
        for ln in lines:
            used |= set(_USED.findall(ln))
    return used


def _problem(sym, overrides, io_mem):
    """-> (error or None, cond or None, entry)"""
    mode, a = parse_io_symbol(sym)
    if io_mem and a in io_mem and mode in ('rd', 'rdx'):
        return None, None, None
    e = apple2.lookup(a, mode, overrides)
    where = f'${a:04X} {mode}'
    if e is None:
        return f'{where}: not in the hardware map (spec HW_OVERRIDES to allow)', None, None
    act = e.action
    if isinstance(act, Trap):
        return f'{where}: {act.reason} (spec HW_OVERRIDES to allow)', None, e
    if isinstance(act, IdiomOnly):
        return (f'{where}: single access to a loop-only switch ({e.name}); see idioms {", ".join(act.kinds)}',
                None, e)
    if isinstance(act, Runtime):
        return None, act.cond, e
    return None, None, e


def check(used, overrides=None, io_mem=None):
    """-> (errors, conds): error lines for symbols with no acceptable action, the build equates needed"""
    errors, conds = [], set()
    for sym in sorted(used):
        err, cond, _ = _problem(sym, overrides, io_mem)
        if err:
            errors.append(err)
        if cond:
            conds.add(cond)
    return errors, conds


def stubs(used, defined, overrides=None):
    """stub lines for the used handlers the runtime does not define: Ignore (read = value, write = nothing) and
    Latch (read = the latch byte).  Anything else undefined stops the build."""
    out, errors = [], []
    for sym in sorted(used - set(defined)):
        err, cond, e = _problem(sym, overrides, None)
        act = e.action if e else None
        if err:
            errors.append(err)
        elif isinstance(act, Ignore):
            if sym.startswith('io_wr'):
                out += [f'{sym}\trts']
            elif act.value == 0:
                out += [f'{sym}\tpshs\tcc', '\tclr\tIOV', '\tpuls\tcc,pc']
            else:
                out += [f'{sym}\tpshs\tcc,a', f'\tlda\t#${act.value:02X}', '\tsta\tIOV', '\tpuls\tcc,a,pc']
        elif isinstance(act, Latch) and sym.startswith('io_rd_'):
            out += [f'{sym}\tpshs\tcc,a', f'\tlda\t{act.sym}', '\tsta\tIOV', '\tpuls\tcc,a,pc']
        else:
            errors.append(f'{sym}: the runtime does not define it' + (f' (needs {cond})' if cond else ''))
    if errors:
        raise SystemExit('I/O handlers:\n  ' + '\n  '.join(errors))
    return out


def runtime_extras(used, overrides=None):
    """sources (relative to rt/) of the Runtime(cond) handlers the generated code needs"""
    _, conds = check(used, overrides)
    return [apple2.COND_SRC[c] for c in sorted(conds)]
