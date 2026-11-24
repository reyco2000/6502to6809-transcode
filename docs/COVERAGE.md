# Coverage file format (`host/profile.py --coverage`, format "a2cc3-coverage", version 1)

Producer: `host/profile.py` (T1).  Consumer: `tools/xlate.py` (T4: units, CFG, banks).  One JSON object, written
compactly.  Lives in the build dir (it holds program bytes), never in the repo.  `validate_coverage(cov)` in
host/profile.py checks every structural rule stated here; `merge_coverage([...])` merges files.

Conventions:
* Addresses are 4-digit upper-case hex strings (`"19E6"`), pages 2-digit (`"19"`).  Byte strings are upper-case hex.
* Counts are executions / events summed over all merged runs.  Frames are per run (60 Hz host frames of 17030
  cycles, frame 0 = start of the run); a frame value is only meaningful together with its `run`.
* Image ids, episode ids and run indexes are file-local: they equal the position in their list and every reference
  inside the file uses them.  Merging renumbers all three consistently (never compare ids across files).
* EA class bits (`EA_NAMES`): 1 `zp` $00xx, 2 `stack` $01xx, 4 `ram0` $0200-$1FFF, 8 `hires` $2000-$5FFF,
  16 `ram6` $6000-$BFFF, 32 `io` $C000-$C0FF, 64 `rom` $C100-$FFFF (ROM, slot ROM, language card).
* Only RAM below $C000 is imaged.  Code executed at $C000+ (ROM, PROM, language-card RAM) appears only in
  `rom_exec` / `rom_entries`; the translator keeps it interpreted.

## Top level

| key | type | meaning |
|---|---|---|
| `format`, `version` | `"a2cc3-coverage"`, `1` | |
| `runs` | list of run | one per profiled scenario run, in merge order |
| `min_s` | int | lowest 6502 S seen in any run (after JSR, PHA, PHP, BRK, TXS and any ROM instruction) |
| `images` | list of image | code images, sorted by page, then first execution (run, frame) |
| `episodes` | list of episode | load episodes, per run sorted by first frame |
| `writers` | list of writer | store PCs that wrote executed bytes (per run) |
| `bulk_writers` | list of addr | union over runs of writers with `bulk` true |
| `loaders` | list of addr | union over runs of writers with `loader` true |
| `rom_entries` | list | transfers from RAM code (or the run start) into $C000+: `[kind, site, site_image, target, count]`; `kind` as for entries, `site` addr or null, `site_image` image id or -1 (site in ROM / none) |
| `rom_exec` | {addr: count} | executions per instruction start at $C000+ |
| `summary` | object | derived counts (`summarize()`), informative only |

### run
`index`, `name`, `frames` (requested), `frames_run`, `stop` {`reason`: `frames` / `stop_pc` / `illegal` /
`max_instr`, `pc`, `frame`}, `phases` [[name, first frame, end frame (exclusive)]], `instr`, `cycles`, `wall_s`,
`min_s`, `bulk` (BULK threshold used), `episode_gap` (frames), `io` {`"r:C030"` / `"w:C054"`: count} (all
$C0xx accesses of the run).

## image

A code image is the code of one 256-byte page as it was during an interval of the run.  A page's current image ends
when bytes executed in it were changed by a bulk writer (see writer) and code on that page runs again; stores by other
writers to executed bytes are SMC and keep the image.  After a run, and again on every merge, images of one page are
**merged when they hold the same code** ("image identity"): for every offset executed in either image, excluding
both images' `volatile` bytes, the two `bytes` values are equal.  Merging is greedy in (page, first execution) order:
an image joins the first earlier group it is compatible with.  So one image may have several `loads` (the same
overlay loaded again, the same engine in several runs) and two images on one page always differ in at least one
non-volatile byte that one of them executed (overlays).

| key | type | meaning |
|---|---|---|
| `id` | int | position in `images` |
| `page` | "PP" | page ($00-$BF) |
| `bytes` | 512 hex | page content: executed bytes = value at their first execution in this image; other bytes = value at the image's first execution (first instruction executed on the page).  On merge, bytes executed only by a later member are taken from it |
| `role` | 256 chars | per offset: `.` never executed, `o` executed as opcode (instruction start), `a` as operand, `x` both (overlapping instructions or SMC that moved an instruction boundary) |
| `nbytes` | int | number of executed bytes (`role` != `.`) |
| `count` | int | instructions executed in this image (= sum of `instr`) |
| `instr` | {addr: count} | executed instruction starts (role `o`/`x`) and their execution counts.  The instruction is `bytes` decoded at addr, except at SMC sites (see `smc`) |
| `ea` | {addr: mask} | memory instructions (all r/w/rmw modes except JMP/JSR): OR of the EA classes of all their accesses.  Indexed / indirect EAs are the final address |
| `flags` | {addr: mask} | 1 ADC/SBC executed with D=1; 2 zp,X / zp,Y index wrapped past $FF or (zp),Y / (zp,X) pointer at $FF; 4 PLA/PLP pulled a byte pushed by JSR (return-address pop); 8 TXS; 16 BRK; 32 RTI |
| `entries` | list | how execution arrived at instruction starts of this image: `[target, kind, site, site_image, count]`, sorted.  `site` = PC of the transferring instruction (null for `start`), `site_image` = image id of the site (-1: site in ROM / none) |
| `smc` | {addr: smc} | instruction starts at which a changed byte was executed (see below) |
| `volatile` | [addr] | bytes of this page that were executed (as opcode or operand) after a non-bulk writer changed them since their previous execution: the SMC bytes |
| `data_writes` | {addr: dw} | bytes of this page stored to between the image's first and last execution (any writer, any value; pages $00/$01 are not recorded): `{"n": stores, "changed": any store changed the value, "writers": [store PCs, at most 16]}`.  `n` counts all stores while the image was current (also after its last execution) |
| `writers` | {addr: n} | per store PC, how many executed bytes it wrote last before their first execution (who loaded the code) |
| `loads` | list | one per merged member: `{"run", "episode", "load_frame", "first_frame", "last_frame"}`: load episode, load frame (below), frames of the first and last instruction executed in it |
| `episodes` | [int] | sorted distinct `loads[].episode` |

