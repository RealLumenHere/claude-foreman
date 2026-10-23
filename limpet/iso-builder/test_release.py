import hashlib

import release


def _bencode(value):
    """Minimal bencode encoder for test fixtures (sorted dict keys, like
    every real torrent writer emits)."""
    if isinstance(value, int):
        return f"i{value}e".encode()
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, str):
        return _bencode(value.encode())
    if isinstance(value, list):
        return b"l" + b"".join(_bencode(v) for v in value) + b"e"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: kv[0] if isinstance(kv[0], bytes) else kv[0].encode())
        body = b"".join(_bencode(k) + _bencode(v) for k, v in items)
        return b"d" + body + b"e"
    raise TypeError(type(value))


def _make_torrent_bytes(info: dict, announce: str = "udp://tracker.example:80/announce") -> bytes:
    return _bencode({"announce": announce, "info": info})


def test_info_hash_matches_direct_sha1_of_info_value():
    info = {"name": "limpet.iso", "piece length": 262144, "pieces": b"\x00" * 20, "length": 123}
    torrent_bytes = _make_torrent_bytes(info)
    expected = hashlib.sha1(_bencode(info)).hexdigest()
    assert release.info_hash(torrent_bytes) == expected


def test_info_hash_ignores_sibling_keys_outside_info():
    info = {"name": "a", "length": 1}
    narrow = _make_torrent_bytes(info, announce="udp://x:1/announce")
    wide = _make_torrent_bytes(info, announce="udp://a-totally-different-host:9999/announce")
    assert release.info_hash(narrow) == release.info_hash(wide)


def test_info_hash_requires_info_key():
    torrent_bytes = _bencode({"announce": "udp://x:1/announce"})
    try:
        release.info_hash(torrent_bytes)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_magnet_link_contains_btih_name_and_every_tracker():
    link = release.magnet_link("deadbeef" * 5, "limpet-1.0.iso", [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://tracker.example:80/announce",
    ])
    assert link.startswith("magnet:?xt=urn:btih:" + "deadbeef" * 5)
    assert "dn=limpet-1.0.iso" in link
    assert link.count("&tr=") == 2
    assert "tracker.opentrackr.org" in link


def test_magnet_link_url_encodes_display_name():
    link = release.magnet_link("ab" * 20, "limpet 1.0 (amd64).iso", [])
    assert "limpet%201.0%20%28amd64%29.iso" in link


def test_transmission_create_cmd_adds_dash_t_per_tracker_and_no_private_flag():
    cmd = release.transmission_create_cmd(
        "/out/limpet.iso", "/out/limpet.iso.torrent",
        ["udp://a:1/announce", "udp://b:2/announce"], "Limpet ISO",
    )
    assert cmd[0] == "transmission-create"
    assert cmd.count("-t") == 2
    assert "-p" not in cmd and "--private" not in cmd  # DHT/PEX stay enabled
    assert cmd[-1] == "/out/limpet.iso"


def test_apt_install_cmd_uses_sudo_dash_n_not_bare_sudo():
    cmd = release.apt_install_cmd()
    assert cmd[:2] == ["sudo", "-n"]
    assert "transmission-cli" in cmd


def test_seed_unit_cmd_is_a_detached_systemd_run_transient_unit():
    cmd = release.seed_unit_cmd("limpet-seed-x", "/out", "/out/x.torrent")
    assert cmd[:3] == ["sudo", "-n", "systemd-run"]
    assert "--unit=limpet-seed-x" in cmd
    assert "--collect" in cmd
    assert cmd[-2:] == ["-w", "/out"] or "/out/x.torrent" in cmd


def test_gpg_sign_cmd_targets_requested_key_and_detach_sign():
    cmd = release.gpg_sign_cmd("ABCDEF0123456789", "SHA256SUMS", "SHA256SUMS.gpg")
    assert "--detach-sign" in cmd
    assert "--local-user" in cmd
    assert "ABCDEF0123456789" in cmd
    assert cmd[-1] == "SHA256SUMS"
    assert "SHA256SUMS.gpg" in cmd


def test_release_notes_includes_magnet_checksums_and_gh_release_command():
    notes = release.release_notes(
        "1.0", "limpet-1.0-amd64.iso", "magnet:?xt=urn:btih:deadbeef",
        release.TRACKERS_DEFAULT, "ABCDEF0123456789",
    )
    assert "magnet:?xt=urn:btih:deadbeef" in notes
    assert "SHA256SUMS.gpg" in notes
    assert "ABCDEF0123456789" in notes
    assert "gh release create" in notes
    assert "limpet-1.0-amd64.iso.torrent" in notes


def test_cli_magnet_subcommand(tmp_path, capsys):
    info = {"name": "limpet.iso", "length": 1}
    torrent = tmp_path / "limpet.iso.torrent"
    torrent.write_bytes(_make_torrent_bytes(info))
    rc = release.main(["magnet", "--torrent", str(torrent), "--name", "limpet.iso"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("magnet:?xt=urn:btih:")
    assert "dn=limpet.iso" in out
