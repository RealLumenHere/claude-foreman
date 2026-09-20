from pkg.store import load_row


def describe(db, key):
    row = load_row(db, key)
    return "missing" if row is None else f"{key}={row}"
