"""Kit locations (the repo root, the 6809 runtime sources, the unit-test simulator)."""
import os

KIT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(KIT)
RT = os.path.join(REPO, 'rt')
SIM = os.path.join(REPO, 'sim', 'cc3sim')
