# Step 1: identify and open the Apple II disk

Tool: `python3 -m kit.disk.a2disk` (`info`, `cat`, `get`, `todsk`, `tonib`). The host emulator (`kit/host`), the snapshots and the profiler accept every bootable format below directly, so no conversion is needed to start.

## 1. Which format is it?
Run `a2disk info IMAGE`. It identifies the format by content first and by extension second.

| format | extensions | size / signature | what it holds | boots in kit/host |
|---|---|---|---|---|
| DOS-order sectors | `.dsk`, `.do` | 143,360 bytes | 35 tracks × 16 sectors, DOS 3.3 logical order | yes |
| ProDOS-order sectors | `.po` (sometimes `.dsk`) | 143,360 bytes | the same sectors in ProDOS block order; detected by content (volume header at block 2) | yes (reordered) |
| nibbles | `.nib` | 232,960 bytes = 35 × 6656 | raw disk bytes per track, without timing | yes, as is: keeps most copy protection |
| WOZ | `.woz` | `WOZ1`/`WOZ2` + `FF 0A 0D 0A` | bit streams with timing, made by Applesauce; the best preservation format | yes: whole tracks from the bit stream; quarter tracks are reported, not used |
| 2IMG | `.2mg`, `.2img` | `2IMG` header | a DOS, ProDOS or NIB payload behind a 64-byte header | yes |
| 13-sector | `.d13` | 116,480 bytes | DOS 3.2 (5-and-3 encoding) | no: convert with CiderPress II, or extend the tool |
| AppleSingle | `.as` | `00 05 16 00` | one file with its type and load address | not a disk: send it straight to the `a2-disassemble` skill |
| ShrinkIt | `.shk`, `.sdk`, `.bxy` | NuFX `4E F5 46 E9 6C E5` | an archive of files or of a whole disk | unpack with CiderPress II or `nulib2`, then run `info` on the result |
| Binary II | `.bny`, `.bqy` | `0A 47 4C` | an archive | unpack with CiderPress II |

For copy-protected games, prefer `.woz` or `.nib`: a `.dsk` keeps only standard sectors. A cracked `.dsk` (like most "(cracked)" releases) is fine and is the easiest input.

## 2. What is on it?
`a2disk info` also reports:
- **Tracks with data.** A low count means a small game; tracks past the data are free.
- **Standard decode** (`.nib`/`.woz`): how many of the 560 sectors decode as normal 16-sector 6-and-2. Tracks that don't are copy protection, or a custom format the game reads with its own RWTS. Boot the image as it is; the snapshots capture the result.
- **Boot sector.** DOS 3.3-style boot 0 (`01 A5 27 C9 09`, used by DOS and by most custom loaders), ProDOS boot block, or custom.
- **File system:**
  - **DOS 3.3** (VTOC at track 17, sector 0): `a2disk cat` lists the files with type (`B` binary, `A` Applesoft, `T` text) and size. `a2disk get IMAGE "NAME" out.bin` extracts a file and prints a `B` file's load address.
  - **ProDOS** (volume directory at block 2): `cat` lists the paths with type, EOF and aux type (the load address for `BIN`/`SYS`). `get` extracts by path.
  - **None:** the game reads raw tracks with its own loader (most commercial games). There are no files to extract; the snapshots are how the code is obtained.

## 3. How does it boot? (needed for `HAND_OVER` and the level loader)
1. Boot on the host and watch where it runs and what it touches. `kit/host/profile.py` with `--phase` names frame ranges; a quick probe that prints the busiest pages and the softswitches every 300 frames shows when loading stops (Disk II accesses `$C0E0-$C0EF` drop to 0). It also shows the idle loop of the title screen.
2. Read the boot sector and the loader stages with the `a2-disassemble` and `a2-trace-calls` skills. Load the bytes from `a2disk todsk` or from a host RAM dump.
3. The **hand-over** is the first PC after the boot loader, relocator or decompressor that belongs to the game proper. It is usually a `JMP` from the loader's last stage into the main code. Set it as `HAND_OVER` in `snapshots.py`.
4. Find **level loads**: Disk II activity after the hand-over (`$C08C,X` reads), and the routine that starts them (a level number stored in RAM, then a `JSR` or `JMP` into the RWTS). Each level becomes an image in `STAGES`, captured by running that loader on the host (`snapshot.record_writes` records which pages it fills).
5. Note what the game needs: RAM size (does it touch `$C08x` language-card banking, or aux memory `$C002-$C005`?), video modes (`$C050-$C057`, `$C05E` double hi-res), and the ROM entries it calls. `kit.survey` reports these after the profile.

## Converting
- `a2disk todsk IMAGE out.dsk` gives a DOS-order sector image. It works for `.po`/`.2mg`, and for `.nib`/`.woz` when every sector is standard; non-standard sectors are listed.
- `a2disk tonib IMAGE out.nib` gives nibble tracks from any bootable format. For example, use it to boot a `.po` in another emulator.
- Copy-protected disks: never "fix" them by converting to `.dsk`. Boot the `.nib`/`.woz`; the port only needs the snapshots.
