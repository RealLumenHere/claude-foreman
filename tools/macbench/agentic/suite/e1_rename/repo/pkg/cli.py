import sys

from pkg import store
from pkg.report import describe


def main(argv=None):
    db = {"a": 1, "b": 2}
    for key in argv or sys.argv[1:]:
        print(describe(db, key), store.fetch_row(db, key))
