"""独立包不做代理死亡计数。"""


class _Mgr:
    def note(self, *args, **kwargs):
        return None

    def get_summary(self):
        return {"cooling_down_count": 0}


def get_proxy_health_manager():
    return _Mgr()
