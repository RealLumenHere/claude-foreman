"""Test the daemon loop bounds its iterations and reconciles each pass,
without sleeping for real. (wq#3810 lifecycle step 2 periodic pickup.)"""
import daemon as d
from registry import Registry


def test_loop_runs_bounded_iterations(tmp_path):
    plugins = tmp_path / "plugins.d"
    state = tmp_path / "state"
    plugins.mkdir()
    (plugins / "ghidra.toml").write_text(
        '[plugin]\nname="ghidra"\nversion="11.2"\nkind="tool"\n')
    reg = Registry(plugins_dir=str(plugins), state_dir=str(state),
                   health_runner=lambda cmd, t: (0, "ok"), clock=lambda: 1.0)

    slept = []
    count = d.run(interval_seconds=5, iterations=3, reg=reg, sleep=slept.append)
    assert count == 3
    # final iteration does not sleep (loop exits first)
    assert slept == [5, 5]
    # unified.json exists after the run
    assert (state / "unified.json").exists()
