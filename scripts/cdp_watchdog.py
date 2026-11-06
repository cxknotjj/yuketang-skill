# -*- coding: utf-8 -*-
"""cdp_watchdog.py — 保证 CDP 调试版浏览器常驻（Chrome / Edge / Brave / Chromium）

策略：
  1) 循环探测调试端口，通了就睡 10 秒；
  2) 掉了就按「上次成功的浏览器 → 配置目录模式」重新拉起，并写回状态文件；
  3) 首次没有记录时，自动挑一个已安装的浏览器（可用 --browser 指定）。

用法：
  后台常驻：  python cdp_watchdog.py
  指定浏览器：python cdp_watchdog.py --browser edge --port 9333
日志：%LOCALAPPDATA%\\Temp\\cdp-watchdog.log
状态：%LOCALAPPDATA%\\Temp\\cdp-browser-state.json
"""
import datetime
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browser_env as B  # noqa: E402

LOG = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Temp", "cdp-watchdog.log")
STATE = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Temp", "cdp-browser-state.json")


def log(msg):
    line = "%s %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    try:
        print(line, end="", flush=True)
    except Exception:
        pass


def load_state():
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    try:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass


def recover(port, prefer):
    state = load_state()
    key = prefer or state.get("browser")
    if key not in B.BROWSERS or not B.exe_of(key):
        found = B.installed()
        if not found:
            log("未找到任何 Chromium 系浏览器，放弃")
            return False
        key = found[0]
    exe = B.exe_of(key)
    log("尝试拉起 %s（%s）" % (B.BROWSERS[key]["name"], exe))
    B.kill(key)
    for use_copy in (False, True):
        if use_copy:
            B.copy_profile(key, quiet=True)
        B.launch(key, exe, port, use_copy_profile=use_copy)
        ver = B.wait_cdp(port, 18)
        if ver:
            state.update({"browser": key, "profile": "copy" if use_copy else "real", "port": port})
            save_state(state)
            log("恢复成功：%s / %s / %s" % (B.BROWSERS[key]["name"],
                                          state["profile"], ver))
            return True
        B.kill(key)
    return False


def main():
    port = 9333
    prefer = None
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    if "--browser" in sys.argv:
        prefer = sys.argv[sys.argv.index("--browser") + 1]
    B.sanitize_proxy()
    log("watchdog started (pid %d), port=%d, prefer=%s" % (os.getpid(), port, prefer or "auto"))
    while True:
        ver = B.cdp_version(port)
        if ver:
            time.sleep(10)
            continue
        log("端口 %d 无响应，准备恢复" % port)
        ok = recover(port, prefer)
        log("恢复%s" % ("成功" if ok else "失败"))
        time.sleep(12)


if __name__ == "__main__":
    main()
