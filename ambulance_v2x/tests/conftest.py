import sys
from pathlib import Path

# Ensure ambulance_v2x root directory is on sys.path for test discovery
PKG_ROOT = Path(__file__).resolve().parent.parent
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))
