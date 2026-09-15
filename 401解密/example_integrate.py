"""嵌进其它系统的最小示例。先启动不了也没关系，这是 API 用法。"""
from __future__ import annotations

import time

from engine import check_user_proxy, collect_exports, get_task, parse_account_text, start


def main():
    proxy = "socks5://user:pass@127.0.0.1:1080"
    try:
        print(check_user_proxy(proxy))
    except Exception as e:
        print("代理探测失败（仍可继续，但建议先修好）:", e)

    accounts = parse_account_text(
        "alice@x.com----Pass1----JBSWY3DPEHPK3PXP\n"
    )
    info = start(
        accounts,
        proxy_text=proxy,
        workers=2,
        timeout=45,
        force_full_login=True,
    )
    task_id = info["task_id"]
    print("task", task_id)
    while True:
        snap = get_task(task_id).snapshot()
        print(snap["done_count"], "/", snap["total"], snap["stats"])
        if snap["finished_at"]:
            break
        time.sleep(1)
    print("cpa", collect_exports(task_id, "cpa"))


if __name__ == "__main__":
    main()
