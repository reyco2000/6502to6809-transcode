# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A kit for porting 6502 games (Apple II today; C64 designed only, in `docs/machines/c64.md`) to the TRS-80 Color Computer 3 by **static transcoding**: game code is translated to 6809 ahead of time (no interpreter at run time), and hardware-bound or hot routines are replaced by hand-written 6809 "natives". Targets: 512K and 128K CoCo 3.

The repo is also a Claude Code skill: `~/.claude/skills/6502to6809` symlinks here and `SKILL.md` is its entry point (the step-by-step porting workflow). Keep `SKILL.md`, `README.md` and `docs/` consistent when behaviour changes.

## Commands

Run from the repo root (modules use `python3 -m`; there is no package install, lint config or test framework).

```
make -C sim                          # build sim/cc3sim (6809 unit-test machine, needed by difftests)
python3 -m tests.selftest            # kit self-test, no game data needed (opcodes, LZSS, RS-DOS image,
                                     # GIME tables, xc65+cc3sim difftest, scaffolder, disk formats)
```

Reference port (needs the user's Karateka disk and Apple ROMs, paths in `ports/karateka/port.toml`):
```
python3 -m ports.karateka.snapshots
python3 -m ports.karateka.test_engine -n 300 [--set engine|IMAGE] [--entry HHHH] [-v]   # one routine: --entry
python3 -m ports.karateka.test_native -n 300 --ram 512
python3 -m ports.karateka.build512   # build/karateka/karateka_512k.dsk  (LOADM"K3":EXEC)
python3 -m ports.karateka.build128
```
Regression baseline: both Karateka disks must stay byte-identical to the original k3port builds.

New ports are scaffolded outside the repo (`python3 -m kit.newport NAME --disk DSK` → `~/proyectos/NAME-coco3`) and driven through their `run.sh` (`snapshots`, `profile`, `survey`, `gen`, `test_engine`, `build512`/`build128`), which puts the kit on `PYTHONPATH`.

External tools: Python 3.11+, lwasm at `~/opt/lwtools/lwasm/lwasm` (driven by `kit/asm.py`, which auto-relaxes overflowing short branches), gcc, MAME with CoCo 3 ROMs for `kit/mame/*` probes.

## Architecture

Pipeline (details in `docs/METHOD.md`):

1. **Snapshots** — port's `snapshots.py` → `kit/snapshot.py` runs the game's own boot/loaders on `kit/host` (cycle-accurate Apple II + Disk II, `cpu6502.py`/`apple2.py`) and captures RAM+CPU state per *image* (`BASE`, usually `cold`, plus one per `STAGES` level).
2. **Profile** — `kit/host/profile.py` replays scenario JSONs → `coverage.json` (executed instructions, EA classes, SMC, loaders).
3. **Survey** — `kit/survey.py` applies the transcode-vs-native rules (`docs/DECIDE.md`: H/T/V/P/R/S/L) → `survey.txt`, `draft_spec.py`.
4. **Discover + split** — `kit/discover.py` (recursive descent from entries, coverage and `NATIVE_EXITS`; disassembly listings are not trusted as code maps) and `kit/program.py`: bytes identical in every image = **engine**, the rest = per-image **overlays**; engine→overlay calls go through `OVT_xxxx` vectors (missing ones become traps).
5. **Generate** — `kit/gen.py` → `kit/xc65.py` → `build/src/engine.asm`, `ovl_<image>.asm`, `report.txt` (native contracts: what each native must produce for its callers).
6. **Differential test** — `kit/test_engine.py`/`kit/difftest.py`: translated code in `sim/cc3sim` vs the 6502 reference, from real snapshot state with random registers.
7. **Build** — `kit/build512.py` / `kit/build128.py` + `kit/fastload512.py`/`fastload128.py` + runtime `rt/t512|t128/*.asm` → RS-DOS `.dsk` (`kit/disk/mkdsk.py`, `lzss.py`).
8. **MAME probes** — `kit/mame/*.lua` + `invcheck512/128.py` (invariant GIME == conv(Apple page)), `profile.py`.

Kit modules are game-independent; a port supplies a spec module (`docs/SPEC.md`; generic parts in `kit/specbase.py`, paths from `port.toml` via `kit/portcfg.py`) and its thin `gen.py`/`build*.py`/`test_engine.py` wrappers call into the kit. `ports/karateka/` is the worked example; nothing in the kit depends on it.

### Transcoder ABI (`kit/xc65.py`) — invariants to preserve
- 6502 A→6809 A, X→B, Y→byte `RY` (`RYW` word with zero high byte, for `(zp),Y`); 6502 stack = 6809 S (native JSR/RTS); DP=0 is zero page. 6809 X and U are template scratch.
- N/Z/C kept in 6502 polarity in CC at every label; V not modelled. Liveness analysis drops unread flag work.
- Rejected at generation: `BVC BVS CLV PHP PLP RTI BRK SED TSX TXS` (→ native or extend xc65). No decimal mode, no 65C02 opcodes (`kit/opcodes.py` is NMOS-only, 151 opcodes).
- `$C000-$C0FF` → `io_rd_Cxxx`/`io_wr_Cxxx`/`io_rdx_Cxxx` handlers; ROM data via `ROMCONST`; operand SMC via `SMC_IMM`, opcode SMC only inside natives.
- Labels: `E<HHHH>` engine, `<IMAGE>_<HHHH>` overlays.
- Apple RAM `$0000-$BFFF` maps 1:1 to CoCo logical memory; data never moves. Engine window `$2000-$5FFF` on 512K (see `docs/MEMORY.md`).

### Video model
The Apple hi-res pages stay authoritative: game and natives draw into Apple RAM and report changes (`vm_rect`/`vm_page`); the runtime converts to GIME 320×192×16 on page select or VBL using tables from `kit/tables.py` (same artifact-colour rules as `kit/host/hires.py`). 128K has one GIME buffer with a logged, beam-timed commit; 512K has two buffers.

## Rules

- **Never commit anything derived from a game disk**: `build/`, `*.dsk`, `*.woz`, `*.nib`, `*.bin`, ROMs, snapshots, generated source (see `.gitignore`). Specs hold addresses only.
- **Bit-exact before fast**: a native must leave registers, flags and RAM as the 6502 routine does (as far as callers read them, per `report.txt`), proven by difftests (`ports/karateka/test_native.py` is the model), before optimizing.
- Compare speed like for like (same scene, CPU work per frame), not raw flips. Pitfalls from real ports are in `docs/LESSONS.md`.
- Tests run in MAME headless (`-nothrottle`); playtesting is the user's job.
