"""独立包不解接码，台账全部放行。"""


def should_skip(phone):
    return ""


def used_count(phone):
    return 0


def mark_inflight(phone):
    return None


def clear_inflight(phone):
    return None


def remember(*args, **kwargs):
    return None


def warmup():
    return {}
