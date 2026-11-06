# -*- coding: utf-8 -*-
"""bootstrap.py — 环境自检与自动准备（首次使用先跑这个）

依次确认：
  1) Python >= 3.8
  2) playwright 库可用（缺则自动 pip 安装，失败自动换清华镜像）
  3) 找到本机可用的 Chromium 系浏览器（Chrome / Edge / Brave / Chromium）
  4) 关闭该浏览器，用带调试端口的配置目录重启，并确认 CDP 就绪

用法：
  python bootstrap.py                      # 自动选浏览器（会关闭它！先征得用户同意）
  python bootstrap.py --browser edge       # 指定 Edge
  python bootstrap.py --no-kill            # 只检测，不动浏览器
  python bootstrap.py --port 9444

退出码：0 就绪 / 1 缺依赖 / 2 浏览器或端口需人工处理
"""
import json
import subprocess
import sys

import browser_env as B

MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def ensure_playwright():
    try:
        import playwright  # noqa: F401
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True, "已安装"
    except Exception:
        pass
    print("未检测到 playwright，正在安装（仅库，不下载浏览器）…", flush=True)
    for args in (["install", "playwright"], ["install", "-i", MIRROR, "playwright"]):
        try:
            r = subprocess.run([sys.executable, "-m", "pip"] + args,
                               capture_output=True, text=True)
            if r.returncode == 0:
                return True, "已自动安装"
        except Exception:
            continue
    return False, "安装失败，请手动执行：%s -m pip install playwright" % sys.executable


def main():
    port = int(arg("--port", 9333))
    prefer = arg("--browser")
    no_kill = "--no-kill" in sys.argv

    status = {"python": sys.version.split()[0], "playwright": None,
              "browser": None, "cdp": None, "port": port}
    print("== 雨课堂自动作答：环境自检 ==", flush=True)

    if sys.version_info < (3, 8):
        print("[X] Python 版本过低（%s），需要 3.8+" % status["python"])
        return 1
    print("[OK] Python %s" % status["python"])

    ok, msg = ensure_playwright()
    status["playwright"] = ok
    if not ok:
        print("[X] playwright 不可用：%s" % msg)
        return 1
    print("[OK] playwright %s" % msg)

    B.sanitize_proxy()

    ver = B.cdp_version(port)
    if ver:
        status["cdp"] = ver
        print("[OK] 调试端口 %d 已就绪：%s" % (port, ver))
        print(json.dumps(status, ensure_ascii=False))
        return 0

    found = B.installed()
    if not found:
        print("[X] 未找到 Chrome / Edge / Brave / Chromium，请先安装其中一个")
        return 1
    print("[OK] 已安装浏览器：%s" % "、".join(B.BROWSERS[k]["name"] for k in found))

    if no_kill:
        print("[!] 指定了 --no-kill：只检测不启动。请手动关闭浏览器后重跑，"
              "或用 scripts/start_cdp_browser.cmd")
        return 2

    print("[..] 准备启动带调试端口的浏览器（会关闭该浏览器，未保存标签会丢）")
    ok, info = B.ensure(port=port, prefer=prefer, no_kill=False)
    status["browser"] = info.get("name")
    status["cdp"] = info.get("cdp")
    if ok:
        print("[OK] %s 调试端口 %d 就绪（%s配置目录）：%s"
              % (info.get("name"), port,
                 "副本" if info.get("profile") == "copy" else "真实", info.get("cdp")))
        print(json.dumps(status, ensure_ascii=False))
        return 0

    print("[X] 端口 %d 起不来。可尝试：" % port)
    print("    1) 换浏览器：python bootstrap.py --browser edge")
    print("    2) 换端口：  python bootstrap.py --port 9444")
    print("    3) 手动启动：\"<浏览器exe>\" --user-data-dir=\"%%LOCALAPPDATA%%\\Temp\\cdp-<browser>-profile\" "
          "--remote-debugging-port=%d --remote-allow-origins=*" % port)
    return 2


if __name__ == "__main__":
    sys.exit(main())
