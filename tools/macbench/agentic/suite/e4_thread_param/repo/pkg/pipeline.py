def emit(item):
    return f"{item}"


def stage_b(items):
    return [emit(i) for i in items]


def stage_a(items):
    return stage_b([i.strip() for i in items])


def run(items):
    return stage_a(items)
