"""
Demonstration mode.

Runs the real SAT-SA pipeline over a built-in synthetic dataset. The
ONLY thing demonstration mode changes is where the dataset comes from —
ingestion, validation, normalisation, detection, scoring, benchmarking,
capability mapping, evidence, prioritisation and reporting are the same
code paths a real submission takes. Nothing is staged, cached as a
screenshot, or precomputed.

That matters beyond honesty: a demonstration that took a different path
from the product would not be evidence that the product works.

Dataset shape, chosen by measurement rather than preference
----------------------------------------------------------
9 entities x 350 alerts, single period. Measured alternatives:

    9 x 350, 1 period    3.6s   14/14 rules   3/3 peer groups
    9 x 400, 2 periods   6.3s   12/14 rules   3/3
    9 x 250, 3 periods   7.0s   12/14 rules   3/3
   12 x 300, 1 period    4.0s   14/14 rules   4/4

Multi-period costs roughly twice the time AND fires fewer rules, because
spreading a fixed alert budget across periods drops per-period volume
below what the entity-level rules need. Two periods is worse still: the
trend-consistency check needs more than one step to mean anything, so a
two-period run reports a direction for every entity whether or not one
exists.

Fifteen entities is the smallest number giving three sector peer groups
of five, and the sector-relative rules need five.

A sample z-score is bounded at (n-1)/sqrt(n). At three peers the bound
is 1.155, so LOW_ACTIVITY_OUTLIER's -1.5 threshold is unreachable and
the rule cannot fire at all. At four the bound is exactly 1.5, so it
fires only when every peer reports an identical volume — which is true
of generated data and of nothing else: 5% variation between peers drops
detection from 100% to 0%. At five the bound is 1.789 and detection
survives 30% variation intact.

A demonstration that silently cannot exercise a rule, or exercises it
only by coincidence, is worse than one that omits it.

Trend analysis is therefore NOT part of the demonstration dataset. It is
exercised by generating a multi-period submission separately, which the
application ingests the same way.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

#: Fixed so a demonstration is reproducible: the same entities, the same
#: findings, the same ranking, every time it is shown.
DEMO_SEED = 20261
DEMO_ENTITIES = 15
DEMO_ALERTS_PER_ENTITY = 350

DEMO_DIRNAME = "demo"

#: Shown wherever demonstration data is loaded. A supervisor, or a judge,
#: must never be able to mistake it for a real entity's submission.
DEMO_BANNER = (
    "DEMONSTRATION DATA — synthetic records generated for this "
    "demonstration. Not a real Critical Sector Entity, and not derived "
    "from any real submission."
)

DEMO_LABEL = "Demonstration dataset (synthetic)"


@dataclass
class DemoDataset:
    path: Path
    generated: bool
    entities: int = DEMO_ENTITIES
    alerts_per_entity: int = DEMO_ALERTS_PER_ENTITY

    @property
    def label(self) -> str:
        return DEMO_LABEL


class DemoService:
    """Prepares the built-in demonstration dataset."""

    def __init__(self, project_root: Optional[Path] = None):
        self.project_root = (Path(project_root) if project_root
                              else Path(__file__).resolve().parents[2])
        self.generator = (self.project_root / "data" / "generator"
                          / "generate_dataset.py")

    @property
    def dataset_path(self) -> Path:
        return self.project_root / "data" / DEMO_DIRNAME

    def is_ready(self) -> bool:
        """
        Whether a usable dataset for the CURRENT configuration exists.

        Checking only that a file is present is not enough. The dataset
        is cached between runs, so after the entity count changes — as
        it did when the peer groups had to grow for the sector-relative
        rules — a stale folder from an earlier version would be reused
        silently, and the demonstration would quietly stop exercising
        the rules it was enlarged to exercise. Worse on an upgraded
        install than here, because nobody would think to look.
        """
        alerts = self.dataset_path / "alerts.csv"
        socs = self.dataset_path / "socs.csv"
        if not alerts.is_file() or not socs.is_file():
            return False

        try:
            with open(socs, encoding="utf-8") as handle:
                entities = sum(1 for _ in handle) - 1   # minus the header
        except OSError:
            return False

        return entities == DEMO_ENTITIES

    def prepare(self,
                 progress: Optional[Callable[[str], None]] = None,
                 force: bool = False) -> DemoDataset:
        """
        Ensure the demonstration dataset exists, generating it if not.

        Generated on first use rather than committed, so a fresh clone
        needs no extra step and the repository carries no bundled data.
        The seed is fixed, so what is generated on a judge's machine is
        identical to what was generated here.

        Raises RuntimeError with a readable message if generation fails;
        the caller reports it rather than leaving a half-written folder
        to be mistaken for a dataset.
        """
        def say(message: str) -> None:
            if progress:
                progress(message)

        if self.is_ready() and not force:
            say("Demonstration dataset ready.")
            return DemoDataset(path=self.dataset_path, generated=False)

        if not self.generator.is_file():
            raise RuntimeError(
                f"The dataset generator is missing:\n{self.generator}\n\n"
                "Demonstration mode builds its dataset from the generator "
                "shipped with the application.")

        say(f"Generating {DEMO_ENTITIES} synthetic entities "
            f"({DEMO_ENTITIES * DEMO_ALERTS_PER_ENTITY:,} alerts)…")

        command = [
            sys.executable, str(self.generator),
            "--socs", str(DEMO_ENTITIES),
            "--alerts-per-soc", str(DEMO_ALERTS_PER_ENTITY),
            "--seed", str(DEMO_SEED),
            "--out", str(self.dataset_path),
        ]

        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                     timeout=300)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                "Generating the demonstration dataset timed out.")
        except OSError as exc:
            raise RuntimeError(
                f"The demonstration dataset could not be generated: {exc}")

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(
                "The demonstration dataset could not be generated.\n\n"
                + detail[-600:])

        if not self.is_ready():
            raise RuntimeError(
                "The demonstration dataset generator reported success but "
                f"wrote no alerts.csv to {self.dataset_path}.")

        say("Demonstration dataset generated.")
        return DemoDataset(path=self.dataset_path, generated=True)
