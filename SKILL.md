---
name: 6502to6809
description: Use when porting a 6502 game (Apple II today; C64 designed) to the TRS-80 Color Computer 3 by static 6502→6809 transcoding. The game's own code becomes native 6809, and hardware-bound hot spots become hand-written CoCo routines. Covers deciding what to transcode and what to replace, the optimized video, keyboard, joystick, sound, VBL and disk routines, 512K and 128K targets, differential tests and MAME probes.
---

# 6502 → 6809 static transcoding kit (CoCo 3)

The kit lives at `~/proyectos/6502to6809`; this skill is its `SKILL.md`. It turns a 6502 game into a native CoCo 3 program with two parts:

- **Transcoded code.** Every instruction of the game is translated ahead of time (`kit/xc65.py`), keeping the 6502's registers, flags and stack. Liveness analysis drops flag work nobody reads.
- **Natives.** Code that touches hardware, is cycle-timed, or is hot and pixel-bound is replaced by hand-written 6809. A runtime (`rt/`) supplies video, input, sound, timing and disk.

It is not an emulator: there is no interpreter or per-instruction dispatch at run time. The game's own memory map is kept: Apple RAM `$0000-$BFFF` is mapped 1:1, and zero page is DP 0.

Run all commands from the kit repo, or from a port made with `kit.newport` through its `run.sh`.

## When to use / not use
- Use for an Apple II game (48K/64K, hi-res, Disk II), whether or not it is copy-protected, as long as it boots in the host emulator.
- For C64 games, read `docs/machines/c64.md` first. It describes the design; the machine layer is not implemented yet.
- Not for double hi-res, 128K aux-memory or 65C02-only games without first extending the host emulator and the converter. `docs/machines/apple2.md` lists the gaps.

## Workflow (one step at a time; each has a check)

