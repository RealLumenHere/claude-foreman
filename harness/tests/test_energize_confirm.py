"""wq#4747 / wq#5216 — energize_confirm.py: operator-issued confirm tokens for
hardware MCP tools (hal-spec.md §9.1/§9.2a).

The property under test is that an agent session cannot mint or forge its own
confirm token: minting needs Frank's passphrase (a pty from script(1) is not
enough — wq#5216), and verify_token rejects any record the passphrase-derived
Ed25519 key did not sign (a hand-written token file is not enough). A minted
token is capability-scoped, time-limited and single-use."""
import json
import os
import select
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import energize_confirm as ec  # noqa: E402
import ec_testkit as kit  # noqa: E402

EC_PATH = os.path.join(kit.HARNESS, "energize_confirm.py")


@pytest.fixture
def ecdirs(tmp_path, monkeypatch):
    return kit.install(ec, tmp_path, monkeypatch)


def _token_files(token_dir):
    return sorted(os.listdir(token_dir)) if os.path.isdir(token_dir) else []


# ---------------- Ed25519: pinned RFC 8032 §7.1 vectors ----------------

RFC8032_VECTORS = [  # (secret, public, message, signature) — TEST 1, 2, 3
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


@pytest.mark.parametrize("secret,public,msg,sig", RFC8032_VECTORS)
def test_ed25519_matches_rfc8032_vectors(secret, public, msg, sig):
    s, m = bytes.fromhex(secret), bytes.fromhex(msg)
    assert ec.ed25519_public(s).hex() == public
    assert ec.ed25519_sign(s, m).hex() == sig
    assert ec.ed25519_verify(bytes.fromhex(public), m, bytes.fromhex(sig)) is True


@pytest.mark.parametrize("secret,public,msg,sig", RFC8032_VECTORS)
def test_ed25519_rejects_tampering(secret, public, msg, sig):
    pub, m, sg = bytes.fromhex(public), bytes.fromhex(msg), bytearray(bytes.fromhex(sig))
    assert ec.ed25519_verify(pub, m + b"x", bytes(sg)) is False
    flipped = bytearray(sg)
    flipped[40] ^= 1
    assert ec.ed25519_verify(pub, m, bytes(flipped)) is False
    other_pub = bytes.fromhex(RFC8032_VECTORS[0][1] if public != RFC8032_VECTORS[0][1] else RFC8032_VECTORS[1][1])
    assert ec.ed25519_verify(other_pub, m, bytes(sg)) is False
    assert ec.ed25519_verify(pub, m, bytes(sg[:63])) is False


def test_ed25519_interoperates_with_cryptography():
    ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    from cryptography.hazmat.primitives import serialization
    seed, msg = os.urandom(32), os.urandom(77)
    theirs = ed.Ed25519PrivateKey.from_private_bytes(seed)
    their_pub = theirs.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    assert ec.ed25519_public(seed) == their_pub
    theirs.public_key().verify(ec.ed25519_sign(seed, msg), msg)  # raises on mismatch
    assert ec.ed25519_verify(their_pub, msg, theirs.sign(msg)) is True


# ---------------- minting needs the passphrase ----------------

def test_issue_refuses_without_a_verifier(tmp_path, monkeypatch):
    token_dir, _ = kit.install(ec, tmp_path, monkeypatch, with_verifier=False)
    with pytest.raises(ec.EnergizeConfirmError, match="set-passphrase"):
        kit.mint(ec, "openocd.write_target_memory")
    assert _token_files(token_dir) == []


def test_issue_refuses_a_wrong_passphrase(ecdirs):
    token_dir, _ = ecdirs
    with pytest.raises(ec.EnergizeConfirmError, match="wrong passphrase"):
        kit.mint(ec, "openocd.write_target_memory", passphrase=kit.WRONG)
    assert _token_files(token_dir) == []


def test_issue_with_the_passphrase_round_trips(ecdirs):
    token = kit.mint(ec, "openocd.write_target_memory")
    assert token and isinstance(token, str)
    assert ec.verify_token(token, "openocd.write_target_memory") is True


def test_issue_caps_ttl(ecdirs):
    with pytest.raises(ec.EnergizeConfirmError, match="capped"):
        kit.mint(ec, "flashrom.write_flash", ttl_seconds=ec.MAX_TTL_SECONDS + 1)


def test_verifier_stores_no_signing_secret(ecdirs):
    _, verifier = ecdirs
    rec = json.load(open(verifier))
    assert set(rec) == {"v", "kdf", "sig", "n", "r", "p", "salt", "pub", "set_at"}
    assert oct(os.stat(verifier).st_mode & 0o777) == "0o600"


# ---------------- token scope, expiry, single use ----------------

def test_verify_token_rejects_a_different_capability(ecdirs):
    token = kit.mint(ec, "flashrom.write_flash")
    assert ec.verify_token(token, "flashrom.erase_chip") is False


def test_verify_token_rejects_empty_or_unknown_token(ecdirs):
    assert ec.verify_token("", "flashrom.write_flash") is False
    assert ec.verify_token("not-a-real-token", "flashrom.write_flash") is False


def test_verify_token_is_single_use(ecdirs):
    token = kit.mint(ec, "flashrom.erase_chip")
    assert ec.verify_token(token, "flashrom.erase_chip") is True
    assert ec.verify_token(token, "flashrom.erase_chip") is False  # consumed


def test_verify_token_rejects_an_expired_token(ecdirs):
    token = kit.mint(ec, "flashrom.erase_chip", ttl_seconds=-1)
    assert ec.verify_token(token, "flashrom.erase_chip") is False


# ---------------- forged / hand-written token files (wq#5216) ----------------

def _write_record(token_dir, token, record):
    os.makedirs(token_dir, exist_ok=True)
    with open(os.path.join(token_dir, ec._token_hash(token) + ".json"), "w") as f:
        json.dump(record, f)


def test_hand_written_pre_fix_record_is_rejected(ecdirs):
    # The exact record shape the pre-wq#5216 issue_token wrote, unsigned.
    token_dir, _ = ecdirs
    token = "a" * 64
    _write_record(token_dir, token, {"capability": "serial.write", "expires_at": 4102444800.0})
    assert ec.verify_token(token, "serial.write") is False


def test_record_signed_by_another_key_is_rejected(ecdirs):
    token_dir, _ = ecdirs
    token, exp = "b" * 64, 4102444800
    attacker_seed = os.urandom(32)
    sig = ec.ed25519_sign(attacker_seed, ec._message(ec._token_hash(token), "serial.write", exp)).hex()
    _write_record(token_dir, token, {"v": 2, "capability": "serial.write", "expires_at": exp, "sig": sig})
    assert ec.verify_token(token, "serial.write") is False


def test_edited_capability_or_expiry_is_rejected(ecdirs):
    token_dir, _ = ecdirs
    for field, value, cap in (("capability", "flashrom.erase_chip", "flashrom.erase_chip"),
                              ("expires_at", 4102444800, "serial.read")):
        token = kit.mint(ec, "serial.read")
        path = ec._token_path(token)
        rec = json.load(open(path))
        rec[field] = value
        json.dump(rec, open(path, "w"))
        assert ec.verify_token(token, cap) is False, field


def test_valid_record_renamed_to_another_token_is_rejected(ecdirs):
    token = kit.mint(ec, "serial.write")
    other = "c" * 64
    os.rename(ec._token_path(token), ec._token_path(other))
    assert ec.verify_token(other, "serial.write") is False


def test_rotation_needs_the_current_passphrase_and_kills_outstanding_tokens(ecdirs):
    token = kit.mint(ec, "serial.write")
    with pytest.raises(ec.EnergizeConfirmError, match="wrong passphrase"):
        ec.set_passphrase(_getpass=lambda _p: kit.WRONG)
    answers = iter([kit.PASSPHRASE, "rotated passphrase xyz", "rotated passphrase xyz"])
    assert ec.set_passphrase(_getpass=lambda _p: next(answers)) == "rotated"
    assert ec.verify_token(token, "serial.write") is False


def test_group_writable_verifier_fails_closed(ecdirs):
    _, verifier = ecdirs
    token = kit.mint(ec, "serial.write")
    os.chmod(verifier, 0o620)
    assert ec.verify_token(token, "serial.write") is False


def test_missing_verifier_fails_closed(ecdirs):
    _, verifier = ecdirs
    token = kit.mint(ec, "serial.write")
    os.remove(verifier)
    assert ec.verify_token(token, "serial.write") is False


def test_no_environment_variable_redirects_the_dirs(tmp_path):
    env = dict(os.environ, ENERGIZE_CONFIRM_DIR=str(tmp_path / "x"), HOME=str(tmp_path / "home"))
    out = subprocess.run([sys.executable, "-c",
                          "import energize_confirm as ec; print(ec.TOKEN_DIR); print(ec.VERIFIER)"],
                         cwd=kit.HARNESS, env=env, capture_output=True, text=True, timeout=30)
    assert out.stdout.split() == ["/home/lumen/re/.energize-confirm",
                                  "/home/lumen/.config/energize-confirm/verifier.json"], out.stderr
    assert "os.environ" not in open(EC_PATH).read()


# ---------------- CLI ----------------

def test_cli_issue_refuses_outside_a_terminal(ecdirs, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert ec._main(["issue", "openocd.write_target_memory"]) == 1


def test_cli_verify_round_trip(ecdirs):
    token = kit.mint(ec, "serial.write")
    assert ec._main(["verify", "serial.write", token]) == 0
    assert ec._main(["verify", "serial.write", "bogus"]) == 1


# ---------------- script(1): the wq#5216 bypass ----------------

needs_script = pytest.mark.skipif(shutil.which("script") is None, reason="util-linux script(1) not installed")


def _script(cmd, keystrokes, prompt=b"passphrase to authorize", timeout=30):
    """Run `cmd` under script(1) the way an agent shell would: a fresh pty, with
    `keystrokes` typed into it once `prompt` shows (getpass flushes typeahead
    with TCSAFLUSH, so text sent earlier never reaches it). Returns
    (returncode, output); returncode is None if it hung and was killed, which
    for an agent is a Bash timeout with nothing minted."""
    p = subprocess.Popen(["script", "-qec", cmd, "/dev/null"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    fd, buf, deadline = p.stdout.fileno(), b"", time.monotonic() + timeout
    sent = False
    while time.monotonic() < deadline:
        if not sent and (prompt is None or prompt in buf):
            if keystrokes:
                p.stdin.write(keystrokes.encode())
                p.stdin.flush()
            time.sleep(0.3)
            p.stdin.close()
            sent = True
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            buf += chunk
        elif p.poll() is not None and not r:
            break
    rc = p.poll()
    if rc is None:
        p.kill()
        p.wait()
    return rc, buf.decode(errors="replace")


def _printed_token(out):
    return [w for w in out.split() if len(w) == 64 and all(c in "0123456789abcdef" for c in w)]


@needs_script
def test_script1_really_hands_the_child_a_tty():
    # Premise of the bypass: under script(1) isatty() is True, so the old check passed.
    # If this ever goes False the tests below stop proving anything.
    rc, out = _script(f"{sys.executable} -c 'import sys; print(sys.stdin.isatty())'", "", prompt=None)
    assert "True" in out, out


@needs_script
@pytest.mark.parametrize("keystrokes", ["", "\n", kit.WRONG + "\n"])
def test_script1_mint_without_the_passphrase_fails(tmp_path, monkeypatch, keystrokes):
    token_dir, verifier = kit.install(ec, tmp_path, monkeypatch)
    w = kit.wrapper(tmp_path, token_dir, verifier, "ec")
    rc, out = _script(f"{sys.executable} {w} issue serial.write", keystrokes, timeout=15)
    assert "passphrase to authorize" in out, out  # the pty did reach the prompt
    assert rc != 0, out
    assert _printed_token(out) == [], out
    assert _token_files(token_dir) == []


@needs_script
def test_script1_mint_of_the_ticket_command_fails():
    # The exact command from the ticket against the real module and its fixed
    # verifier path: it must refuse (no verifier, or a wrong passphrase if Frank
    # has set one) and never print a token.
    rc, out = _script(f"{sys.executable} {EC_PATH} issue serial.write", "\n", prompt=None, timeout=15)
    assert rc != 0, out
    assert _printed_token(out) == [], out


@needs_script
def test_script1_mint_with_the_passphrase_works_positive_control(tmp_path, monkeypatch):
    # Positive control: the same pty path DOES mint when the human types the
    # passphrase, so the failures above are the passphrase, not a broken harness.
    token_dir, verifier = kit.install(ec, tmp_path, monkeypatch)
    w = kit.wrapper(tmp_path, token_dir, verifier, "ec")
    rc, out = _script(f"{sys.executable} {w} issue serial.write", kit.PASSPHRASE + "\n")
    assert rc == 0, out
    token = _printed_token(out)[-1]
    assert ec.verify_token(token, "serial.write") is True
