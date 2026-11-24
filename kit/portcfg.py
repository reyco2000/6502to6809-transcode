"""Per-port configuration: <port dir>/port.toml.

[port]
name     = "mygame"
disk     = "~/path/game.dsk"          # the user's Apple disk image (never copied into the kit)
rom      = "~/path/APPLE2E.ROM"       # Apple ROM (last 16K = $C000-$FFFF)
prom     = "~/path/341-0027-a.p5"     # Disk II boot PROM
build    = "build"                    # everything derived from the user's disk goes here (never committed)
coverage = "build/coverage.json"          # host-Apple coverage profile (kit/host/profile.py --coverage)
ram      = 512                         # default CoCo 3 target: 512 or 128
loader   = "GAME"                      # disk file names <loader>.BIN / <loader>.DAT;  LOADM"<loader>":EXEC

Paths may use ~; relative paths are relative to the directory holding port.toml.  Derived dirs: <build>/snap, <build>/t512, <build>/t128.
"""
import os
import tomllib



class Port:
    def __init__(self, path):
        self.file = os.path.abspath(path)
        self.dir = os.path.dirname(self.file)
        d = tomllib.load(open(self.file, 'rb'))['port']
        self.raw = d
        self.name = d['name']
        self.ram = int(d.get('ram', 512))
        self.loader = d.get('loader', 'GAME').upper()
        assert 1 <= len(self.loader) <= 8 and self.loader.isalnum(), 'loader: 1-8 letters/digits'
        for k in ('disk', 'rom', 'prom', 'build', 'coverage'):
            v = d.get(k)
            setattr(self, k, self._abs(v) if v else None)
        self.snap = os.path.join(self.build, 'snap')

    def _abs(self, p):
        p = os.path.expanduser(p)
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(self.dir, p))

    def out(self, ram=None):
        """build dir of one target"""
        return os.path.join(self.build, f't{ram or self.ram}')

    def dsk(self, ram=None, suffix=''):
        return os.path.join(self.build, f'{self.name}_{ram or self.ram}k{suffix}.dsk')


def load(port_dir):
    return Port(os.path.join(port_dir, 'port.toml'))
