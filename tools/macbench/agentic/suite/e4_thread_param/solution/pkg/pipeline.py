def emit(item, prefix=""):
    return f"{prefix}{item}"


def stage_b(items, prefix=""):
    return [emit(i, prefix=prefix) for i in items]


def stage_a(items, prefix=""):
    return stage_b([i.strip() for i in items], prefix=prefix)


def run(items, *, prefix=""):
    return stage_a(items, prefix=prefix)
