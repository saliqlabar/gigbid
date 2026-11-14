"""Change request (policy 1.1): GigBid must absorb it from GigSim's policy alone. See evals/change_request/."""
import shutil

import pytest

from agent.nodes.compliance import check
from agent.nodes.pricing import quote
from agent.settings import ROOT


@pytest.fixture()
def policy_v11(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    shutil.copytree(ROOT / "gigsim" / "seed", seed)
    shutil.copy(ROOT / "evals" / "change_request" / "policy_v1_1.yaml", seed / "policy.yaml")
    import yaml
    return yaml.safe_load((seed / "policy.yaml").read_text())


def test_surcharge_reaches_connects_cost_and_expected_value(policy_v11):
    job = {"budget_type": "fixed", "budget_max": 500, "connects_cost": 6}
    q = quote(job, {"hourly_rate_usd": 30, "minimum_project_budget_usd": 100}, {}, {}, 0.8, policy_v11, 80)
    assert q.connects_cost == 8 and any("surcharge 2" in r for r in q.rationale)


def test_tighter_word_limit_blocks_long_letters(policy_v11):
    v = check(" ".join(["word"] * 320), policy_v11)
    assert not v.allowed and v.blocked_reason == "cover letter longer than 300 words"


def test_minimum_hourly_bid(policy_v11):
    job = {"budget_type": "hourly", "hourly_low": 5, "hourly_high": 8, "connects_cost": 4}
    q = quote(job, {"hourly_rate_usd": 6}, {}, {}, 0.8, policy_v11, 80)
    assert q.amount >= 10
