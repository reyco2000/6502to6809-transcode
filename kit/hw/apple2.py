"""Apple II hardware map: every soft switch / I/O address and the firmware entry points games call, with the
CoCo 3 action for each (see kit/hw/__init__.py).  The generator, the survey, the builds and the docs all read
this table.

    python3 -m kit.hw.apple2 --doc        the markdown tables for docs/NATIVES.md and docs/machines/apple2.md
"""
from . import Hw, Runtime, Latch, Ignore, Trap, IdiomOnly, lookup_in

APPLE_TO_COCO = 1.75        # CoCo cycles per Apple cycle (1.79 MHz / 1.02 MHz)
TIMING_TOL = 0.02           # timed idioms: allowed relative timing error
DELAY_MIN = 500             # Apple cycles: shorter counted loops are not delay candidates

R, W, RX, WX = 'rd', 'wr', 'rdx', 'wrx'
RW = frozenset({R, W})
F = frozenset

SLOT = Trap('slot I/O (Disk II): loaders are dropped, use snapshots + nt_load')

MAP = [
    Hw(0xC000, 0xC00F, 'KBD (keyboard data)', 'keyboard', Latch('kb_kbd'), F({R})),
    Hw(0xC000, 0xC00F, 'IIe 80STORE/RAMRD/RAMWRT/ALTZP/80COL/ALTCHAR switches', 'memory',
       Trap('IIe 80STORE/RAMRD/RAMWRT/ALTZP switches: aux memory not supported'), F({W})),
    Hw(0xC010, 0xC010, 'KBDSTRB (clear strobe; read: any key down)', 'keyboard', Runtime()),
    Hw(0xC011, 0xC018, 'IIe status: language card / aux memory / 80STORE', 'memory',
       Trap('language card / aux status'), F({R})),
    Hw(0xC011, 0xC01F, 'KBDSTRB alias', 'keyboard',
       Trap('keyboard strobe alias: write $C010, or map it with HW_OVERRIDES'), F({W})),
    Hw(0xC019, 0xC019, 'RDVBL (IIe vertical blank, bit 7 = 0 in blank)', 'vbl', Runtime(cond='HW_IOV'), F({R}),
       doc='a single read is a handler; a polling loop is the vbl_poll idiom'),
    Hw(0xC01A, 0xC01D, 'RDTEXT / RDMIXED / RDPAGE2 / RDHIRES', 'video', Runtime(cond='HW_IOV'), F({R})),
    Hw(0xC01E, 0xC01F, 'RDALTCHAR / RD80COL', 'video', Ignore(0), F({R})),
    Hw(0xC020, 0xC02F, 'cassette out', 'sound', Ignore(0)),
    Hw(0xC030, 0xC030, 'SPKR (speaker toggle)', 'sound', Runtime(),
       doc='single clicks; a cycle-timed loop is the tone idiom'),
    Hw(0xC031, 0xC03F, 'SPKR alias', 'sound',
       Trap('speaker alias: access $C030, or map it with HW_OVERRIDES')),
    Hw(0xC040, 0xC04F, 'utility strobe / unused', 'other', Ignore(0)),
    Hw(0xC050, 0xC053, 'TXTCLR / TXTSET / MIXCLR / MIXSET', 'video', Runtime(cond='HW_IOV'),
       doc='tracked in v_mode; only full-screen hi-res is shown'),
    Hw(0xC054, 0xC055, 'PAGE1 / PAGE2', 'video', Runtime(), doc='selects and converts the page'),
    Hw(0xC056, 0xC057, 'LORES / HIRES', 'video', Runtime(cond='HW_IOV'), doc='tracked in v_mode'),
    Hw(0xC058, 0xC05D, 'annunciators AN0-AN2', 'video', Ignore(0)),
    Hw(0xC05E, 0xC05F, 'AN3 (IIe double hi-res)', 'video', Trap('double hi-res (AN3) not supported')),
    Hw(0xC060, 0xC060, 'cassette in', 'joystick', Ignore(0), F({R})),
    Hw(0xC061, 0xC061, 'button 0', 'joystick', Latch('btn0'), F({R})),
    Hw(0xC062, 0xC062, 'button 1', 'joystick', Latch('btn1'), F({R})),
    Hw(0xC061, 0xC061, 'button 0+X', 'joystick', Runtime(), F({RX})),
    Hw(0xC063, 0xC063, 'button 2 (shift-key mod)', 'joystick', Ignore(0), F({R})),
    Hw(0xC064, 0xC067, 'paddle timers', 'joystick', IdiomOnly(('paddle_count', 'rom_pread')), F({R, RX})),
    Hw(0xC068, 0xC06F, 'mirror of $C060-$C067', 'joystick',
       Trap('mirror of $C060-$C067: access the base address'), F({R})),
    Hw(0xC060, 0xC06F, 'input switches (write: no effect)', 'joystick', Ignore(0), F({W})),
    Hw(0xC070, 0xC070, 'PTRIG (paddle trigger)', 'joystick', IdiomOnly(('paddle_count', 'rom_pread'))),
    Hw(0xC071, 0xC07F, 'IIe IOU / paddle-trigger alias', 'joystick',
       Trap('IIe IOU switch or paddle-trigger alias: not supported')),
    Hw(0xC080, 0xC08F, 'language card banking', 'memory', Trap('language card: see docs/machines/apple2.md')),
    Hw(0xC080, 0xC0FF, 'slot I/O ($C08x+slot*16; Disk II = $C0E0-$C0EF)', 'disk', SLOT, F({RX, WX})),
    Hw(0xC090, 0xC0FF, 'slot I/O (Disk II = $C0E0-$C0EF in slot 6)', 'disk', SLOT),
    Hw(0xC100, 0xCFFF, 'slot ROM / expansion ROM', 'rom', Trap('slot / expansion ROM')),
]


