# Minimal shim for Python 3.13 where stdlib imghdr was removed.
# pgpy imports imghdr but we don't actually need image type detection.
def what(file, h=None):
    return None