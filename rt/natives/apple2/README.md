# Apple II native templates

These are starting points for a port's `src/*.asm`. Copy the ones you need into the port, rename the labels, and adapt them to the 6502 routine they replace.

Each template states the contract it keeps. The runtime (`rt/t512/rt.asm`, `rt/t128/rt.asm`) already provides the I/O handlers listed in `docs/NATIVES.md`. These files cover the cases a handler can't: whole loops and routines that must be replaced.

| file | replaces | how |
|---|---|---|
| `hires.inc` | — | `HRON`/`HROFF` macros: map the two bitmaps at `$2000`/`$4000` inside a native; dirty-marking reminders |
| `fill_template.asm` | a hi-res rectangle fill / copy routine | skeleton: row table, `HRON`, inner loop, `vm_rect`, exact 6502 exit state |
| `vbl.asm` | `LDA $C019 / BMI loop`-style VBL waits (IIe), or a timing loop used as a frame wait | `jsr rt_vbl` |
| `wait.asm` | monitor `WAIT` (`$FCA8`) | the same delay in CoCo cycles: `(26+27A+5A²)/2` Apple cycles |
| `paddle.asm` | `PREAD` (`$FB1E`) or a game's own `$C070`/`$C064` count loop | a count from the runtime's `joy_x`/`joy_y` (0..63) |
| `speaker.asm` | cycle-timed `$C030` tone loops | the same period structure, the click through the DAC (`io_wr_C030`), timed to Apple cycles |

## Calling convention (every native)
- A = 6502 A, B = 6502 X, `RY` = 6502 Y (`RYW` = 0:Y), S = 6502 stack, DP = 0, so zero page is `<$nn`.
- **Exit state.** On exit, leave A, X(B), Y(RY) and the N/Z/C flags exactly as the 6502 routine leaves them, but only where the caller reads them. `report.txt` lists each native's "must produce" set, which `xc65.routine_contract` derives.
- **Zero page.** Leave every zero-page byte the 6502 routine writes exactly as the 6502 would. `test_native` compares all of RAM.
- **Natives that never return** (they jump into translated code) use `jmp E<addr>` and list that address in `spec.NATIVE_EXITS`.
