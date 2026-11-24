# Method: static 6502 → 6809 transcoding for the CoCo 3

## Why static transcoding
An emulator (interpreter or JIT) on a 1.79 MHz 6809 runs a 1.02 MHz 6502 game at about a third of full speed. The approach here, used first for Robotron and Karateka, works differently:

1. The game's code is **translated ahead of time**, instruction by instruction, into 6809 assembly.
2. Liveness analysis keeps only the register and flag work later code reads, so ordinary game logic runs near real time: about 2-4 CoCo cycles per Apple cycle, where 1.75 is real time.
3. A few routines carry most of the CPU time, typically sprite blitters, fills and scrolls. Together with everything that touches hardware or is cycle-timed, these are **replaced by hand-written 6809** (natives).
4. A resident **runtime** maps Apple RAM, converts the Apple hi-res page into a GIME screen, and handles keyboard, joystick, sound, timing and disk.

The game keeps its own memory map: Apple `$0000-$BFFF` is CoCo logical `$0000-$BFFF`, and zero page = DP 0. Data never moves, so pointers, tables and self-modification behave as on the Apple.

## Pipeline

| stage | tool | output (build/) |
|---|---|---|
| snapshots | port `snapshots.py` → `kit/snapshot.py` on `kit/host` (cycle-accurate Apple II, Disk II) | `snap/snap_<image>.bin/.json/.mask` |
| coverage profile | `kit/host/profile.py` with scenarios | `coverage.json` (executed instructions, EA classes, SMC, loaders) |
| survey | `kit/survey.py` | `survey.txt`, `draft_spec.py` |
| code discovery + split | `kit/discover.py`, `kit/program.py` | engine = code identical in every image; overlays = the rest, one per image |
| generation | `kit/gen.py` → `kit/xc65.py` | `src/engine.asm`, `src/ovl_<image>.asm`, `ovt.inc`, `xiy.asm`, `report.txt` |
| differential test | `kit/test_engine.py` → `kit/difftest.py` + `sim/cc3sim` | same / diff / timeout counts |
| build | `kit/build512.py` or `kit/build128.py`, `kit/fastload*.py`, `rt/t512|t128/*.asm` | `<name>_<ram>k.dsk` |
| MAME probes | `kit/mame/*.lua`, `invcheck*.py`, `profile.py` | invariant samples, flips, cycle profile |

### Images and the engine/overlay split
A game's memory changes as it loads levels. Every distinct memory state is an **image**, captured by a snapshot at its first translated instruction:
- `BASE` (usually `cold`) is the image the boot hands over to.
- `STAGES` are the overlay images (levels, events) in the order the native loader numbers them.

`program.split` compares the images:
- An instruction whose bytes are identical in every image is **engine** code, translated once.
- Everything else is **overlay** code per image.
- Engine → overlay calls go through a vector table (`OVT_xxxx`). An entry that does not exist in some image becomes a trap there.

Code is found by recursive descent from:
- the snapshot entries and return chains;
- every executed instruction in the coverage profile;
- `NATIVE_EXITS`.

Disassembly listings are not trusted as code maps: they mark executed code as data.

### Transcoder ABI (`kit/xc65.py`)
- **Registers.** 6502 A → 6809 A. X → B. Y → byte `RY`; `RYW` is a word with a zero high byte, used for `(zp),Y`. The 6502 stack is the 6809 S: JSR/RTS are native and push 6809 addresses.
- **Scratch.** 6809 X and U are free for templates.
- **Flags.** N, Z, C are kept in 6502 polarity in CC at every label. V is not modelled.
- **Rejected input** (an error at generation): `BVC BVS CLV PHP PLP RTI BRK SED TSX TXS`. A routine that uses them must become a native, or the transcoder must be extended (see "Known gaps").
- **I/O.** `$C000-$C0FF` goes to handlers `io_rd_Cxxx`/`io_wr_Cxxx`/`io_rdx_Cxxx` (value in `IOV`). Side-effect-free reads listed in `IO_MEM` become plain loads of runtime latches.
- **ROM.** ROM data reads come from `ROMCONST`; ROM calls must be natives.
- **Labels.** Code addresses become `<prefix><HHHH>`: `E` for the engine, `<IMAGE>_` for overlays.
- **SMC.** Operand SMC sites in `SMC_IMM` read their operand from memory at run time. Opcode SMC must sit inside natives (`SMC_BYTES`).
- **Natives.** A native's contract (what it reads and changes) is derived from the 6502 routine it replaces (`routine_contract`). It can also be given in `NATIVE_CONTRACTS`. `report.txt` prints what each native "must produce" for its callers.

### Natives
See `NATIVES.md`. Every native is tested bit-exact against the 6502 routine on random inputs from real snapshot state: the registers and flags the callers read, plus all of RAM. Speed work only starts after that.

### Video model
- The Apple hi-res pages stay the **authoritative frame buffers**: the game and the natives draw into Apple RAM.
- Natives report what they changed: `vm_rect` (a rectangle) or `vm_page` (a whole page).
- When the game selects a page (`$C054`/`$C055`) or waits for VBL, the runtime converts the changed byte pairs of that page into the GIME 320×192×16 buffer. Tables (`kit/tables.py`) reproduce the Apple's artifact colours exactly.
- This keeps the game's own read-modify-write drawing correct. It also gives a checkable invariant: GIME = conv(page), probed in MAME.

### Timing
- Transcoded logic runs as fast as it can.
- The game's own frame pacing (page flips, VBL waits) holds it to the Apple's frame rate through `rt_vbl`.
- Cycle-timed code (tones, delays, `WAIT`) is native and padded to Apple time.
- Speed is compared as CPU work per frame on the same scene (see `LESSONS.md`).

## Known gaps (extend when a game needs them)
- **Decimal mode (`SED`/`CLD` + ADC/SBC):** not translated. Many games keep scores in BCD. Either add decimal templates to `xc65` (ADC/SBC with `DAA`-like correction; the 6809 `DAA` only fixes up after ADD), or make the score routines natives.
- **V flag, `PHP`/`PLP`, `TSX`/`TXS`, `RTI`, `BRK`:** not modelled. Stack tricks need natives or a transcoder extension.
- **65C02 opcodes:** not decoded. `kit/opcodes.py` is NMOS only.
- **Double hi-res, 80-column, aux memory (IIe 128K):** neither the host emulator nor the converter supports them yet. See `machines/apple2.md`.
- **Code in the hi-res pages or in the language card (`$D000-$FFFF`):** the engine window is `$2000-$5FFF` (512K), and the code blocks are fixed. A game running code from the language card needs a new layout.