def _rom(a, name, cat, action):
    return Hw(a, a, name, cat, action, F({'call'}))


ROM = {e.lo: e for e in [
    _rom(0xFCA8, 'WAIT', 'timing', IdiomOnly(('rom_wait',))),
    _rom(0xFB1E, 'PREAD', 'joystick', IdiomOnly(('rom_pread',))),
    _rom(0xFF3A, 'BELL', 'sound', IdiomOnly(('rom_bell',))),
    _rom(0xFBDD, 'BELL1', 'sound', IdiomOnly(('rom_bell',))),
    _rom(0xFC58, 'HOME', 'text', Trap('text output: text page not displayed')),
    _rom(0xFDED, 'COUT', 'text', Trap('text output: text page not displayed')),
    _rom(0xFDF0, 'COUT1', 'text', Trap('text output: text page not displayed')),
    _rom(0xFD0C, 'RDKEY', 'keyboard', Trap('RDKEY: write a native keyboard wait')),
    _rom(0xFD1B, 'KEYIN', 'keyboard', Trap('RDKEY: write a native keyboard wait')),
    _rom(0xFE89, 'SETKBD', 'init', Ignore()),
    _rom(0xFE93, 'SETVID', 'init', Ignore()),
    _rom(0xFB2F, 'INIT', 'init', Ignore()),
    _rom(0xF3E2, 'HGR', 'video', Trap('HGR clear: write a fill native')),
    _rom(0xF3D8, 'HGR2', 'video', Trap('HGR clear: write a fill native')),
    _rom(0xC600, 'Disk II boot', 'disk', Trap('Disk II: native loader')),
    _rom(0xC65C, 'Disk II read sector', 'disk', Trap('Disk II: native loader')),
]}


def lookup(addr, mode, overrides=None):
    """the map entry for an access to addr in mode rd / wr / rdx / wrx (overrides: spec HW_OVERRIDES)"""
    return lookup_in(MAP, addr, mode, overrides)


def rom(addr):
    """the firmware entry point at addr, or None"""
    return ROM.get(addr)
