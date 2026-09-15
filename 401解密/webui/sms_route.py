"""独立包不解接码，占位给 oauth_export 导入。"""

LEDGER_SKIP_STREAK = 2


def normalize_sms_routes(raw=None):
    return []


class SmsRouteScheduler:
    def __init__(self, *args, **kwargs):
        self.ledger_skip_streak = 0

    def next_route(self):
        return None

    def note_ledger_skip(self, *args, **kwargs):
        return False

    def note_ledger_ok(self, *args, **kwargs):
        return None
