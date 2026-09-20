"""Tiny in-memory row store."""


def load_row(db, key):
    """Return the row stored under key, or None."""
    return db.get(key)


def fetch_many(db, keys):
    return [load_row(db, k) for k in keys]
