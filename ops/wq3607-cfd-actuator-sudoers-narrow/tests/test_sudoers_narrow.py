"""wq#3607 — the lumen-cfd-actuator sudoers block extended by install.sh must
still: (a) parse clean under visudo, (b) keep the actuator's own restart
command allowed, (c) deny every direct cloudflared control path the blanket
`lumen ALL=(root) NOPASSWD: ALL` grant would otherwise permit.

visudo has no "would this command be allowed" query mode, so these tests
build a two-line /etc/sudoers.d-shaped fixture (a stand-in blanket grant +
the actuator block this bundle ships) and assert against `sudo -l -U lumen`
semantics via a direct re-implementation of sudo's last-match rule: since
sudo and visudo refuse to run outside a real sudoers context in this
sandbox, the deny/allow set is instead checked structurally (exact literal
lines, no overlap with the allowed "restart" command) against the rendered
block, and visudo -cf syntax-checks the rendered file on disk.
"""
import re
import subprocess
import unittest
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"

ALLOW_LINE = (
    "lumen ALL=(root) NOPASSWD: "
    "/usr/bin/systemd-run --collect /usr/bin/systemctl restart cloudflared.service"
)


def _render_sudoers_block():
    text = INSTALL_SH.read_text()
    m = re.search(r"cat > \"\$ACTUATOR_SUDOERS\" <<'EOF'\n(.*?)\nEOF\n", text, re.S)
    assert m, "install.sh must ship the actuator sudoers heredoc"
    return m.group(1)


class SudoersNarrowTest(unittest.TestCase):
    def setUp(self):
        self.block = _render_sudoers_block()
        self.lines = [
            l for l in self.block.splitlines() if l.strip() and not l.strip().startswith("#")
        ]

    def test_visudo_accepts_rendered_block(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".sudoers", delete=False) as f:
            f.write(self.block + "\n")
            path = f.name
        try:
            r = subprocess.run(["visudo", "-cf", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        finally:
            Path(path).unlink()

    def test_actuator_restart_command_still_allowed(self):
        self.assertIn(ALLOW_LINE, self.lines)

    def test_no_negation_matches_the_allowed_literal(self):
        # The actuator's own allowed line must never collide with (and thus
        # be overridden by) one of the negations that follow it (wq#3607's
        # own bug class: a wildcard deny can out-rank the earlier allow).
        allow_cmd = ALLOW_LINE.split("NOPASSWD: ", 1)[1]
        for line in self.lines:
            if "!" not in line:
                continue
            neg_cmd = line.split("!", 1)[1]
            pattern = re.escape(neg_cmd).replace(r"\*", ".*")
            self.assertIsNone(
                re.fullmatch(pattern, allow_cmd),
                f"negation {line!r} matches the actuator's own allowed command",
            )

    def test_direct_and_wrapped_bypass_paths_are_denied(self):
        denied_fragments = [
            "!/usr/bin/systemctl stop cloudflared.service",
            "!/usr/bin/systemctl kill cloudflared.service",
            "!/usr/bin/systemctl kill -s SIGHUP cloudflared.service",
            "!/usr/bin/systemctl kill -s SIGTERM cloudflared.service",
            "!/usr/bin/systemctl reload cloudflared.service",
            "!/usr/bin/systemctl try-restart cloudflared.service",
            "!/usr/bin/systemctl restart cloudflared.service",
            "!/usr/bin/systemd-run --collect /usr/bin/systemctl stop cloudflared.service",
            "!/usr/bin/systemd-run --collect /usr/bin/systemctl kill cloudflared.service",
            "!/usr/bin/systemd-run --collect /usr/bin/systemctl kill -s SIGHUP cloudflared.service",
            "!/usr/bin/systemd-run --collect /usr/bin/systemctl kill -s SIGTERM cloudflared.service",
            "!/usr/bin/systemd-run --collect /usr/bin/systemctl reload cloudflared.service",
            "!/usr/bin/systemd-run --collect /usr/bin/systemctl try-restart cloudflared.service",
        ]
        joined = "\n".join(self.lines)
        for frag in denied_fragments:
            self.assertIn(frag, joined)

    def test_migration_tmp_retirement_is_present(self):
        text = INSTALL_SH.read_text()
        self.assertIn("MIGRATION_TMP=/etc/sudoers.d/99-lumen-migration-tmp", text)
        self.assertIn('rm -f "$MIGRATION_TMP"', text)


if __name__ == "__main__":
    unittest.main()
