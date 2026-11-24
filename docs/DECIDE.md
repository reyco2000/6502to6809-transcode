# Deciding what to transcode and what to replace

The default is **transcode**. A routine becomes a native only for one of the reasons below. `kit/survey.py` finds candidates for each rule from the coverage profile. Confirm every candidate against a disassembly (`a2-disassemble`, `a2-trace-calls` skills) before putting it in the spec.

## Least likely to transcode
Ranked from most to least likely to be replaced. `kit/survey.py` section 8 applies the list to a game, one line per routine.

1. **Loaders, RWTS, copy protection, relocators.** These are never transcoded. Snapshots capture what they produce, and the native level loader (`nt_load`) replaces them (rule L).
2. **Cycle-timed sound beyond a single voice.** Multi-voice and duty-cycle players, sample playback, and notes that poll the keyboard are always hand-written natives (rule T). The survey reports them as `tone` near-misses with the reason.
3. **Hardware the map traps on.** This covers the language card, IIe aux memory, double hi-res and slot I/O. Text, lo-res and mixed screens are tracked by the handlers but not displayed. `gen` refuses these accesses. Extend the kit first, or use `HW_OVERRIDES` when the access doesn't matter (rule H).
4. **Code the transcoder rejects.** This is decimal mode (`SED`), `PHP/PLP`, `TSX/TXS`, `RTI`, `BRK`, `JMP (ind)`, the V flag and opcode SMC. Make it a native, or extend `xc65` (rules S and `METHOD.md`, "Known gaps").
5. **Hot hi-res code.** Blitters, fills and scrolls transcode correctly but are too slow, so make them natives for speed (rules V and P).
6. **Standard hardware idioms.** VBL polls, counted delays, single-voice tones, paddle count loops and `WAIT`/`PREAD`/`BELL` use the library routines in `rt/hw/apple2/`. The survey proposes them with test evidence (`--verify`); you confirm each one and add one `Idiom(...)` line to `HW_IDIOMS` (rules H, T and R).
7. **Everything else transcodes.** That includes game logic, AI, tables, menus and keyboard polling.

## The rules

**H: hardware.** Every address has an action in the hardware map (`kit/hw/apple2.py`; table in `NATIVES.md` §1).
- A routine whose job is hardware I/O beyond what the runtime handlers already cover (`NATIVES.md`, table 1) becomes a native.
- A single `LDA $C000` or `STA $C030` in game logic is fine: it is translated to a handler call.
- A loop that polls hardware, or depends on an exact sequence of hardware accesses, is not. Examples: the Disk II nibble loop, `$C08x,X` slot I/O, language-card banking, video-mode sequences for mixed text/graphics.

**T: timing.** Check survey section 7 first: VBL polls, counted delays, single-voice tones, paddle counts and `WAIT` have library routines.
- Code whose effect depends on its cycle count must be native, padded to Apple time (1 Apple cycle ≈ 1.75 CoCo cycles). Examples:
  - speaker tone loops (pitch = cycles between `$C030` clicks);
  - paddle reads (count until `$C064` falls);
  - delay loops and `WAIT`;
  - VBL polling (`$C019`), and loops that count polls as a delay.
- The survey lists these as "timing code" per routine.
- Replace the **whole loop**, and exit where it exits (`NATIVE_EXITS`) with the registers it leaves.

**V: video.**
- The survey ranks routines by hi-res memory traffic. Routines that carry most of it (blitters, fills, scrolls, page copies, clears) become natives.
- Any translated store into a hi-res page is invisible: nobody calls `vm_rect` for it. Run a per-image hi-res scan (Karateka `hires_scan.py` is the model): play each image on the host with random input and assert that no translated instruction touches `$2000-$5FFF`.

**P: performance.**
- A routine above about 5% of the estimated cycles that is pixel-, shift- or table-bound: the 6809's `MUL`, 16-bit registers, `PSHS`/`PULU` block moves and indexed modes beat the translation by 2-5×.
- Measure first: `test_engine` prints CoCo cycles per Apple cycle; 1.75 is real time.
- Pure logic usually translates well enough; leave it.

**R: ROM.**
- Every call into monitor or firmware ROM becomes a native (`WAIT`, `PREAD`, `COUT`, `HOME`, `RDKEY`, `BELL`, Disk II `$C65C`).
- Reads of ROM bytes as data (ID bytes, tables) become `ROMCONST` entries.
- `SETKBD`/`SETVID`/`INIT` at boot usually vanish with the boot code.

**S: self-modifying code.**
- An operand patched at run time (an address or immediate that changes) goes in `SMC_IMM`; the translation reads the operand from memory.
- A patched opcode cannot be translated. Put the routine inside a native (the native takes the patched value as a mode) and list the byte in `SMC_BYTES` if it differs between images.

**L: loaders.**
- The boot sector, RWTS, relocators, decompressors and copy protection are **not translated**.
- Snapshots capture their result.
- The native level loader (`nt_load`) restores exactly the pages each load writes (`.mask`) and enters the level from its snapshot state. On 512K they come compressed from RAM; on 128K they are streamed from disk.
- A "reboot" path (game over that re-boots the disk) becomes `nt_reboot`.

## Checklist per candidate
1. Entry address(es) and the full body range go in `NATIVE`/`NATIVE_RANGES`. Every other jump into the range is a generation error, which is useful: it finds missed entries.
2. Exits into translated code go in `NATIVE_EXITS`.
3. Inputs and outputs: registers, flags, zero page, tables in RAM. `report.txt` shows what the callers read.
4. A bit-exact test against the 6502 routine on random inputs from real snapshot state.
5. Dirty marking for every changed bitmap byte: `vm_rect` once per call, with the tightest rectangle that is cheap to know.
6. The cost ratio from the native test (CoCo cycles per Apple cycle).

## What the survey cannot know
- Whether a hi-res routine's traffic is sprites (draw) or scenery (scroll/copy): read the code.
- Whether a "hot" logic routine is hot because it waits: polling loops look hot.
- Code the scenarios never reached. Profile every level and mode with several scenarios (`--scenario` is repeatable, `--merge` combines runs).
