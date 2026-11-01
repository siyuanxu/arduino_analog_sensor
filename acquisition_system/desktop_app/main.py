"""Direct-script and PyInstaller entry."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from desktop_app.startup import run
if __name__ == '__main__':
    raise SystemExit(run())
