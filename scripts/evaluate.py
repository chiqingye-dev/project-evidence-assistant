"""Run the fixed development benchmark without bespoke trial entry points."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from project_evidence.evaluation import main


if __name__ == "__main__":
    main()
