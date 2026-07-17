from __future__ import annotations

from pathlib import Path

from aflow.fixtures.factory import write_fixtures


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
    names = write_fixtures(root)
    print(f"generated={len(names)}")
    for name in names:
        print(name)

