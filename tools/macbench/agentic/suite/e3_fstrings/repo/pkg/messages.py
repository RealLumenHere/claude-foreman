def greet(name):
    return "hello, %s" % name


def count(n, what):
    return "%d %s found" % (n, what)


def pair(a, b):
    return "(%s, %s)" % (a, b)


def ratio(x, y):
    return "%.2f of %d" % (x, y)


def keyed(d):
    return "%(k)s=%(v)s" % d
