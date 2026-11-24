# Hardware transcoder (Apple II → CoCo 3): design

Date: 2026-11-24. Status: draft for approval.

## Intent
Today the kit maps Apple II hardware piecemeal:
- **Single accesses.** `xc65` turns a `$C0xx` access into `io_rd_/io_wr_/io_rdx_Cxxx` calls (`kit/gen.py`). Some reads become latch loads (`IO_MEM`).
- **Runtime handlers.** The runtime defines a handful of them (`rt/t512|t128/rt.asm`).
- **Silent defaults.** Every other address gets a stub from `build*.py:io_defaults`: reads give 0, writes do nothing. A game that touches an unsupported switch builds cleanly and misbehaves.
- **Whole loops.** Hardware loops (tone, paddle count, VBL poll, delays, ROM calls) are hand-written natives, started from templates (`rt/natives/apple2/`).
- **Inconsistent tables.** The survey's `SOFTSW`/`ROM_KNOWN` tables suggest native names that don't exist (`kbd.asm`, `nt_vbl`, `nj_paddle`).

This project adds a **hardware transcoder**, made of four parts:
1. One **hardware map** that every part of the kit reads.
2. A **library of CoCo routines** for common hardware idioms, with parameters.
3. **Idiom matchers** that find those idioms in a game and *propose* them in the draft spec, with test evidence.
4. A clear, ranked statement in the skill of **what is least likely to transcode**.

The user's reason for wanting this was not stated. The working assumption is that each new port should need fewer hand-written natives, and that no hardware access should be handled wrongly without anyone noticing.

## Goals
- One source of truth for Apple II hardware: `kit/hw/apple2.py`. It feeds `gen.py` (I/O table, latches), `survey.py` (classification, ROM entry points), the builds' I/O defaults and the docs tables.
- No silent defaults. Every hardware address the generated code touches has an explicit action in the map or in a spec override. Anything else is an error at generation time.
- Library routines for the standard idioms (below), assembled from parameters, for both 512K and 128K targets.
- Matchers (Python, over the decoded, executed code) that recognise those idioms, extract their parameters and exit state, and write them into `draft_spec.py` as proposals. They also report near-misses with the reason they did not match.
- Evidence with every proposal: an automatic differential test, library routine vs the 6502 loop, on random inputs from snapshot state. It compares registers, flags and RAM (per contract) and, for timed idioms, the timing of hardware events.
- `SKILL.md`, `docs/DECIDE.md` and the survey report get a ranked **"least likely to transcode"** section.

## Non-goals
- **Auto-applying matches.** Nothing a matcher finds changes the build until the user moves it into `spec.py`. *(user decision)*
- **C64.** The map format is machine-neutral (`kit/hw/<machine>.py`), but only `apple2.py` is written.
- New display support: text, lo-res, mixed mode, double hi-res, 80-column. The map tracks the mode switches and reports unsupported modes; it does not convert them.
- Language-card RAM and IIe aux memory: mapped to an explicit trap, with a pointer to `docs/machines/apple2.md`.
- Multi-voice music players, sample players, blitters: still hand-written natives.

## Anti-goals (failure even if it "works")
- A proposal that passes random tests but is wrong in timing (pitch, paddle range). Timed idioms must carry timing evidence, not only RAM equality.
- Breaking the Karateka regression: both disks must stay byte-identical with the current spec.
- Matchers that propose many low-quality candidates. A near-miss is reported as a near-miss, never as a proposal.

## Approaches considered
1. **Code-shape matchers in Python** over the decoded instruction graph, producing parameterised library-routine instances. Handles register and branch-layout variation; adding an idiom means writing a short Python function. **Chosen by the user.**
2. Declarative signature patterns with wildcards. Easier to add, but brittle against real-game variants (X vs Y, `BIT` vs `STA`, reordered loops), so near-duplicate patterns multiply.
3. Behaviour fingerprinting on the host (classify loops by their hardware access trace). Independent of code layout, but heavy, and exact parameter and exit-state extraction is unreliable. Its idea is kept as **evidence** for approach 1: each proposal's difftest.

## Design

