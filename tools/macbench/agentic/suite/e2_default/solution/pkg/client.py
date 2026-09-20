from pkg import config


def get(url, timeout=45):
    return {"url": url, "timeout": timeout}


def get_default(url):
    return get(url, config.DEFAULT_TIMEOUT_S)
