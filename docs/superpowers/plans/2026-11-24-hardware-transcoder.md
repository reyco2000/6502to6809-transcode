# Hardware Transcoder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Apple II hardware map that drives the whole kit, a library of CoCo routines for standard hardware idioms, matchers that propose them in the draft spec with test evidence, and a ranked "least likely to transcode" guide.

**Architecture:**
- `kit/hw/apple2.py` holds the map. The generator, survey, builds and docs read it, and nothing falls back to a silent default any more.
- Idioms the user accepts (`HW_IDIOMS` in `spec.py`) expand into ordinary `NATIVE`/`NATIVE_RANGES`/`NATIVE_EXITS` entries. The routines behind them are rendered from templates in `rt/hw/apple2/`.
- The matchers (`kit/hw/match.py`) only propose. `kit/hw/verify.py` attaches difftest evidence, including the timing of hardware events.

**Tech Stack:** Python 3.11+ (stdlib only), lwasm (`~/opt/lwtools/lwasm/lwasm` via `kit/asm.py`), `sim/cc3sim` (C, gcc), the existing `kit/difftest.py` harness.

**Spec:** `docs/superpowers/specs/2026-11-24-hardware-transcoder-design.md`

## Global Constraints
- Matches are never auto-applied: nothing a matcher finds changes the build until the user moves it into `spec.py`.
- No silent defaults: every hardware address translated code touches has an explicit map action or a spec override, or generation fails.
- The reference port regression: `<ref>_512k.dsk` and `<ref>_128k.dsk` stay byte-identical with the current spec. New runtime code is assembled only when a port uses it.
- Never commit game bytes, ROMs, snapshots, generated source or disks: everything derived goes to `build/`.
- Native contract (`docs/NATIVES.md` §2): A = A, B = X, `RY` = Y, S = 6502 stack, DP = 0; produce exactly the registers/flags/RAM the callers read.
- Apple time: `APPLE_TO_COCO = 1.75` CoCo cycles per Apple cycle.
- Timing tolerance for timed idioms: ±2% (`TIMING_TOL = 0.02`). Delay candidates: loops of at least 500 Apple cycles (`DELAY_MIN = 500`).
- Out of scope: C64, converting text/lo-res/mixed/double hi-res, language card, aux memory, multi-voice players.
- Tests follow the repo's style: `check(name, ok)` functions run by `python3 -m tests.selftest`, with no pytest. New checks live in `tests/hw_checks.py`, which also has its own `main()` so `python3 -m tests.hw_checks [GROUP]` runs one group.

## Before you start
- The repo has **no commits yet** (everything is untracked). Ask the user before making the first commit; the commit steps below assume they agreed.
- Build the simulator: `make -C sim`. Baseline: `python3 -m tests.selftest` → `ALL OK`.
- If the reference port's disk and ROMs are present (paths in `ports/<ref>/port.toml`), save the baseline disks:
  `python3 -m ports.<ref>.build512 && python3 -m ports.<ref>.build128 && mkdir -p build/<ref>/baseline && cp build/<ref>/<ref>_*k.dsk build/<ref>/baseline/`.
  Every task that says "reference port identity" means: rebuild both and `cmp` against the baseline. Without the disk, say so in the task report; don't skip silently.

## Review Focus
1. **A counter that is 0 on entry** (`DEX/BNE` with X = 0 runs 256 passes; a tone period of 0 = 256). The library routines must wrap exactly like the 6502. Tests: Task 6 (`delay`), Task 7 (`tone`).
2. **`$C030` touched by `LDA`, `BIT` or `STA`.** All three click on the Apple, and the matcher must accept each. Test: Task 9.
3. **An accepted idiom that overlaps an existing `NATIVE`/`NATIVE_RANGES` entry or another idiom** must be a clear error, never a silent merge. Test: Task 6.
4. **A spec `IO_MEM` that also names an address the map gives a different latch:** the spec wins, as today. Test: Task 3.
5. **Indexed slot I/O** (`LDA $C080,X` with X = `$60`, i.e. `io_rdx_C080`) must resolve to slot I/O (Disk II trap), not to the language card. Test: Task 1.

---

## Phase 1: the hardware map

### Task 1: Map data model and Apple II table

**Files:**
- Create: `kit/hw/__init__.py`, `kit/hw/apple2.py`, `tests/hw_checks.py`
- Modify: `tests/selftest.py` (call `hw_checks.run(check)` from `main`)

**Interfaces:**
- Produces, in `kit/hw/__init__.py` (machine-neutral):
  - `@dataclass(frozen=True) class Runtime: cond: str | None = None`. The handler `io_<mode>_<ADDR>` is defined by the runtime. If `cond` is set, it is defined only when the build sets that equate (Task 4).
  - `Latch(sym: str)`, `Ignore(value: int = 0)`, `Trap(reason: str)`, `IdiomOnly(kinds: tuple[str, ...])`: frozen dataclasses.
  - `@dataclass(frozen=True) class Hw: lo: int; hi: int; name: str; cat: str; action; modes: frozenset = frozenset({'rd', 'wr'}); doc: str = ''`
  - `parse_io_symbol(sym: str) -> tuple[str, int]`: `'io_rdx_C061'` → `('rdx', 0xC061)`; raises `ValueError` otherwise.
- Produces, in `kit/hw/apple2.py`:
  - `APPLE_TO_COCO = 1.75`, `TIMING_TOL = 0.02`, `DELAY_MIN = 500`
  - `MAP: list[Hw]` (`$C000-$CFFF`) and `ROM: dict[int, Hw]` (entry points; `lo == hi == addr`)
  - `lookup(addr: int, mode: str, overrides: dict | None = None) -> Hw | None`. `overrides` maps an address to an action and returns a copy of the entry with that action. Mode `rdx`/`wrx` at `$C080-$C08F` means slot I/O (`addr + slot*16`), so it resolves to the slot-I/O trap.
  - `rom(addr: int) -> Hw | None`