| # | step | command (in the port dir) | check |
|---|---|---|---|
| 0 | Scaffold | `python3 -m kit.newport NAME [--disk DSK]` (from the kit) | `./run.sh` exists |
| 1 | **Identify and open the disk**: format, file system, protection, boot chain, hand-over PC, level loader (`docs/DISKS.md`) | `python3 -m kit.disk.a2disk info IMAGE`, then `cat` / `get` / `todsk`; `a2-disassemble`, `a2-trace-calls` skills | format known; hand-over address known |
| 2 | Snapshots: set `HAND_OVER` (and the level triggers) in `NAME/snapshots.py` | `./run.sh snapshots` | `build/snap/snap_*.json` pc = hand-over |
| 3 | Profile: play-through scenarios in `NAME/scenarios/*.json` (keys, joystick, phases; `pokes` such as infinite lives to reach late levels) | `./run.sh profile` | `build/coverage.json` reaches every level (every disk load seen) |
| 4 | **Survey: what to transcode** (hardware through the map `kit/hw/apple2.py`; section 7 proposes hardware idioms, section 8 ranks what will not transcode) | `./run.sh survey --verify` (each proposal tested against its 6502 code, timing included) | `build/survey.txt`, `build/draft_spec.py` |
| 5 | Spec: confirm the candidates against the listing; fill `NATIVE`, `NATIVE_RANGES`, `NATIVE_EXITS`, `HW_IDIOMS` (uncomment the verified proposals you confirm), `HW_OVERRIDES` (only for trapped switches that do not matter), `SMC_*`, `TRAPS`, `CODE_RANGES`, `STAGES` (restart or call stages), `HIRES_ACCESS`, `ROMDATA` | `python3 -m kit.listing PORT RANGE` (executed code with counts); `kit.listing PORT --a2dis build/dis RANGE` (the `a2-disassemble` skill seeded with the profile's entries); `docs/DECIDE.md`, `docs/SPEC.md` | `./run.sh gen` has no errors (no unmapped or trapped hardware access) |
| 6 | Differential test of the generated code | `./run.sh test_engine -n 300 [--set IMAGE]` | `diff: 0` |
| 7 | Natives, for what the idiom library does not cover: start from `rt/natives/apple2/*`; test them bit-exact against the 6502 (a port's `test_native.py` is the model) | | `diff: 0` |
| 8 | Build: choose the target (`docs/MEMORY.md`) | `./run.sh build512` / `build128` | disk built, budgets asserted |
| 9 | MAME: boot, invariant, speed | `kit/mame/*.lua` (see "MAME probes") | 0 inconsistent; draws/frame ≈ Apple |
| 10 | User playtest (the user runs XRoar); fix, then re-run steps 6-9 | | user confirms |

Rules that hold at every step:
- **Never commit game bytes**, ROMs, snapshots, generated source or disks. They are derived from the user's disk and go to `build/` only.
- **Bit-exact before fast.** A native must leave registers, flags and zero page exactly as the 6502 does, as far as the callers read them (`report.txt`). Prove it with the differential tests, then optimize.
- **The Apple bitmap stays authoritative.** Natives draw into Apple RAM; the converter derives the GIME screen from it. This keeps artifact colours exact and makes every frame checkable.
- **Measure like for like.** Compare the speed of the same scene on both machines, checked with screenshots, as CPU work per frame (for example draws per frame), not as raw flips.
- Tests run in MAME (headless, `-nothrottle`). Playtests are the user's.

## Step 1 in detail: Apple II disk formats (`docs/DISKS.md`)
- **`a2disk info`** identifies by content:
  - `.dsk`/`.do` (DOS order) and `.po` (ProDOS order; also found inside a `.dsk`), 140K each;
  - `.nib` (35×6656 nibbles);
  - `.woz` (WOZ1/2 bit streams);
  - `.2mg`;
  - `.d13` (DOS 3.2): identified only;
  - AppleSingle, ShrinkIt, Binary II: archives; unpack them with CiderPress II first.
- It also reports the boot-sector kind, the file system and, for nibble/bit images, the sectors that are not standard (copy protection).
- **File systems:** DOS 3.3 (VTOC T17 S0) and ProDOS (volume directory at block 2). `a2disk cat` lists the files with types and load addresses; `a2disk get IMAGE NAME OUT` extracts one, removing the 4-byte header of a DOS `B` file and printing its load address. "None" means a custom track loader: the code comes from snapshots, not files.
- **Booting:** `kit/host` boots every bootable format directly. `.nib`/`.woz` keep their protected tracks, so never convert a protected disk to `.dsk` to port it.
- **Then find the boot chain:** boot on the host, watch where Disk II activity stops and where the title loop waits, and disassemble the loader stages. Set `HAND_OVER` to the first PC of the game proper after the loader/relocator.

## Least likely to transcode (summary of `docs/DECIDE.md`)
Most likely to be replaced first. Survey section 8 applies this list to the game.

| # | what | outcome |
|---|---|---|
| 1 | Loaders, RWTS, copy protection, relocators | never transcoded: snapshots plus `nt_load` |
| 2 | Cycle-timed sound beyond a single voice (multi-voice players, sample playback, notes that poll the keyboard) | always a hand-written native |
| 3 | Hardware the map traps on: language card, aux memory, double hi-res, slot I/O, and text/lo-res/mixed display (tracked, not shown) | extend the kit first, or `HW_OVERRIDES` when the access doesn't matter |
| 4 | Code the transcoder rejects: decimal mode, `PHP/PLP`, `TSX/TXS`, `RTI`, `BRK`, `JMP (ind)`, the V flag, opcode SMC | a native, or extend `xc65` |
| 5 | Hot hi-res code: blitters, fills, scrolls | transcodes correctly but too slowly: a native for speed |
| 6 | Standard hardware idioms: VBL polls, counted delays, single-voice tones, paddle count loops, `WAIT`/`PREAD`/`BELL` | library routines (`rt/hw/apple2/`), proposed by the survey with test evidence; one `Idiom(...)` line each |
| 7 | Everything else: game logic, AI, tables, menus, keyboard polling | transcodes |

## What to transcode vs replace (the rules; full text in `docs/DECIDE.md`)

| rule | when | then |
|---|---|---|
| **H** hardware | the routine reads or writes `$C0xx` (beyond what the runtime handlers cover) | native |
| **T** timing | cycle-counted loops: speaker tones, paddle timers, delays, VBL polling | native, in Apple time |
| **V** video | hi-res writers and readers that carry a large share of the hi-res traffic | native (`HRON`, `vm_rect`) |
| **P** performance | over ~5% of cycles and pixel- or shift-bound (`MUL`, tables) | native |
| **R** ROM | monitor/firmware calls (`WAIT`, `PREAD`, `COUT`, …) | native, or a `ROMCONST` for data reads |
| **S** SMC | patched opcodes or operands | `SMC_BYTES`/`SMC_IMM`, or inside a native |
| **L** loaders | boot, RWTS, relocators, decompressors | dropped: snapshots plus the native level loader (`nt_load`) replace them |
| everything else | game logic, AI, tables, menus | transcode |

## The hardware transcoder (`kit/hw/`)
- **Hardware map** (`kit/hw/apple2.py`). Every soft switch and firmware entry has one action:
  - a runtime handler or latch;
  - ignored;
  - a trap, which is a generation error unless the spec overrides it;
  - loop-only, which is an idiom.

  The generator, the survey, the builds and the docs all read this map, so there are no silent defaults.
- **Idiom library** (`rt/hw/apple2/*.asm`, `kit/hw/lib.py`). Routines with parameters, timed in Apple cycles × 1.75 through `hw_dly`:
  - `vbl_poll`, `delay`, `tone` and `paddle_count`;
  - `rom_wait`, `rom_pread` and `rom_bell`.
- **Matchers** (`kit/hw/match.py`). They find those idioms and only *propose* them. Near-misses come with a reason; these are the hand natives.
- **Evidence** (`kit/hw/verify.py`, `survey --verify`). Each proposal is run against the 6502 code on random cases, comparing registers, flags and RAM. Timing is compared on click intervals, total time, or a paddle-timer model, depending on the kind.
- **Tests:** `python3 -m tests.hw_checks [GROUP]`.

## Runtime services (already written; see `docs/NATIVES.md`)
- **Keyboard.** `$C000`/`$C010` with Apple-style auto-repeat; the CoCo matrix is mapped to ASCII; BREAK = ESC.
- **Buttons and joystick.** `$C061`/`$C062` and `,X`; right stick in `joy_x`/`joy_y` (0..63).
- **Speaker.** `$C030` clicks the DAC.
- **Pages.** `$C054`/`$C055` select a page and convert it.
- **VBL.** `rt_vbl`.
- **Hi-res → GIME converter** (320×192×16, exact artifact colours), with dirty rectangles, a scroll fast path and, on 128K, a log + beam-timed commit.
- **Disk.** Fast loader with a title screen (`<loader>.BIN`); on 128K, a disk streamer.
- **Also:** LZSS, MMU helpers (`hr_on`/`hr_off`, `rt_bank`) and `x_trap`, which halts with a red border on an untranslated transfer.

## Targets
- **512K:** two GIME buffers, everything resident and compressed in RAM. Simplest, and smooth.
- **128K:** one GIME buffer, no shadow; changes are logged and committed timed to the beam; boot data and levels are streamed from disk.

`ram` in `port.toml` picks the default; `./run.sh build512` and `./run.sh build128` build either one.

## MAME probes (`kit/mame/`, env `KIT=<kit repo>`, `CMD='LOADM"<loader>":EXEC'`, `INPUT=<script>`)
- `prof.lua`: flips/ticks every n frames (`FLIPS`), debugger trace (`TRACE=start,len`), snapshots (`SNAP`). Turn a trace into cycles per routine with `python3 -m kit.mame.profile`.
- `inv512.lua` + `kit.mame.invcheck512`, `inv128.lua` + `kit.mame.invcheck128`: the screen equals the conversion of the Apple page.
- `run_mame.sh DSK CMD OUTDIR FRAMES...`: headless PNGs (`RAM=128k` for the 128K target).
- Input scripts: `kit/mame/input.lua` format (`T key SPACE`, `T joyx 1023`, `T btn 1`, `T1-T2/N keys QAZ`).

## Docs
- `docs/DISKS.md`: identifying and opening Apple II disk images (step 1).
- `docs/METHOD.md`: the pipeline and the transcoder ABI.
- `docs/SPEC.md`: every spec field.
- `docs/DECIDE.md`: transcode vs native.
- `docs/NATIVES.md`: the runtime services and native contracts.
- `docs/MEMORY.md`: 512K and 128K maps.
- `docs/LESSONS.md`: pitfalls found in real ports.
- `docs/COVERAGE.md`: the profile format.
- `docs/machines/apple2.md`, `docs/machines/c64.md`.
