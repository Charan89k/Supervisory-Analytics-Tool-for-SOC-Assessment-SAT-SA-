"""
Trend ground truth must actually be ground truth.

Trend analysis is validated by seeding each entity a trajectory and
checking the reported direction against it. That only works if the
trajectory holds still across the periods of one submission.

It did not. `build_dataset` reseeds the global RNG with
`seed + period_index`, and entities beyond the first five drew their
profile from it — so an entity could be "weak" in Q3, "clean" in Q4 and
"typical" in Q1. Those entities were a random walk by construction, and
measuring trend direction against their nominal trajectory reported
mismatches that said nothing about the trend code.

The trends module was right to call them "mixed". The measurement was
wrong, not the algorithm.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATOR = os.path.join(ROOT, "data", "generator", "generate_dataset.py")

#: Beyond PROFILE_ORDER's five, so the randomly-assigned entities are
#: exercised — they are the ones that used to drift.
ENTITIES = 8
PERIODS = 3


@pytest.fixture(scope="module")
def multi_period_dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("trend-truth") / "submission"
    subprocess.run(
        [sys.executable, GENERATOR, "--socs", str(ENTITIES),
         "--alerts-per-soc", "40", "--periods", str(PERIODS),
         "--seed", "4242", "--out", str(out)],
        check=True, capture_output=True, cwd=ROOT)
    return out


def profiles_per_period(dataset):
    """{period: {soc_id: profile}} from each period's own labels."""
    periods = sorted(p for p in dataset.iterdir() if p.is_dir())
    return {p.name: json.loads((p / "ground_truth.json").read_text())["profiles"]
            for p in periods}


def test_the_dataset_really_has_several_periods(multi_period_dataset):
    assert len(profiles_per_period(multi_period_dataset)) == PERIODS


def test_every_entity_keeps_one_profile_across_all_periods(multi_period_dataset):
    """
    The property the whole trend validation rests on. Without it an
    entity has no trajectory to be right or wrong about.
    """
    by_period = profiles_per_period(multi_period_dataset)

    drifted = {}
    for soc_id in next(iter(by_period.values())):
        seen = {period: profiles[soc_id]
                for period, profiles in by_period.items()}
        if len(set(seen.values())) > 1:
            drifted[soc_id] = seen

    assert not drifted, f"profile changed between periods: {drifted}"


def test_entities_beyond_the_fixed_five_are_covered(multi_period_dataset):
    """
    The first five take their profile from PROFILE_ORDER by index and
    were always stable. The regression only ever affected the rest, so
    a dataset that does not contain any would pass vacuously.
    """
    profiles = next(iter(profiles_per_period(multi_period_dataset).values()))
    assert len(profiles) == ENTITIES > 5


def test_generation_is_reproducible_for_a_seed(tmp_path_factory):
    """Stable across periods, and still reproducible run to run."""
    outputs = []
    for run in range(2):
        out = tmp_path_factory.mktemp(f"repeat-{run}") / "submission"
        subprocess.run(
            [sys.executable, GENERATOR, "--socs", "7", "--alerts-per-soc", "40",
             "--periods", "2", "--seed", "99", "--out", str(out)],
            check=True, capture_output=True, cwd=ROOT)
        outputs.append(profiles_per_period(out))

    assert outputs[0] == outputs[1]
