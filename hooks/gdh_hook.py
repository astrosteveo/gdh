"""Runs gdh's Claude Code hooks (src/gdh/hooks.py) from the plugin's own copy of gdh, with no install needed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gdh.hooks import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
