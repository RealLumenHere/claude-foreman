from pkg.store import fetch_row


def describe(db, key):
    row = fetch_row(db, key)
    return "missing" if row is None else f"{key}={row}"
