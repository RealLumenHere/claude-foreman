"""Forgery attempts against the rev-pinned fixed module, with full read access to the
probe verifier and one genuine (Frank-signed) record. Every line must print False."""
import hashlib, importlib.util, json, os, secrets, shutil, time

W = "/tmp/fworker-wq5235/probe/w1"
spec = importlib.util.spec_from_file_location("ec_fixed", "/tmp/fworker-wq5235/probe/ec_fixed.py")
ec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ec)
ec.TOKEN_DIR = os.path.join(W, "tokens")
ec.VERIFIER = os.path.join(W, "cfg", "verifier.json")
v = json.load(open(ec.VERIFIER))
genuine = [f for f in os.listdir(ec.TOKEN_DIR) if f.endswith(".json")][0]
grec = json.load(open(os.path.join(ec.TOKEN_DIR, genuine)))
CAP = "serial.write"


def plant(rec):
    tok = secrets.token_hex(32)
    with open(os.path.join(ec.TOKEN_DIR, hashlib.sha256(tok.encode()).hexdigest() + ".json"), "w") as f:
        json.dump(rec, f)
    return tok


exp = int(time.time()) + 300
# 1 pre-fix shape (no sig)
print("unsigned v1 record   ", ec.verify_token(plant({"capability": CAP, "expires_at": exp}), CAP))
# 2 v2 with random sig
print("random sig           ", ec.verify_token(plant({"v": 2, "capability": CAP, "expires_at": exp, "sig": secrets.token_hex(64)}), CAP))
# 3 genuine record copied under an attacker-chosen token name
print("genuine sig, new name", ec.verify_token(plant(dict(grec)), CAP))
# 4 malleated s + Q of the genuine sig
sig = bytes.fromhex(grec["sig"])
s2 = (int.from_bytes(sig[32:], "little") + ec._Q).to_bytes(32, "little")
print("s+Q malleation       ", ec.verify_token(plant(dict(grec, sig=(sig[:32] + s2).hex())), CAP))
# 5 identity R, s=0
ident = (1).to_bytes(32, "little")
print("identity R, s=0      ", ec.verify_token(plant({"v": 2, "capability": CAP, "expires_at": exp, "sig": (ident + bytes(32)).hex()}), CAP))
# 6 sign with seed derived from a guessed passphrase using the stored salt/params
seed = ec._seed("agent guessed this one", bytes.fromhex(v["salt"]), v["n"], v["r"], v["p"])
tok = secrets.token_hex(32)
th = hashlib.sha256(tok.encode()).hexdigest()
rec = {"v": 2, "capability": CAP, "expires_at": exp, "sig": ec.ed25519_sign(seed, ec._message(th, CAP, exp)).hex()}
with open(os.path.join(ec.TOKEN_DIR, th + ".json"), "w") as f:
    json.dump(rec, f)
print("wrong-key signature  ", ec.verify_token(tok, CAP))
# 7 expires_at as float / bool / string
print("float expiry         ", ec.verify_token(plant(dict(grec, expires_at=float(grec["expires_at"]))), CAP))
print("bool expiry          ", ec.verify_token(plant(dict(grec, expires_at=True)), CAP))
# 8 non-canonical pub not applicable (pub is Frank's); verifier made group-writable
os.chmod(ec.VERIFIER, 0o620)
print("g+w verifier issue   ", end=" ")
try:
    ec.issue_token(CAP, _getpass=lambda _p: "probe passphrase horse battery")
    print("MINTED (bad)")
except ec.EnergizeConfirmError as e:
    print("refused:", str(e)[:60])
os.chmod(ec.VERIFIER, 0o600)
# positive control: genuine passphrase via the API, then verify -> True
t = ec.issue_token(CAP, _getpass=lambda _p: "probe passphrase horse battery")
print("POSITIVE control     ", ec.verify_token(t, CAP), "| replay", ec.verify_token(t, CAP))
t = ec.issue_token(CAP, _getpass=lambda _p: "probe passphrase horse battery")
print("cross-capability     ", ec.verify_token(t, "serial.send_break"))
print("leftover token files ", sorted(os.listdir(ec.TOKEN_DIR)) == [genuine])