Entry kinds (`ENTRY_KINDS`):

| kind | the instruction before the target was |
|---|---|
| `start` | none: first instruction of the run |
| `jsr` | JSR (site = JSR address) |
| `jmp` | JMP abs |
| `jmpi` | JMP (ind) (pointer address = operand of the site instruction) |
| `br` | a taken branch |
| `fall` | a sequential instruction (or untaken branch) whose next instruction is on another page |
| `rts` | RTS returning to the innermost JSR+3 with both bytes pushed by JSR (normal return) |
| `rts_skip` | RTS with JSR-pushed bytes but not to the innermost call (after a PLA/PLA return pop, stack tricks) |
| `rts_pha` | RTS whose return bytes were (at least partly) pushed by PHA: computed jump |
| `rts_other` | other RTS (bytes stored by ordinary stores, PHP, ...; also any RTS executed in ROM that does not match the call stack) |
| `rti`, `brk` | RTI / BRK |

Every control transfer that reaches RAM is recorded (also from ROM: e.g. the boot PROM's `JMP $0801` is a `jmp`
entry with site $C6xx, site_image -1; a ROM routine's RTS back into RAM is `rts`).  Return sites of JSRs that
never returned are not entries.

### smc (per instruction start)
A site is recorded from the first execution at which one of its instruction bytes had been changed by a non-bulk
writer since that byte's previous execution; from then on every execution at the site records the byte tuple.

| key | meaning |
|---|---|
| `kind` | `opcode` if the site's own address is in `volatile` (its opcode byte changes), else `operand` |
| `tuples` | `[[len, "B0B1B2", count], ...]`: distinct (instruction length, 3 bytes from the site) seen at execution, with counts.  Only the first `len` bytes are the instruction; the rest are what followed it.  Executions before the site was first seen changed are counted under the original tuple (first-execution values) |
| `writers` | store PCs whose changes were executed at this site |
| `changed_execs` | executions at which some instruction byte had changed since its previous execution |

An opcode SMC region (design 3.7) is the union of the spans `[site, site+len)` of overlapping opcode sites, e.g.
$19E6 (`EAEA..` len 1 / `1100..` len 2) + $19E7 (`EA` len 1) = $19E6-$19E7; its variants are the tuples
of the first site truncated to the region length (regions up to 3 bytes are exact).  Executed bytes that are in
`data_writes` but not `volatile` were written while the image was live but never seen changed at execution
(patched before every execution with the same value, or rewritten identically): treat them as potentially volatile.

## episode
A load episode is a burst of loader writes.  Per run: a writer is a **loader** when it was the last writer (before first
execution) of >= BULK non-volatile executed bytes, or of at least one such byte while being bulk.  (Bulk writers
that only overwrite dead code - e.g. a blitter drawing over a hi-res page where a loader once ran - are not
loaders.)  Each writer's store activity is kept as
frame intervals (gaps <= `episode_gap` merged); loader intervals that contain the last-write frame of at least one
executed byte attributed to that loader are united, and intervals closer than `episode_gap` frames form one burst.
An image member's **load frame** is the latest last-write frame (before the byte's first execution) of its
non-volatile executed bytes last written by a loader, else by any writer, considering first only writes up to the
member's `first_frame` (an identical rewrite of a live image does not move its load; only if no byte qualifies are
later writes used); -1 when no executed byte was ever stored to (RAM set up before the run).

| key | meaning |
|---|---|
| `id`, `run` | |
| `kind` | `burst` (load frame inside a burst), `single` (load frame outside every burst: code written by a non-loader), `initial` (load frame -1) |
| `frames` | [first, last] frame of the burst (`single`: [load frame, load frame]; `initial`: [-1, -1]) |
| `writers` | loader PCs of the burst (`single`: last writers at the load frame) |
| `images` | ids of images with a member loaded in this episode (an image merged across loads lists several episodes) |

Episodes without images are dropped.  Translation units (design 3.2) are the image sets of episodes; episodes of
different loads of the same overlay have the same image set.

## writer
`{"run", "pc", "changed_exec_bytes", "loaded_bytes", "bulk", "loader", "active"}`: distinct executed bytes this PC
changed (capped at 4096; also counts dead code overwritten as data), executed bytes it wrote last before their
first execution, `bulk` = changed_exec_bytes >= BULK (64 by default; decides image splits), `loader` (see episode), `active` = activity intervals (loaders only).  Listed: every PC with
changed_exec_bytes > 0 or loaded_bytes > 0.

## Limits
* Static descent beyond the profile can only use bytes of image pages (`bytes`); pages that never executed code have
  no bytes in the file.
* Episodes and load frames have frame resolution: loads closer than `episode_gap` frames are one episode.
* A store PC is identified by address only (an overlay's store at the same address as another's is one writer).
