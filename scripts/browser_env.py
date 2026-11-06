# -*- coding: utf-8 -*-
"""browser_env.py — 浏览器探测与 CDP 启动（Chrome / Edge / Brave / Chromium 通用）

做法：所有 Chromium 系浏览器都支持 CDP，命令与协议一致，只是
  * 可执行文件路径不同；
  * 用户配置目录不同（登录态就存在那里）；
  * 进程名不同（关闭时要杀对进程）。

Chrome 136+ 起，在「默认用户目录」上会忽略 --remote-debugging-port，
因此统一用「复制一份配置目录」的方式启动；同系统用户下 Cookie 可正常解密，
登录态得以保留。Edge / Brave / Chromium 同理。
"""
import json
import os
import shutil
import subprocess
import time
import urllib.request

LOCAL = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
PROGRAM_FILES = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]

# key -> 可执行文件候选、真实配置目录、进程名、显示名
BROWSERS = {
    "chrome": {
        "name": "Google Chrome",
        "exe": [os.path.join(p, r"Google\Chrome\Application\chrome.exe") for p in PROGRAM_FILES]
               + [shutil.which("chrome") or "", shutil.which("google-chrome") or ""],
        "user_data": os.path.join(LOCAL, r"Google\Chrome\User Data"),
        "proc": ["chrome.exe"],
    },
    "edge": {
        "name": "Microsoft Edge",
        "exe": [os.path.join(p, r"Microsoft\Edge\Application\msedge.exe") for p in PROGRAM_FILES]
               + [shutil.which("msedge") or ""],
        "user_data": os.path.join(LOCAL, r"Microsoft\Edge\User Data"),
        "proc": ["msedge.exe"],
    },
    "brave": {
        "name": "Brave",
        "exe": [os.path.join(p, r"BraveSoftware\Brave-Browser\Application\brave.exe") for p in PROGRAM_FILES]
               + [shutil.which("brave") or ""],
        "user_data": os.path.join(LOCAL, r"BraveSoftware\Brave-Browser\User Data"),
        "proc": ["brave.exe"],
    },
    "chromium": {
        "name": "Chromium",
        "exe": [os.path.join(p, r"Chromium\Application\chrome.exe") for p in PROGRAM_FILES]
               + [shutil.which("chromium") or "", shutil.which("chromium-browser") or ""],
        "user_data": os.path.join(LOCAL, r"Chromium\User Data"),
        "proc": ["chromium.exe"],
    },
}
ORDER = ["chrome", "edge", "brave", "chromium"]


def exe_of(key):
    for p in BROWSERS[key]["exe"]:
        if p and os.path.isfile(p):
            return p
    return None


def installed():
    """返回已安装的浏览器 key 列表（按优先级）。"""
    return [k for k in ORDER if exe_of(k)]


def pick(prefer=None):
    """选定浏览器：指定优先；否则按「配置目录存在 → 已安装」的顺序挑。"""
    if prefer:
        if prefer not in BROWSERS:
            raise ValueError("不支持的浏览器：%s（可选 %s）" % (prefer, "/".join(ORDER)))
        exe = exe_of(prefer)
        if not exe:
            raise ValueError("%s 未安装" % BROWSERS[prefer]["name"])
        return prefer, exe
    cands = installed()
    if not cands:
        raise ValueError("未找到任何 Chromium 系浏览器（Chrome/Edge/Brave/Chromium）")
    with_profile = [k for k in cands if os.path.isdir(BROWSERS[k]["user_data"])]
    key = (with_profile or cands)[0]
    return key, exe_of(key)


def cdp_profile(key):
    """副本配置目录（避免 Chrome 136+ 默认目录禁调试端口的限制）。"""
    return os.path.join(LOCAL, "Temp", "cdp-%s-profile" % key)


def sanitize_proxy():
    """本机代理会把 127.0.0.1 的请求也转发出去，必须先清掉。"""
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"


def cdp_version(port, timeout=4):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:%d/json/version" % port, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
            if data.get("webSocketDebuggerUrl"):
                return data.get("Browser", "unknown")
    except Exception:
        pass
    return None


def kill(key):
    if os.name != "nt":
        return
    for proc in BROWSERS[key]["proc"]:
        subprocess.run(["taskkill", "/IM", proc, "/F"], capture_output=True)
    time.sleep(3)


def copy_profile(key, quiet=False):
    src = BROWSERS[key]["user_data"]
    dst = cdp_profile(key)
    if os.path.isdir(dst) and os.listdir(dst):
        return True
    os.makedirs(dst, exist_ok=True)
    if not os.path.isdir(src):
        return True          # 没装过/没用过，空目录也能起，登录需用户手动做一次
    if not quiet:
        print("  正在复制 %s 配置目录（登录态会保留，首次约 1-2 分钟）…" % BROWSERS[key]["name"], flush=True)
    try:
        subprocess.run(["robocopy", src, dst, "Local State",
                        "/NFL", "/NDL", "/NJH", "/NJS", "/R:1", "/W:1"], capture_output=True)
        subprocess.run(["robocopy", os.path.join(src, "Default"), os.path.join(dst, "Default"), "/E",
                        "/XD", "Cache", "Code Cache", "GPUCache", "ShaderCache",
                        "Service Worker", "CacheStorage", "Extension State",
                        "/NFL", "/NDL", "/NJH", "/NJS", "/R:1", "/W:1"], capture_output=True)
        return True
    except Exception as e:
        print("  复制失败：%s" % e, flush=True)
        return False


def launch(key, exe, port, use_copy_profile=True):
    args = [exe]
    if use_copy_profile:
        args += ["--user-data-dir=%s" % cdp_profile(key), "--profile-directory=Default"]
    args += ["--remote-debugging-port=%d" % port, "--remote-allow-origins=*",
             "--no-first-run", "--no-default-browser-check"]
    subprocess.Popen(args, close_fds=True)
    print("  已启动 %s（%s）" % (BROWSERS[key]["name"],
                              "副本配置目录" if use_copy_profile else "真实配置目录"), flush=True)


def wait_cdp(port, seconds=20):
    for _ in range(int(seconds / 1.5)):
        time.sleep(1.5)
        v = cdp_version(port)
        if v:
            return v
    return None


def ensure(port=9333, prefer=None, no_kill=False, retry_real=True, quiet=False):
    """确保 CDP 就绪。返回 (ok, info dict)。"""
    sanitize_proxy()
    ver = cdp_version(port)
    if ver:
        return True, {"browser": None, "cdp": ver, "reused": True}

    key, exe = pick(prefer)
    info = {"browser": key, "name": BROWSERS[key]["name"], "exe": exe, "cdp": None}
    if no_kill:
        return False, info

    kill(key)
    if retry_real:
        launch(key, exe, port, use_copy_profile=False)
        ver = wait_cdp(port, 15)
        if ver:
            info["cdp"] = ver
            info["profile"] = "real"
            return True, info
        kill(key)

    copy_profile(key, quiet=quiet)
    launch(key, exe, port, use_copy_profile=True)
    ver = wait_cdp(port, 21)
    if ver:
        info["cdp"] = ver
        info["profile"] = "copy"
        return True, info
    return False, info
