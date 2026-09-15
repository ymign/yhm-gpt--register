"""独立包不跑套餐验活。AuthFlow 偶尔会 import 这个名字。"""


def parse_account_plan(data, body=""):
    return {
        "status": "free",
        "label": "Free",
        "plan": "free",
        "reason": "",
        "log_lines": [],
    }
