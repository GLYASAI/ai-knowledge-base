"""pytest 根目录 conftest — 确保项目根在 sys.path 中。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
