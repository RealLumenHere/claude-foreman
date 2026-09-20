"""wq#2242: a lane declaring `multi_repo: true` is satisfied by the ticket's own repo (needs
repo(...)) instead of a LANE_REPO row (gates.LaneRepo); an undeclared unmapped lane still parks."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gates_replay import (  # noqa: E402,F401
    UNMAPPED, _cfg, _decide, denv, fd, router, runaway, spawn_loop_guard, world)

CLONE = "/home/lumen/simpill/simpill-utils"


def _multi(cfg, lane):
    cfg["lanes"][lane]["multi_repo"] = True
    return cfg


def test_multi_repo_lane_dispatches_on_the_tickets_own_repo(world, monkeypatch):
    monkeypatch.setattr(router, "repo_for_ticket", lambda t: CLONE)
    world.add(1, "simpill", [])
    ready, skips = _decide(world, _multi(_cfg(["simpill"]), "simpill"), 1, "simpill")
    assert skips == [] and ready["repo"] == CLONE


def test_multi_repo_lane_ticket_with_no_resolvable_repo_still_parks(world):
    assert "simpill" in UNMAPPED
    world.add(1, "simpill", [])
    ready, skips = _decide(world, _multi(_cfg(["simpill"]), "simpill"), 1, "simpill")
    assert ready is None and [s["reason"] for s in skips] == ["lane_unmapped"]


def test_unmapped_lane_without_the_declaration_parks_before_reading_the_ticket(world, monkeypatch):
    monkeypatch.setattr(router, "repo_for_ticket", lambda t: CLONE)
    world.add(1, "simpill", [])
    ready, skips = _decide(world, _cfg(["simpill"]), 1, "simpill")
    assert ready is None and [s["reason"] for s in skips] == ["lane_unmapped"]
