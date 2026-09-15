"""独立包不连主工程号池。oauth_export 里的落库调用全部做成空操作。"""


def update_registered_oauth(*args, **kwargs):
    return None


def update_registered_oauth_status(*args, **kwargs):
    return None


def get_registered(email):
    return None


def get_account(email):
    return None


def get_setting(key, default=""):
    return default


def get_mail_settings():
    return {}


def finish_oauth_try(*args, **kwargs):
    return None


def insert_oauth_attempt_feature(*args, **kwargs):
    return None


def touch_oauth_try_start(*args, **kwargs):
    return None


def mark_registered_banned(*args, **kwargs):
    return None


def reset_oauth_tries(*args, **kwargs):
    return None


def pick_oauth_queue(*args, **kwargs):
    return []


OAUTH_MAX_TRIES_DEFAULT = 3


class _DummyConn:
    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []


def _conn():
    return _DummyConn()