### 1. Hardware map: `kit/hw/apple2.py`
A list of entries `Hw(lo, hi, name, category, action, modes, doc)`:
- `modes`: which access forms an entry covers: `rd`, `wr`, `rdx`, `wrx` (`,X` indexed; `$C08x,X` = slot I/O).
- `action` is one of:
  - `runtime(label)`: a runtime handler (existing `io_*_Cxxx`, or new ones below).
  - `latch(sym)`: a side-effect-free read of a runtime latch (today's `IO_MEM`).
  - `ignore(value=0)`: an explicit, documented no-op; reads return `value`. This replaces today's silent default for addresses known to be harmless.
  - `trap(reason)`: unsupported. A translated access is a generation error naming the routine and the reason, unless the spec overrides it.
  - `idiom(kinds)`: meaningful only inside a loop (paddle timers, `$C019` polling, `$C030` tones). A single translated access is an error that points at the idiom kinds.
- ROM entry points (today's `ROM_KNOWN`) are entries of the same table with `action = idiom('rom_wait')`, `trap('text output')` and so on.
- **Spec override:** `HW_OVERRIDES = {0xC05A: ignore(), …}` in a port's `spec.py`.

Consumers:
- `gen.py` builds `IO`/`IO_MEM` from the map and the overrides.
- `survey.py` drops `SOFTSW`/`ROM_KNOWN` and classifies through the map.
- `build512/128.io_defaults` emits stubs only for `ignore` entries, and fails on anything else that is undefined.
- `python3 -m kit.hw.apple2 --doc` prints the markdown tables for `docs/NATIVES.md` and `docs/machines/apple2.md`. The self-test checks that the docs match.

New runtime support in phase 1, cheap and in both targets:
- `$C050-$C057`: record the text/mixed/lo-res/hi-res state in a `v_mode` latch, and keep page selection as today.
- `$C01A-$C01D` (RDTEXT/RDMIXED/RDPAGE2/RDHIRES): latches derived from `v_mode`/`v_dpage`.
- `$C019` single read: bit 7 from the same GIME vertical-blank window `rt_vbl` uses.
- `$C020` and the annunciators `$C058-$C05D`: `ignore`. `$C05E`/`$C05F` (double hi-res): `trap`.
- `$C080-$C08F` (language card), `$C011-$C018` (LC/aux status), `$C090-$C0FF` (slots) and `$C100-$CFFF`: `trap`, each with its reason.

**Karateka regression:** every address Karateka's generated code reaches through `io_defaults` today must map to `ignore` with the same value, so the output stays byte-identical. The builder finds this set from Karateka's `build/src` and checks it. If an address doesn't fit `ignore`, the Karateka spec gets an override.

### 2. Library routines: `rt/hw/apple2/*.asm`
These are lwasm macros (or `equ`-parameterised includes) that the generator instantiates into `build/src/hw_inst.asm`, one instance per accepted idiom. Each follows the native contract in `docs/NATIVES.md` and leaves the exit state the matcher extracted (for example Y = 0, Z = 1 after `DEY/BNE`).

| kind | 6502 idiom | CoCo routine |
|---|---|---|
| `vbl_poll` | `L: LDA/BIT $C019 / BPL\|BMI L`, optionally the paired wait-for-the-other-edge loop | `jsr rt_vbl`, then set the flags the exit reads |
| `delay` | nested counted loops with no hardware access (`DEX/BNE`, `DEY/BNE`, `SBC #1`) | the same count in Apple time × 1.75 CoCo cycles |
| `rom_wait` | `JSR $FCA8` | today's `wait.asm` formula |
| `tone` | single-voice loop: one `$C030` access per period, inner counted delay (period from reg/zp/imm), outer duration counter | click via the DAC, inner delay scaled to Apple time, the same counters |
| `paddle_count` | `$C070` trigger, then count while `$C064+n` bit 7 is set, with a max | count derived from `joy_x/joy_y` scaled to the loop's per-pass cycles and max |
| `rom_pread` | `JSR $FB1E` | same, with PREAD's scale (0-255) |
| `rom_bell` | `JSR $FF3A`/`$FBDD` | 1 kHz tone for 0.1 s |

The `rt/natives/apple2/` templates stay for hand-written natives. Where a library routine supersedes a template, its README points to the library.

**Spec form**, after the user accepts a proposal:
```python
HW_IDIOMS = [
    Idiom('tone', entry=0x0D00, end=0x0D3A, exits=[0x0D3B],
          params=dict(period=('zp', 0x06), duration=('x',), inner_cycles=5)),
]
```
`gen.py` expands each entry into `NATIVE`, `NATIVE_RANGES`, `NATIVE_EXITS` and the instance source. The contract comes from `xc65.routine_contract` as it does for hand natives.

### 3. Matchers: `kit/hw/idioms.py`
- **Input:** one image's memory plus coverage, decoded with `discover`.
- **Candidates:** executed loops (back-edges) that contain a map entry with `action = idiom(...)`, pure counted delay loops above a cycle threshold, and calls to ROM idiom entries.
- **Per kind:** a function that checks the shape, extracts the parameters (where each counter and the period live, the trigger, the max), the range, the exits and the exit-state facts.
- **Output:** `Match` (a proposal) or `NearMiss(kind, range, reason)`. Example near-miss: "loop at `$0D40` toggles `$C030` with two counters: multi-voice player, hand native".
- `survey.py` adds a report section "hardware idioms: proposed / near-miss" and writes each proposal into `draft_spec.py` as a commented `Idiom(...)` line, with its evidence and the disassembly range to check.

### 4. Evidence: auto-generated difftest
- **Command:** `python3 -m kit.survey … --verify` (needs `sim/cc3sim`). For each proposal it assembles the instance and runs both sides through `kit/difftest.py` on N random cases from snapshot state: cc3sim against the host `cpu6502` reference.
- **Compared:** the contract's registers and flags, plus RAM.
- **Timed kinds** (`tone`, `delay`, `rom_wait`): the cycle stamps of hardware events (`$C030` accesses on the host vs `$FF20` DAC writes in cc3sim), converted by 1.75, within a tolerance. For `delay`, total cycles. This needs a new `cc3sim` option that logs writes to given addresses with cycle stamps.
- **`paddle_count`:** the returned count vs a model of the Apple timer for the same stick value.
- The result goes into the proposal comment, for example `# verified 300/300, period error 0.6% max`.

### 5. Skill and docs: "least likely to transcode"
A ranked section in `SKILL.md` (summary) and `docs/DECIDE.md` (full), most likely to be replaced first:
1. **Loaders, RWTS, copy protection, relocators.** Never transcoded: snapshots plus `nt_load`.
2. **Cycle-timed sound beyond a single voice** (multi-voice players, sample playback). Always hand-written.
3. **Code the hardware map traps on:** language card, aux memory, double hi-res, text/lo-res/mixed display, slot I/O. Needs a kit extension first.
4. **Code the transcoder rejects:** decimal mode, `PHP/PLP`, `TSX/TXS`, `RTI`, `BRK`, the V flag, opcode SMC. Hand-written, or extend `xc65`.
5. **Hot hi-res code** (blitters, fills, scrolls). Transcodes correctly but is too slow; native for speed.
6. **Standard hardware idioms** (VBL polls, delays, single-voice tones, paddle counts, `WAIT`/`PREAD`/`BELL`). Library routines proposed by the matchers.
7. **Everything else** (game logic, AI, tables, menus, keyboard polling): transcodes.

The survey report ends with this list applied to the game: each routine in each bucket, with the reason.

### Testing
- **Self-test** (no game data):
  - the map is consistent: every `runtime`/`latch` label exists in both `rt/t512/rt.asm` and `rt/t128/rt.asm`;
  - the docs tables match `--doc`;
  - each matcher has synthetic positive and near-miss 6502 snippets;
  - each library routine difftests against a synthetic 6502 loop.
- **Karateka** (the user's disk):
  - both disks are byte-identical;
  - the survey proposes idioms at Karateka's existing `WAIT`, VBL and paddle natives, and reports its two-voice player as a near-miss.

## Phases
1. Hardware map and its consumers, no silent defaults, new latches/handlers, docs generation, self-test. Karateka byte-identical.
2. Library routines, the `HW_IDIOMS` spec form and its generator expansion.
3. Matchers and survey proposals or near-misses.
4. `cc3sim` event logging and `--verify` evidence.
5. Skill and docs ranking, and the survey's per-game ranked section.

## Calls made by the designer (check these)
- Unmapped or unsupported addresses become a **generation error** (with a spec override), not a runtime trap.
- Text/lo-res/mixed modes are tracked but not converted.
- The language card is a trap, not supported.
- Keyboard poll loops are not an idiom: they transcode correctly.
- Library routines are lwasm macros instantiated per accepted idiom.
- The timing tolerance and the cycle threshold for delay candidates are left to the builder; start at ±2% and 500 Apple cycles.

## Left to the builder
- Matcher internals and helper APIs.
- The macro parameter syntax.
- The exact wording of map reasons.
- How `draft_spec.py` lays out proposal comments.