Table contents (actions as in the design; `cat` values are the survey's existing categories):

| range | modes | action |
|---|---|---|
| `$C000` | rd | `Latch('kb_kbd')` |
| `$C000-$C00F` | wr | `Trap('IIe 80STORE/RAMRD/RAMWRT/ALTZP switches: aux memory not supported')` |
| `$C010` | rd, wr | `Runtime()` |
| `$C011-$C018` | rd | `Trap('language card / aux status')` |
| `$C019` | rd | `Runtime(cond='HW_IOV')` and listed as `IdiomOnly(('vbl_poll',))` in `doc`. A single read is allowed; loops are proposed by the matcher. |
| `$C01A-$C01D` | rd | `Runtime(cond='HW_IOV')` |
| `$C01E-$C01F` | rd | `Ignore(0)` |
| `$C020-$C02F` | rd, wr | `Ignore(0)` (cassette out) |
| `$C030-$C03F` | rd, wr | `$C030`: `Runtime()`; `$C031-$C03F`: `Ignore(0)` |
| `$C050-$C053`, `$C056`, `$C057` | rd, wr | `Runtime(cond='HW_IOV')` |
| `$C054`, `$C055` | rd, wr | `Runtime()` |
| `$C058-$C05D` | rd, wr | `Ignore(0)` (annunciators) |
| `$C05E-$C05F` | rd, wr | `Trap('double hi-res (AN3) not supported')` |
| `$C060` | rd | `Ignore(0)` |
| `$C061`, `$C062` | rd, rdx | `Latch('btn0')` / `Latch('btn1')`; `rdx` at `$C061`: `Runtime()` |
| `$C063` | rd | `Ignore(0)` |
| `$C064-$C067`, `$C070` | rd, wr, rdx | `IdiomOnly(('paddle_count', 'rom_pread'))` |
| `$C080-$C08F` | rd, wr | `Trap('language card: see docs/machines/apple2.md')` |
| `$C080-$C0FF` | rdx, wrx; `$C090-$C0FF` also rd, wr | `Trap('slot I/O (Disk II): loaders are dropped, use snapshots + nt_load')` |
| `$C100-$CFFF` | rd | `Trap('slot / expansion ROM')` |

`ROM`: every key of today's `kit/survey.py:ROM_KNOWN`, with the same name and category, plus:
- `$FCA8` → `IdiomOnly(('rom_wait',))`, `$FB1E` → `IdiomOnly(('rom_pread',))`, `$FF3A`/`$FBDD` → `IdiomOnly(('rom_bell',))`;
- `$FC58`/`$FDED`/`$FDF0` → `Trap('text output: text page not displayed')`;
- `$FD0C`/`$FD1B` → `Trap('RDKEY: write a native keyboard wait')`;
- `$FE89`/`$FE93`/`$FB2F` → `Ignore()` (boot init, usually gone with the loader);
- `$F3E2`/`$F3D8` → `Trap('HGR clear: write a fill native')`;
- `$C600`/`$C65C` → `Trap('Disk II: native loader')`.

- [ ] **Step 1: Write the failing checks** in `tests/hw_checks.py` as `def run(check) -> bool` plus `main()`:
```python
from kit.hw import parse_io_symbol, Runtime, Latch, Ignore, Trap, IdiomOnly
from kit.hw import apple2 as A
ok &= check('hw: parse io symbol', parse_io_symbol('io_rdx_C061') == ('rdx', 0xC061))
ok &= check('hw: kbd latch', A.lookup(0xC000, 'rd').action == Latch('kb_kbd'))
ok &= check('hw: speaker runtime', A.lookup(0xC030, 'wr').action == Runtime())
ok &= check('hw: slot rdx is slot I/O', 'slot' in A.lookup(0xC080, 'rdx').action.reason)   # Review Focus 5
ok &= check('hw: LC rd', 'language card' in A.lookup(0xC080, 'rd').action.reason)
ok &= check('hw: override', A.lookup(0xC05E, 'wr', {0xC05E: Ignore()}).action == Ignore())
ok &= check('hw: every C0xx rd/wr mapped', all(A.lookup(a, m) for a in range(0xC000, 0xC100) for m in ('rd', 'wr')))
ok &= check('hw: no overlapping entries', <no address/mode pair matched by two MAP entries>)
ok &= check('hw: ROM WAIT idiom', A.rom(0xFCA8).action == IdiomOnly(('rom_wait',)))
ok &= check('hw: ROM_KNOWN covered', all(A.rom(a) for a in (0xFCA8, 0xFC58, 0xFDED, 0xFDF0, 0xFD0C, 0xFD1B, 0xFB1E, 0xFBDD, 0xFF3A, 0xFE89, 0xFE93, 0xFB2F, 0xF3E2, 0xF3D8, 0xC600, 0xC65C)))
```
- [ ] **Step 2: Run** `python3 -m tests.hw_checks` → fails with `ModuleNotFoundError: kit.hw`.
- [ ] **Step 3: Implement** `kit/hw/__init__.py` and `kit/hw/apple2.py` as specified. `lookup` scans `MAP` in order, so the earlier, more specific entries win; the overlap check guarantees there are none to choose between.
- [ ] **Step 4: Run** `python3 -m tests.hw_checks` → all `ok`; `python3 -m tests.selftest` → `ALL OK`.
- [ ] **Step 5: Commit** `feat(hw): Apple II hardware map`.

### Task 2: Survey reads the map

**Files:**
- Modify: `kit/survey.py:22-74` (remove `SOFTSW`, `ROM_KNOWN`; `softsw()` and the ROM lookups read `kit.hw.apple2`), `kit/survey.py:178-300` (`report`, `draft_spec`)
- Test: `tests/hw_checks.py` (group `survey`)

**Interfaces:**
- Consumes: `apple2.lookup`, `apple2.rom`, the action classes.
- Produces: `survey.softsw(a: int, mode: str = 'abs') -> tuple[str, str, str] | None` with the same shape as today, `(name, category, suggestion)`. `suggestion` is derived from the action: `Runtime`/`Latch` → `'runtime'`, `Ignore` → `'ignored'`, `Trap` → `'TRAP: <reason>'`, `IdiomOnly` → `'idiom: <kinds> (proposed in section 7)'`. Mode mapping: `absx` → `rdx`, `abs` → `rd`.
  The draft spec's `NATIVE` lines now come only from `Trap` and `IdiomOnly` sites; `Runtime`/`Latch`/`Ignore` sites no longer produce native candidates.

- [ ] **Step 1: Write failing checks:**
```python
from kit import survey
ok &= check('survey: speaker via map', survey.softsw(0xC030)[2] == 'runtime')
ok &= check('survey: Disk II slot', survey.softsw(0xC08C, 'absx')[2].startswith('TRAP'))
ok &= check('survey: paddle idiom', survey.softsw(0xC064)[2].startswith('idiom'))
ok &= check('survey: no SOFTSW table left', not hasattr(survey, 'SOFTSW') and not hasattr(survey, 'ROM_KNOWN'))
```
- [ ] **Step 2: Run** `python3 -m tests.hw_checks survey` → FAIL.
- [ ] **Step 3: Implement** the changes above. Section 5 (ROM calls) prints `apple2.rom(a)`, or `unknown: look it up (a2-help skill)`.
- [ ] **Step 4: Run** the group → pass; `python3 -m tests.selftest` → `ALL OK`. If the reference port's coverage exists: `python3 -m kit.survey ports/<ref> > /tmp/s.txt` runs without error.
- [ ] **Step 5: Commit** `refactor(survey): classify hardware through kit.hw`.

### Task 3: No silent defaults

**Files:**
- Create: `kit/hw/iocheck.py`
- Modify: `kit/gen.py:19-37` (IO_MEM default from the map; `HW_OVERRIDES`), `kit/gen.py:161` (`build`: run the check after writing sources), `kit/build512.py:106-120` and `kit/build128.py:110-124` (`io_defaults` → `iocheck.stubs`), `docs/SPEC.md` (generator hooks table: `HW_OVERRIDES`)
- Test: `tests/hw_checks.py` (group `iocheck`)

**Interfaces:**
- Produces, in `kit/hw/iocheck.py`:
  - `used_symbols(lines_sets) -> set[str]`: the regex `io_defaults` uses today.
  - `check(used: set[str], overrides: dict | None = None, io_mem: dict | None = None) -> tuple[list[str], set[str]]`: returns `(errors, conds)`.
    - An error is `"$C05E wr: double hi-res (AN3) not supported (spec HW_OVERRIDES to allow)"` for `Trap`, `"$C064 rd: single access to a loop-only switch; see idioms paddle_count, rom_pread"` for `IdiomOnly`, and `"$Cxxx: not in the hardware map"` for unmapped addresses.
    - `conds` is the set of `Runtime.cond` values the used symbols need.
    - A symbol for an address in `io_mem` is skipped: those are latch loads and never emitted, as today.
  - `stubs(used: set[str], defined: set[str], overrides=None) -> list[str]`: stub lines only for `Ignore` entries not in `defined`. A read stub sets `IOV` to `value` (`clr IOV` when 0, so the reference port's bytes stay the same). Raises `SystemExit` with the `check` errors for anything else undefined.
- `gen.IO_MEM` default = `{a: action.sym for each Latch rd entry}` (same as today: `$C000`/`$C061`/`$C062`). A spec's `IO_MEM` still replaces it entirely (Review Focus 4).
- Spec attribute `HW_OVERRIDES: dict[int, action]`, read by `gen.use` and by both builds.

- [ ] **Step 1: Write failing checks:**
```python
from kit.hw import iocheck, Ignore
e, c = iocheck.check({'io_wr_C05E'})
ok &= check('iocheck: trap is an error', len(e) == 1 and 'double hi-res' in e[0])
e, c = iocheck.check({'io_wr_C05E'}, overrides={0xC05E: Ignore()})
ok &= check('iocheck: override clears it', e == [])
e, c = iocheck.check({'io_rd_C064'})
ok &= check('iocheck: idiom-only single access', 'paddle_count' in e[0])
e, c = iocheck.check({'io_wr_C050', 'io_rd_C030'})
ok &= check('iocheck: cond collected', e == [] and c == {'HW_IOV'})
ok &= check('iocheck: ignore stub', iocheck.stubs({'io_rd_C020'}, set()) == ['io_rd_C020\tpshs\tcc', '\tclr\tIOV', '\tpuls\tcc,pc'])
e, c = iocheck.check({'io_rd_C000'}, io_mem={0xC000: 'my_latch'})
ok &= check('iocheck: spec IO_MEM wins', e == [])                     # Review Focus 4
```
- [ ] **Step 2: Run** `python3 -m tests.hw_checks iocheck` → FAIL.
- [ ] **Step 3: Implement** `iocheck.py`, and wire it into `gen.build`: errors → `SystemExit` with every error line, after all files are written so the user can inspect them. Replace both builds' `io_defaults` with `iocheck.stubs`. Add a `HW_OVERRIDES` row to `docs/SPEC.md`.
- [ ] **Step 4: Run** the group → pass; `python3 -m tests.selftest` → `ALL OK`; reference port identity (both disks `cmp` equal).
- [ ] **Step 5: Commit** `feat(hw): no silent I/O defaults; HW_OVERRIDES`.

### Task 4: Video-mode and status handlers; generated docs tables

**Files:**
- Create: `rt/hw/apple2/io_video.asm`
- Modify: `rt/t512/rt.asm`, `rt/t128/rt.asm` (add `ifdef HW_IOV` / `include` / `endc` after the existing I/O handlers); `kit/build512.py`, `kit/build128.py` (emit `HW_IOV equ 1` in the main source when `iocheck.check` returns it in `conds`); `kit/hw/apple2.py` (add `render_doc() -> str` and `__main__`: `python3 -m kit.hw.apple2 --doc`); `docs/NATIVES.md` §1 and `docs/machines/apple2.md` softswitch section (replace the tables with the generated block between `<!-- hw-map:begin -->` and `<!-- hw-map:end -->`)
- Test: `tests/hw_checks.py` (groups `iov`, `docs`)

**Interfaces:**
- `io_video.asm` defines:
  - the latch `v_mode`, with bits `VM_TEXT=$01`, `VM_MIXED=$02`, `VM_LORES=$04`; 0 = full-screen hi-res;
  - the handlers `io_rd_/io_wr_` for `C050` (clear TEXT), `C051` (set TEXT), `C052` (clear MIXED), `C053` (set MIXED), `C056` (set LORES), `C057` (clear LORES): each preserves registers and CC and clears `IOV`;
  - `io_rd_C01A` (bit 7 = TEXT), `C01B` (MIXED), `C01C` (bit 7 = `v_dpage`), `C01D` (bit 7 = HIRES = not LORES), all in `IOV`;
  - `io_rd_C019`: IIe polarity, bit 7 = 0 **inside** vertical blank. "Inside" is the same test `rt_vbl` makes (`$FF92 & $20` folded into `vbl_over`; `rt/t512/rt.asm:548-552`).
  - It uses `v_dpage`, `vbl_over` and `IOV` from the including runtime.
- `render_doc() -> str`: a markdown table with columns address, name, access, action, notes, plus a ROM table.

- [ ] **Step 1: Write failing checks.** Group `iov` assembles `io_video.asm` standalone at `$C000`, with test equates for `IOV`, `v_dpage`, `vbl_over` and a fake `$FF92` byte (cc3sim I/O page), and runs these sequences in `cc3sim` (`-pc` a driver, `-d` to dump `IOV`/`v_mode`):
```python
ok &= check('iov: TXTSET then RDTEXT', run_seq(['io_wr_C051', 'io_rd_C01A']) == 0x80)
ok &= check('iov: TXTCLR then RDTEXT', run_seq(['io_wr_C051', 'io_wr_C050', 'io_rd_C01A']) == 0x00)
ok &= check('iov: LORES then RDHIRES', run_seq(['io_wr_C056', 'io_rd_C01D']) == 0x00)
ok &= check('iov: RDPAGE2 follows v_dpage', run_seq(['io_rd_C01C'], v_dpage=1) == 0x80)
ok &= check('iov: RDVBL inside blank', run_seq(['io_rd_C019'], ff92=0x20, vbl_over=0) & 0x80 == 0)
ok &= check('iov: handlers preserve A,B,CC', <A, B, CC before == after for every handler>)
```
  Group `docs`:
```python
ok &= check('docs: NATIVES.md table current', A.render_doc() in block('docs/NATIVES.md'))
ok &= check('docs: apple2.md table current', A.render_doc() in block('docs/machines/apple2.md'))
ok &= check('hw: Runtime labels exist', <every Runtime entry without cond has io_<mode>_<addr> in BOTH rt/t512/rt.asm and rt/t128/rt.asm; every cond='HW_IOV' entry is defined in rt/hw/apple2/io_video.asm>)
```
- [ ] **Step 2: Run** `python3 -m tests.hw_checks iov docs` → FAIL.
- [ ] **Step 3: Implement** `io_video.asm`, the `ifdef HW_IOV` includes, the build equate, `render_doc`, and regenerate both doc blocks with `python3 -m kit.hw.apple2 --doc`. Keep the prose around the tables, including the "other `$C0xx`" row in `NATIVES.md`, which now says "see the map; unmapped = generation error".
- [ ] **Step 4: Run** the groups → pass; `python3 -m tests.selftest` → `ALL OK`; reference port identity (the reference port never sets `HW_IOV`).
- [ ] **Step 5: Commit** `feat(rt): video-mode/status handlers behind HW_IOV; generated map docs`.

## Phase 2: library routines and accepted idioms

### Task 5: Idiom spec form, expansion and instance source

**Files:**
- Create: `kit/hw/idiom.py`, `kit/hw/lib.py`, `rt/hw/apple2/vbl_poll.asm`, `rt/hw/apple2/rom_wait.asm`
- Modify: `kit/gen.py` (`use`: call `expand(spec)`; `build`: write `hw_inst.asm`), `kit/build512.py`, `kit/build128.py` (append `build/src/hw_inst.asm` to the native sources when non-empty), `docs/SPEC.md` (`HW_IDIOMS` row), `kit/newport.py` (scaffolded `spec.py` gets `HW_IDIOMS = []` and `HW_OVERRIDES = {}` with one comment line each)
- Test: `tests/hw_checks.py` (group `idiom`)

**Interfaces:**
- `kit/hw/idiom.py`:
  - `@dataclass(frozen=True) class Idiom: kind: str; entry: int; end: int; exits: tuple[int, ...] = (); params: dict = field(default_factory=dict); image: str | None = None`. It is exported as `kit.hw.Idiom` for specs. (The map's loop-only action is `IdiomOnly`, from Task 1: a different class.)
  - `symbol(i: Idiom) -> str`: `f'hw_{i.kind}_{i.entry:04x}'`.
  - `expand(spec) -> None`: idempotent (sets `spec._hw_expanded`). For each idiom it adds `NATIVE[entry] = symbol`, `NATIVE_RANGES += (entry, end)` and `NATIVE_EXITS += exits`. It raises `SystemExit` naming both entries if a range overlaps an existing `NATIVE_RANGES` entry, a `NATIVE` address or another idiom (Review Focus 3).
- Param source tuples (all kinds): `('a',)`, `('x',)`, `('y',)`, `('zp', addr)`, `('abs', addr)`, `('imm', value)`.
- `kit/hw/lib.py`:
  - `KINDS = {'vbl_poll', 'rom_wait', 'delay', 'tone', 'paddle_count', 'rom_pread', 'rom_bell'}`
  - `render(i: Idiom) -> list[str]`: loads `rt/hw/apple2/<kind>.asm`, substitutes `@SYM@` (the instance label), `@LOAD_<P>@` (a 6809 instruction that loads param P into A: `('x',)` → `tfr b,a`; `('y',)` → `lda RY`; `('zp', n)` → `lda <$nn`; `('abs', n)` → `lda >$nnnn`; `('imm', v)` → `lda #$vv`) and `@<P>@` (a numeric param). It also prefixes internal labels with the symbol so instances don't collide.
  - `render_all(idioms) -> list[str]`
- Templates in this task:
  - `vbl_poll`: `jsr rt_vbl`, then the exit flags the param `exit_n` (0/1) states;
  - `rom_wait`: today's `rt/natives/apple2/wait.asm` formula, A in, A = 0 and Z = 1 out.

- [ ] **Step 1: Write failing checks:**
```python
from kit.hw import Idiom
from kit.hw.idiom import expand, symbol
from kit.hw.lib import render
S = types.SimpleNamespace(NATIVE={}, NATIVE_RANGES=[], NATIVE_EXITS=[], HW_IDIOMS=[Idiom('vbl_poll', 0x0900, 0x0905, (0x0906,))])
expand(S); expand(S)
ok &= check('idiom: expand', S.NATIVE == {0x0900: 'hw_vbl_poll_0900'} and S.NATIVE_RANGES == [(0x0900, 0x0905)] and S.NATIVE_EXITS == [0x0906])
S2 = types.SimpleNamespace(NATIVE={0x0902: 'x'}, NATIVE_RANGES=[(0x0902, 0x0910)], NATIVE_EXITS=[], HW_IDIOMS=[Idiom('vbl_poll', 0x0900, 0x0905)])
ok &= check('idiom: overlap refused', raises(SystemExit, expand, S2))       # Review Focus 3
ok &= check('idiom: two instances assemble', assembles(render(Idiom('rom_wait', 0x800, 0x80F)) + render(Idiom('rom_wait', 0x900, 0x90F)) + ['RY equ $F0', 'rt_vbl rts']))
ok &= check('idiom: rom_wait vs 6502 WAIT', difftest_kind('rom_wait', WAIT_6502_BYTES, n=200))
ok &= check('idiom: vbl_poll vs 6502 loop', difftest_kind('vbl_poll', bytes([0xAD,0x19,0xC0, 0x10,0xFB, 0x60]), n=50))
```
  `difftest_kind(kind, code_6502, n)` is a helper in `tests/hw_checks.py`. It places the 6502 bytes at `$0800`, builds a `kit.difftest.Harness` from `render(Idiom(kind, 0x800, ...))` with stubs for `rt_vbl`, and compares registers/flags (live mask from `xc65.routine_contract`) and RAM against `difftest.reference` on random inputs. `WAIT_6502_BYTES` is the monitor `WAIT` routine written out from its documented source (`SEC / PHA / SBC #1 / BNE / PLA / SBC #1 / BNE / RTS`); that is code, not ROM bytes.
- [ ] **Step 2: Run** `python3 -m tests.hw_checks idiom` → FAIL.
- [ ] **Step 3: Implement** as specified. `gen.build` writes `hw_inst.asm` (empty when there are no idioms), and the builds append it only when it's non-empty, which keeps the reference port identical.
- [ ] **Step 4: Run** the group → pass; `python3 -m tests.selftest` → `ALL OK`; reference port identity.
- [ ] **Step 5: Commit** `feat(hw): HW_IDIOMS spec form, expansion, vbl_poll and rom_wait routines`.

### Task 6: `delay` routine

**Files:**
- Create: `rt/hw/apple2/delay.asm`
- Test: `tests/hw_checks.py` (group `lib_delay`)

**Interfaces:**
- Params:
  - `outer`, `inner`: param sources for the two counters (`inner` may be `('imm', v)` or absent for a single loop);
  - `pass_cycles`: Apple cycles per inner pass, as an int;
  - `exit_regs`: the dict of final register values the 6502 leaves, for example `{'x': 0, 'y': 0}`. The routine sets them and Z = 1.
- Timing: total CoCo cycles = `round(apple_cycles × APPLE_TO_COCO)` ± `TIMING_TOL`. The template computes the counts at run time from the loaded values, with counter value 0 meaning 256.

- [ ] **Step 1: Write failing checks:**
```python
code = bytes([0xA2,0x00, 0xA0,0x00, 0x88, 0xD0,0xFD, 0xCA, 0xD0,0xF8, 0x60])   # LDX #0 / L: LDY #0 / M: DEY / BNE M / DEX / BNE L / RTS
ok &= check('delay: regs/flags/RAM', difftest_kind('delay', code, n=20, params=...))
ok &= check('delay: X=0 runs 256 passes (timing)', abs(coco_cycles / (apple_cycles * 1.75) - 1) <= 0.02)   # Review Focus 1
ok &= check('delay: X=1,Y=1 shortest case timing', <same ratio check>)
```
  `coco_cycles` comes from the `Harness.run` result (`'cycles'`), and `apple_cycles` from `difftest.reference`'s `'cycles'`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** the template. **Step 4: Run** → pass, plus `tests.selftest`.
- [ ] **Step 5: Commit** `feat(hw): delay routine in Apple time`.

### Task 7: `tone`, `paddle_count`, `rom_pread`, `rom_bell` routines

**Files:**
- Create: `rt/hw/apple2/tone.asm`, `paddle_count.asm`, `rom_pread.asm`, `rom_bell.asm`
- Modify: `kit/hw/lib.py` (add `paddle_model`); `rt/natives/apple2/README.md` (point `speaker.asm`, `paddle.asm`, `vbl.asm` and `wait.asm` users to the library kinds)
- Test: `tests/hw_checks.py` (group `lib_io`)

**Interfaces:**
- `tone` params:
  - `period`, `duration`: param sources (0 = 256);
  - `pass_cycles`: Apple cycles per inner delay pass;
  - `click_cycles`: Apple cycles per outer pass outside the delay;
  - `exit_regs`.
  The click is `lda $FF20 / eora #$FC / sta $FF20`, the same as `io_wr_C030`.
- `paddle_count` params: `paddle` (0-3; 0/2 → `joy_x`, 1/3 → `joy_y`), `counter` (the register the count ends in, `'x'` or `'y'`), `pass_cycles`, `max`, `exit_regs`.
- `kit/hw/lib.py`: `paddle_model(joy: int, pass_cycles: int, max_count: int) -> int` = `min(max_count, (joy * 255 // 63) * 11 // pass_cycles)`. This models the Apple timer: about 11 Apple cycles per PREAD unit, 0-255.
- `rom_pread`: X = paddle in, Y = `min(255, joy * 255 // 63)` out, as PREAD returns.
- `rom_bell`: a 1 kHz square wave for 0.1 s in Apple time (100 periods of 1023 Apple cycles), with A/X/Y as `BELL` leaves them per the param `exit_regs`.

- [ ] **Step 1: Write failing checks:**
```python
ok &= check('tone: regs/flags/RAM', difftest_kind('tone', TONE_6502, n=100, params=...))
ok &= check('tone: period 0 = 256 (no crash, regs right)', difftest_kind('tone', TONE_6502, cases=[dict(period=0, duration=1)]))   # Review Focus 1
ok &= check('paddle_model: extremes', paddle_model(0, 12, 255) == 0 and paddle_model(63, 11, 255) == 255 and paddle_model(63, 5, 200) == 200)
ok &= check('paddle_count: count = model', all(run_paddle(joy) == paddle_model(joy, 12, 255) for joy in (0, 1, 31, 32, 63)))
ok &= check('rom_pread: Y from joy', all(run_pread(0, j) == min(255, j * 255 // 63) for j in (0, 32, 63)))
ok &= check('rom_bell: assembles, 100 clicks pairs', <cc3sim run: 200 writes to $FF20 — counted after Task 10 adds -w; until then only assembles>)
```
  `TONE_6502` is a synthetic single-voice loop: `L: LDA $C030 / LDY period / M: DEY / BNE M / DEX / BNE L / RTS`. `run_paddle` and `run_pread` run the instance in `cc3sim` with `joy_x`/`joy_y` bytes set by the driver.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass, plus `tests.selftest`.
- [ ] **Step 5: Commit** `feat(hw): tone, paddle and ROM sound/paddle routines`.

## Phase 3: matchers

### Task 8: Matcher framework; `vbl_poll`, `delay`, `rom_*` matchers

**Files:**
- Create: `kit/hw/match.py`
- Test: `tests/hw_checks.py` (group `match`)

**Interfaces:**
- `@dataclass class Match: idiom: Idiom; evidence: list[str]` (notes; Task 10 appends test results).
- `@dataclass class NearMiss: kind: str; lo: int; hi: int; reason: str`.
- `loops(code) -> list[tuple[int, int, int]]`: `(head, tail, exit)` for every back-edge in `code.insns` (a branch or `JMP` whose target ≤ its own address and that is in the same straight run). `code` is a `kit.discover.Code`; each `Insn` has `.addr .mn .mode .len .arg`.
- `find(mem: bytes, code, executed: set[int] | None = None) -> tuple[list[Match], list[NearMiss]]`: runs every matcher. Only loops with at least one executed instruction count when `executed` is given.
- Matchers, `def m_<kind>(mem, code, loop_or_call) -> Match | NearMiss | None`:
  - `m_vbl_poll`: the loop body is only `LDA`/`BIT $C019` + `BPL`/`BMI` back. It optionally includes the paired loop for the opposite edge that immediately follows: the range then covers both.
  - `m_delay`: counted loops with no memory writes and no I/O, nested at most 2 deep, total ≥ `DELAY_MIN` Apple cycles for the largest counter value. Counter decrement by `DEX`/`DEY`/`SBC #1` (with `SEC`). Writes to RAM → `NearMiss('delay', …, 'loop body writes memory')`.
  - `m_rom`: a `JSR` to a `ROM` entry whose action is `IdiomOnly` → a `Match` whose range is the single `JSR`, the instance replacing the ROM entry itself. `entry` = the ROM address, so `NATIVE[$FCA8]` works like the reference port's `nt_wait`.
- `exit_regs` comes from a tiny symbolic pass over the loop: a counter decremented to the `BNE` fall-through is 0; a register loaded with an immediate and not changed after keeps it; any other register the routine changes is "unknown", and then the matcher returns a `NearMiss('<kind>', …, 'exit value of <reg> not determined')`.

- [ ] **Step 1: Write failing checks** (synthetic code at `$0800`, `discover` over it):
```python
ok &= check('match: vbl poll', kinds(find_in(bytes([0xAD,0x19,0xC0, 0x10,0xFB, 0x60]))) == ['vbl_poll'])
ok &= check('match: vbl poll BIT/BMI', kinds(find_in(bytes([0x2C,0x19,0xC0, 0x30,0xFB, 0x60]))) == ['vbl_poll'])
ok &= check('match: nested delay', kinds(find_in(DELAY_CODE)) == ['delay'] and find_in(DELAY_CODE)[0][0].idiom.params['exit_regs'] == {'x': 0, 'y': 0})
ok &= check('match: delay writing RAM is a near-miss', near(find_in(bytes([0xA2,0x00, 0x9D,0x00,0x30, 0xCA, 0xD0,0xFA, 0x60])), 'writes memory'))
ok &= check('match: JSR WAIT', kinds(find_in(bytes([0xA9,0x40, 0x20,0xA8,0xFC, 0x60]))) == ['rom_wait'])
ok &= check('match: short loop is not a delay', kinds(find_in(bytes([0xA2,0x03, 0xCA, 0xD0,0xFD, 0x60]))) == [])
```
- [ ] **Step 2: Run** `python3 -m tests.hw_checks match` → FAIL. **Step 3: Implement.** **Step 4: Run** → pass, plus `tests.selftest`.
- [ ] **Step 5: Commit** `feat(hw): idiom matcher framework (vbl, delay, ROM)`.

### Task 9: `tone` and `paddle_count` matchers; survey proposals

**Files:**
- Modify: `kit/hw/match.py` (`m_tone`, `m_paddle_count`), `kit/survey.py` (new section `## 7. Hardware idioms: proposed / near-miss`; `draft_spec` adds `HW_IDIOMS = [...]` with commented-out `Idiom(...)` lines and their evidence, and leaves those routines out of the draft `NATIVE`)
- Test: `tests/hw_checks.py` (group `match_io`, `survey_idioms`)

**Interfaces:**
- `m_tone`: an outer loop with exactly one `$C030` access (`LDA`, `BIT` or `STA`: Review Focus 2) and one inner counted delay, plus an outer counter. It extracts `period`, `duration`, `pass_cycles`, `click_cycles` and `exit_regs`. Two independent counters that each lead to a `$C030` access → `NearMiss('tone', …, 'two counters: multi-voice player, hand native')`. A keyboard read inside the loop → `NearMiss('tone', …, 'polls the keyboard inside the note: hand native (see speaker.asm)')`.
- `m_paddle_count`: a `$C070` access, then a loop reading `$C064+n` (`abs` or `abs,X`) with `BPL`/`BMI` out and a counter `INX`/`INY` with an optional `CPX`/`CPY #max` → `paddle`, `counter`, `pass_cycles`, `max`, `exit_regs`. An `abs,X` paddle index that isn't loaded with an immediate gives `paddle=None` → `NearMiss(… 'paddle number not constant')`.
- The survey calls `match.find` per image, with `executed` from the coverage profile. In the draft spec, each proposal is written commented out, so the user must uncomment it:
  `#   Idiom('tone', entry=0x0D00, end=0x0D3A, exits=(0x0D3B,), params={...}),   # evidence: ...; check: python3 -m kit.listing PORT 0D00-0D3A`

- [ ] **Step 1: Write failing checks:**
```python
for op in (0xAD, 0x2C, 0x8D):                 # LDA / BIT / STA $C030  (Review Focus 2)
    ok &= check(f'match: tone via {op:02X}', kinds(find_in(tone_code(op))) == ['tone'])
ok &= check('match: two-voice near-miss', near(find_in(TWO_VOICE), 'multi-voice'))
ok &= check('match: paddle loop', find_in(PADDLE_CODE)[0][0].idiom.params['paddle'] == 0)
ok &= check('match: paddle,X unknown is a near-miss', near(find_in(PADDLE_X_CODE), 'not constant'))
ok &= check('survey: draft lists idiom commented out', '#   Idiom(\'tone\'' in survey.draft_spec(cov, s_with_tone))
ok &= check('survey: idiom routine not in draft NATIVE', 'n_sound_0800' not in survey.draft_spec(cov, s_with_tone))
```
  `cov` and `s_with_tone` are a minimal coverage dict and survey result built in the test around `tone_code(0xAD)`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass, plus `tests.selftest`; if the reference port's coverage exists, `python3 -m kit.survey ports/<ref>` runs.
- [ ] **Step 5: Commit** `feat(hw): tone and paddle matchers; survey proposes idioms`.

## Phase 4: evidence

### Task 10: `cc3sim` write log and `--verify`

**Files:**
- Modify: `sim/cc3sim.c` (option `-w ADDR`, up to 8 addresses: print `w ADDR v=VV cycles=N` per write to that CPU address, at most 20000 lines; usage comment updated), `kit/difftest.py` (`Harness.run(..., watch=())` returns `'writes': [(addr, value, cycles)]`; `RefBus` takes an optional `io=None` object with `read(addr, cycles) -> int` and `write(addr, value, cycles)`, used for `$C000-$C0FF` instead of returning 0; `reference(..., io=None)` passes it through)
- Create: `kit/hw/verify.py`
- Modify: `kit/survey.py` (`--verify [N]`, default 300: appends the evidence to each proposal comment)
- Test: `tests/hw_checks.py` (group `verify`)

**Interfaces:**
- `@dataclass class Evidence: cases: int; passed: int; max_timing_err: float | None; notes: list[str]`, with `__str__` → `'verified 300/300, period error 0.6% max'`.
- `verify(m: Match, mem: bytes, n: int = 300, seed: int = 1) -> Evidence`:
  - Every kind: registers/flags/RAM, as in `difftest_kind` (move that helper from the tests into `verify.py` and have the tests import it).
  - `tone`: Apple click times are the `io.write`/`io.read` stamps at `$C030` (host); CoCo click times are the cc3sim writes to `$FF20`. Compare successive intervals: `max |coco/(apple × 1.75) − 1|` ≤ `TIMING_TOL`.
  - `delay`, `rom_wait`: total cycles, same ratio.
  - `paddle_count`: an `io` model on the Apple side. A `$C070` access records `t0`; `$C064+n` reads give bit 7 = 1 while `cycles − t0 < (joy*255//63)*11`. The CoCo side gets the same `joy` in `joy_x`/`joy_y`. Compare the counts.
  - `vbl_poll`: an `io` model returns `$C019` bit 7 = 1 for k reads, then 0; the CoCo side's `rt_vbl` stub counts calls. It passes when the exit state matches and `rt_vbl` was called once.
- Cases where the reference stops as `wild`/`illegal`/`timeout` are excluded and counted in `notes`, as `test_engine` does.

- [ ] **Step 1: Write failing checks:**
```python
ok &= check('cc3sim -w logs writes', <assemble 'lda #1 / sta $FF20 / sta $FF20 / halt'; run with -w FF20 → two lines, cycle stamps increasing>)
ev = verify(match_of(tone_code(0xAD)), mem_with(tone_code(0xAD)), n=50)
ok &= check('verify: tone passes with timing', ev.passed == ev.cases and ev.max_timing_err <= 0.02)
bad = broken_tone_instance()                   # same Match, template patched to add 20 cycles per pass
ok &= check('verify: wrong timing is caught', verify(bad, mem_with(tone_code(0xAD)), n=20).max_timing_err > 0.02)
ok &= check('verify: paddle passes', verify(match_of(PADDLE_CODE), mem_with(PADDLE_CODE), n=50).passed > 0)
ok &= check('rom_bell: 200 DAC writes', <cc3sim -w FF20 count == 200>)     # completes Task 7's pending check
```
- [ ] **Step 2: Run** `make -C sim && python3 -m tests.hw_checks verify` → FAIL.
- [ ] **Step 3: Implement.** `broken_tone_instance` lets `verify` take an optional `render_override: list[str]` (test hook only).
- [ ] **Step 4: Run** → pass; `python3 -m tests.selftest` → `ALL OK` (existing difftests unchanged: `io=None` keeps reads at 0); reference port identity.
- [ ] **Step 5: Commit** `feat(hw): timing evidence for idiom proposals (cc3sim -w, survey --verify)`.

## Phase 5: guidance

### Task 11: "Least likely to transcode" ranking: skill, docs, survey

**Files:**
- Modify: `SKILL.md` (replace the "What to transcode vs replace" summary with the ranked list from the design §5, keeping the H/T/V/P/R/S/L table below it; add the hardware map, `HW_OVERRIDES`, `HW_IDIOMS` and `survey --verify` to the workflow steps 4-7), `docs/DECIDE.md` (new first section "Least likely to transcode", the same seven buckets with one paragraph each; rule H and rule T point to the map and the idiom kinds), `docs/NATIVES.md` (§3 catalogue: rows that a library kind now covers say so), `README.md` (one line in the path table for `kit/hw` and `rt/hw`), `CLAUDE.md` (architecture: the map as the single source; `tests.hw_checks`), `kit/survey.py` (final section `## 8. Least likely to transcode (this game)`)
- Test: `tests/hw_checks.py` (group `guide`)

**Interfaces:**
- `survey.rank(s: dict, matches: list[Match], near: list[NearMiss], cov: dict) -> list[tuple[int, int | None, str]]`: `(bucket 1-7, routine, reason)`, sorted by bucket, then routine. Bucket sources:
  1. `cov['loaders']`;
  2. tone near-misses (multi-voice / keyboard);
  3. sites whose map action is `Trap`;
  4. routines containing rejected opcodes (`BVC BVS CLV PHP PLP RTI BRK SED TSX TXS`; `SED` covers decimal mode) or opcode SMC;
  5. the top hi-res routines (>2% of traffic, as in the draft spec);
  6. matches;
  7. not listed: it is "everything else".
  Section 8 prints buckets 1-6 with their names from the design.

- [ ] **Step 1: Write failing checks:**
```python
r = survey.rank(s_synth, [match_of(tone_code(0xAD))], [NearMiss('tone', 0x0D40, 0x0D80, 'two counters: multi-voice player, hand native')], {'loaders': ['$B700']})
ok &= check('rank: buckets ordered', [b for b, _, _ in r] == sorted(b for b, _, _ in r))
ok &= check('rank: multi-voice is bucket 2', any(b == 2 and a == 0x0D40 for b, a, _ in r))
ok &= check('rank: match is bucket 6', any(b == 6 for b, _, _ in r))
ok &= check('guide: SKILL.md has ranking', 'Least likely to transcode' in open('SKILL.md').read())
ok &= check('guide: DECIDE.md has ranking', 'Least likely to transcode' in open('docs/DECIDE.md').read())
```
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** the docs and `rank`. **Step 4: Run** → pass, plus `tests.selftest`.
- [ ] **Step 5: Commit** `docs(skill): rank what is least likely to transcode; survey section 8`.

### Task 12: The reference port acceptance (needs the user's disk)

**Files:** none changed unless a check fails.

- [ ] **Step 1: Identity.** Rebuild both reference port disks and `cmp` them against `build/<ref>/baseline/` → identical.
- [ ] **Step 2: Survey.** Run `python3 -m kit.survey ports/<ref> --verify 300 > build/<ref>/survey_hw.txt`. Expected in section 7:
  - a `rom_wait` proposal for `$FCA8` (the reference port's `nt_wait`);
  - `vbl_poll` proposals at the reference port's VBL-wait natives;
  - a `paddle_count` proposal or a reasoned near-miss at the paddle loop with the `$6D2F` exit;
  - the two-voice player in `$0D00-$0DFF` as a `multi-voice` near-miss.
  Every proposal's evidence reads `verified N/N`.
- [ ] **Step 3: Report.** For each expected item that's missing or wrong, report it with its section-7 line, and fix the matcher in a follow-up task. Don't loosen the matcher just to make it match.
- [ ] **Step 4: Selftest.** `python3 -m tests.selftest` → `ALL OK`.
- [ ] **Step 5: Commit** any fixes as `fix(hw): …` (no game output is committed).
