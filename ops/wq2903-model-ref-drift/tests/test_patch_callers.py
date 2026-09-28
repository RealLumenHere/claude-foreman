"""patch_callers: exact-string, idempotent, never clobbers an unknown file (wq#2903)."""
import glob, importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "patch_callers", os.path.join(os.path.dirname(HERE), "patch_callers.py"))
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)


def _targets(tmp_path):
    out = []
    for i, (_, old, new) in enumerate(pc.PATCHES):
        p = tmp_path / f"t{i}.py"
        p.write_text("head\n" + old + "tail\n")
        out.append((str(p), old, new))
    return out


def test_check_apply_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "PATCHES", _targets(tmp_path))
    assert pc.main(["--check"]) == 1
    assert pc.main([]) == 0
    for p, old, new in pc.PATCHES:
        text = open(p).read()
        assert new in text and old not in text and "mistral-small\"" not in text
        assert len(glob.glob(p + ".bak.*_wq2903")) == 1
    assert pc.main(["--check"]) == 0
    assert pc.main([]) == 0                            # re-run: no second backup
    assert len(glob.glob(str(tmp_path / "*.bak.*"))) == len(pc.PATCHES)


def test_unknown_content_aborts_without_writing(tmp_path, monkeypatch):
    targets = _targets(tmp_path)
    open(targets[1][0], "w").write("someone rewrote this\n")
    monkeypatch.setattr(pc, "PATCHES", targets)
    assert pc.main([]) == 2
    assert targets[0][1] in open(targets[0][0]).read()  # first file untouched too


def test_live_targets_hold_the_old_text():
    """The shipped old strings match the live files byte-for-byte (pre-deploy) or the
    new strings do (post-deploy); anything else means the patch would abort."""
    for path, old, new in pc.PATCHES:
        if not os.access(path, os.R_OK):
            continue
        text = open(path).read()
        assert text.count(old) == 1 or new in text, path
