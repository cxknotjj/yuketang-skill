# -*- coding: utf-8 -*-
"""ykt_executor.py — 雨课堂习题机械执行器（零大模型调用）

三个阶段：
  collect  逐题切页、截图题目        → manifest.json + shots/qNN.png
  apply    按 answers.json 点选并提交 → state.json
  report   打开成绩单抓总分          → report.json / report.txt

已验证的关键事实（勿改）：
  * 题面在 iframe 内，必须进入对应 frame 才能操作；
  * 选项是 label.el-checkbox（多选）/ label.el-radio（单选），点击是 toggle，
    所以必须先读 is-checked 状态，只点「该选未选」和「已选不该选」的项；
  * 提交后页面自动跳到下一题，因此回读得分要先退回该题；
  * 页面有坐标 -10000 的诱饵元素，按可见尺寸过滤即可。

用法：
  python ykt_executor.py --phase collect --url <习题页> --run-dir <目录>
  python ykt_executor.py --phase apply   --run-dir <目录> --answers <answers.json>
  python ykt_executor.py --phase report  --run-dir <目录>

遇到安全核验（验证码）时脚本直接退出并提示人工处理，不做任何破解。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# 本机回环不走代理，否则 CDP 端口会被代理劫持
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

from playwright.sync_api import sync_playwright  # noqa: E402

DEFAULT_CDP = "http://127.0.0.1:9333"
OPT_SEL = "label.el-checkbox, label.el-radio"
CAPTCHA_MARKERS = ("请完成安全核验", "安全校验", "依次经过")
EXIT_CAPTCHA = 2
EXIT_LOGIN = 3


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def jload(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def jsave(obj, path):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ 浏览器与页面

def connect(cdp_url):
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(cdp_url)
    return pw, browser, browser.contexts[0]


def open_exercise(ctx, url):
    base = url.split("?")[0]
    for page in ctx.pages:
        if base in page.url:
            page.bring_to_front()
            if page.url != url:
                page.goto(url, wait_until="domcontentloaded")
            return page
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded")
    return page


def is_login_page(page):
    """登录态失效时会跳 /web/?next=… 登录页。"""
    try:
        if "/web/?next=" in page.url or page.url.rstrip("/").endswith("/web"):
            return True
        return "登录" in (page.title() or "")
    except Exception:
        return False


def maximize(page):
    """窗口最大化，保证底部按钮在视口内（一次设置，全程有效）。"""
    try:
        cdp = page.context.new_cdp_session(page)
        wid = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": wid,
                                            "bounds": {"windowState": "maximized"}})
        page.wait_for_timeout(500)
    except Exception:
        pass


def text(scope):
    try:
        return scope.inner_text("body", timeout=1500)
    except Exception:
        return ""


class CaptchaHit(Exception):
    """安全核验弹窗出现——立刻中止，交给上层提示用户。"""


CHALLENGE_SEL = '#homework-automation-risk-challenge, [data-risk-target="challenge"]'


def captcha_now(page, scope=None):
    """轻量检测：只查 DOM 标记（几十毫秒），用于高频巡检。"""
    frames = [page.main_frame]
    if scope is not None:
        frames.insert(0, scope)
    for fr in frames:
        try:
            if fr.evaluate("() => !!document.querySelector('%s')" % CHALLENGE_SEL):
                return True
        except Exception:
            continue
    return False


def guard(page, scope=None):
    if captcha_now(page, scope):
        raise CaptchaHit()


def wait_frame(page, timeout_s=30):
    """题面所在 iframe：正文含「提交」或「下一题」的子 frame。"""
    for _ in range(timeout_s * 2):
        guard(page)
        for fr in page.frames:
            if fr is page.main_frame:
                continue
            t = text(fr)
            if "提交" in t or "下一题" in t:
                return fr
        page.wait_for_timeout(500)
    return None


def wait_question(page, timeout_s=30):
    """等到题面渲染完成（frame 出现且题号可解析）。"""
    scope = wait_frame(page, timeout_s)
    for _ in range(20):
        if scope is not None and current_index(scope) is not None:
            return scope
        page.wait_for_timeout(300)
        guard(page, scope)
        scope = wait_frame(page, 3) or scope
    return scope


def captcha_present(page, scope):
    """安全核验弹窗：以 DOM 标记为准（题面文字是加密字体，inner_text 读到的是乱码）。"""
    for s in (scope, page.main_frame):
        try:
            hit = s.evaluate(
                """() => !!document.querySelector(
                     '#homework-automation-risk-challenge, [data-risk-target="challenge"]')""")
            if hit:
                return True
        except Exception:
            continue
        if any(m in text(s) for m in CAPTCHA_MARKERS):
            return True
    return False


def wait_captcha_clear(page, run_dir, timeout_s=120):
    """检测到验证码后不退出：轮询等用户手动完成，超时才停。返回是否已清除。"""
    shot = Path(run_dir or ".") / "captcha.png"
    try:
        page.screenshot(path=str(shot))
    except Exception:
        pass
    log("⚠️ 检测到安全核验：等待人工完成（最多 %d 秒），完成后会自动继续…" % timeout_s)
    log("   截图：%s" % shot)
    for _ in range(int(timeout_s / 2)):
        page.wait_for_timeout(2000)
        if not captcha_now(page):
            log("验证码已清除，继续执行。")
            return True
    return False


def stop_on_captcha(page, scope, run_dir):
    """遇验证码：截图留证 + 明确提示，直接停止，不尝试通过。"""
    shot = Path(run_dir) / "captcha.png"
    try:
        page.screenshot(path=str(shot))
    except Exception:
        pass
    log("⚠️ 出现安全核验（验证码）：脚本已停止，请人工在浏览器中完成核验后重新运行。")
    log("   截图：%s" % shot)
    return EXIT_CAPTCHA


# ------------------------------------------------------------------ 题目定位与操作

def current_index(scope):
    """当前题号：题面首行形如 '11.' / '11、' / '11．' 的数字。"""
    for line in text(scope).splitlines():
        m = re.match(r"^(\d{1,2})\s*[.、．]", line.strip())
        if m:
            return int(m.group(1))
    return None


def total_questions(scope):
    """总题数：顶部计数器 'X/Y 题' 的 Y。"""
    m = re.search(r"\d+\s*/\s*(\d+)\s*题", text(scope))
    return int(m.group(1)) if m else None


def find_button(scope, label):
    """按文本找可见且可点的按钮（跳过屏幕外诱饵与禁用态）。"""
    loc = scope.get_by_text(label, exact=True)
    for k in range(loc.count()):
        el = loc.nth(k)
        box = el.bounding_box()
        if not (box and box["width"] > 2 and box["x"] > 0 and box["y"] > 0):
            continue
        if el.evaluate("e => !!e.closest('.is-disabled,[disabled]')"):
            continue        # 末题的「下一题」是禁用态，不能点
        return el
    return None


def dismiss_guard(scope):
    """切题守卫：当前题有未提交作答时切题会弹「需要提交吗」，选「先不用了」继续跳。"""
    try:
        el = scope.get_by_text("先不用了", exact=True).first
        if el.is_visible():
            el.click(timeout=5000)
            return True
    except Exception:
        pass
    return False


def find_button_like(scope, label):
    """文本包含即可（页面按钮常带箭头，如「展开 »」「收起 «」）。"""
    loc = scope.get_by_text(label, exact=False)
    for k in range(min(loc.count(), 10)):
        el = loc.nth(k)
        box = el.bounding_box()
        if not (box and box["width"] > 2 and box["x"] > 0 and box["y"] > 0):
            continue
        if el.evaluate("e => !!e.closest('.is-disabled,[disabled]')"):
            continue
        return el
    return None


def goto_question(page, scope, target, max_clicks=60):
    """切到第 target 题：优先点左侧题号直达（1 次点击），不行再退回上一题/下一题翻页。"""
    cur = current_index(scope)
    if cur == target:
        return cur

    # 题号直达
    try:
        loc = scope.locator(".problems-aside").get_by_text(str(target), exact=True)
        if loc.count():
            loc.first.click(timeout=6000)
            page.wait_for_timeout(700)
            dismiss_guard(scope)          # 有未提交作答时会被守卫拦下
            wait_index_change(page, scope, cur, timeout_ms=3000, floor_ms=200)
            if current_index(scope) == target:
                return target
    except Exception:
        pass

    # 回退：逐题翻页
    for _ in range(max_clicks):
        cur = current_index(scope)
        if cur == target:
            return cur
        guard(page, scope)
        el = find_button(scope, "上一题" if (cur and cur > target) else "下一题")
        if el is None:
            return cur
        try:
            el.click(timeout=8000)
        except Exception:
            return cur          # 点不动（多为验证码遮罩），交给上层判验证码
        wait_index_change(page, scope, cur, timeout_ms=4000, floor_ms=600)
    return current_index(scope)


def options(scope):
    """可见选项元素，DOM 顺序即 A/B/C/D。"""
    els = []
    for el in scope.query_selector_all(OPT_SEL):
        box = el.bounding_box()
        if box and box["width"] > 2 and box["height"] > 2 and box["x"] > 0:
            els.append(el)
    return els


def selected(scope):
    """已选中的选项字母。"""
    out = []
    for k, el in enumerate(options(scope)):
        if "is-checked" in (el.get_attribute("class") or ""):
            out.append(chr(65 + k))
    return out


def is_submitted(scope):
    t = text(scope)
    return ("本题得分" in t) or ("正确答案" in t) or (find_button(scope, "已提交") is not None)


def select(page, scope, letters, multi):
    """按答案点选。toggle 语义：只点差额，多选还要取消多余项。"""
    els = options(scope)
    sel = selected(scope)
    if multi:
        todo = [c for c in letters if c not in sel] + [c for c in sel if c not in letters]
    else:
        todo = [] if letters[0] in sel else [letters[0]]
    for c in todo:
        k = ord(c) - 65
        if k >= len(els):
            return False
        guard(page, scope)
        els[k].click(timeout=4000)
        page.wait_for_timeout(250)
        guard(page, scope)
    sel = selected(scope)
    return set(sel) == set(letters) if multi else letters[0] in sel


def submit(page, scope):
    guard(page, scope)
    el = find_button(scope, "提交")
    if el is None:
        return False
    before = current_index(scope)
    try:
        el.click(timeout=4000)
    except Exception:
        guard(page, scope)
        return False
    wait_index_change(page, scope, before, timeout_ms=4000, floor_ms=600)
    return True


def shot_question(page, scope, path):
    try:
        scope.frame_element().screenshot(path=str(path))
    except Exception:
        page.screenshot(path=str(path))


# ------------------------------------------------------------------ 阶段实现

def phase_collect(page, run_dir, resume=False):
    shots = Path(run_dir) / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    mf = Path(run_dir) / "manifest.json"
    if resume and mf.exists():
        manifest = jload(mf)
    else:
        manifest = {"exercise_url": page.url,
                    "collected_at": datetime.now().isoformat(), "questions": []}
    start = len(manifest["questions"]) + 1
    scope = wait_question(page)
    if scope is None:
        log("题面未渲染，停止。")
        return 1
    total = total_questions(scope)
    if not total:
        log("读不到总题数，停止。")
        return 1
    log("总题数 %d，从第 %d 题开始采集。" % (total, start))
    for i in range(start, total + 1):
        scope = wait_question(page) or scope
        if captcha_present(page, scope):
            jsave(manifest, mf)
            return stop_on_captcha(page, scope, run_dir)
        if goto_question(page, scope, i) != i:
            if captcha_present(page, scope):
                jsave(manifest, mf)
                return stop_on_captcha(page, scope, run_dir)
            log("题%d: 定位失败，跳过。" % i)
            continue
        scope = wait_question(page) or scope
        path = shots / ("q%02d.png" % i)
        shot_question(page, scope, path)
        manifest["questions"].append({"index": i, "screenshot": str(path),
                                      "options": len(options(scope))})
        log("collect 题%d -> %s" % (i, path.name))
    manifest["total"] = len(manifest["questions"])
    jsave(manifest, mf)
    log("collect 完成：共 %d 题 → %s" % (manifest["total"], mf))
    return 0


def check_entry(entry, q):
    """单题答案自检：题型与选项数是否自洽。返回问题描述列表。"""
    errs = []
    letters = entry.get("answer") or []
    typ = entry.get("type")
    if typ in ("单选", "判断") and len(letters) != 1:
        errs.append("单选/判断应恰好 1 个字母，实际 %d 个" % len(letters))
    if typ == "多选" and len(letters) < 2:
        errs.append("多选只有 %d 个字母，疑似标错题型（少选不得分）" % len(letters))
    n = q.get("options")
    if n and any(ord(c) - 65 >= n for c in letters):
        errs.append("答案字母超出该题选项数（%d 项）" % n)
    return errs


def phase_apply(page, run_dir, answers_file, resume, only=None,
                dry_run=False, allow_low=False):
    run = Path(run_dir)
    manifest = jload(run / "manifest.json")
    amap = {a["index"]: a for a in jload(answers_file)["answers"]}
    missing = [q["index"] for q in manifest["questions"] if q["index"] not in amap]
    if missing:
        log("answers.json 缺少题号 %s，未做任何提交。" % missing)
        return 1
    for q in manifest["questions"]:
        for e in check_entry(amap[q["index"]], q):
            log("⚠️ 题%d 答案自检：%s" % (q["index"], e))

    state_file = run / "state.json"
    state = jload(state_file) if (resume and state_file.exists()) else {"done": {}, "flagged": []}

    for q in manifest["questions"]:
        i = q["index"]
        if (only and i not in only) or str(i) in state["done"]:
            continue
        scope = wait_question(page)
        dismiss_guard(scope)          # 清掉可能残留的切题守卫弹窗
        if captcha_present(page, scope):
            jsave(state, state_file)
            return stop_on_captcha(page, scope, run_dir)
        if goto_question(page, scope, i) != i:
            if captcha_present(page, scope):
                jsave(state, state_file)
                return stop_on_captcha(page, scope, run_dir)
            log("题%d: 定位失败，跳过。" % i)
            state["flagged"].append({"index": i, "reason": "locate_failed"})
            jsave(state, state_file)
            continue
        scope = wait_question(page) or scope
        if is_submitted(scope):
            log("题%d: 已提交，跳过。" % i)
            state["done"][str(i)] = {"result": "already_submitted"}
            jsave(state, state_file)
            continue

        entry = amap[i]
        conf = (entry.get("confidence") or "high").lower()
        if not allow_low and conf != "high":
            log("题%d: 置信度 %s，按闸门跳过（确认无误后加 --allow-low 重跑）。" % (i, conf))
            state["flagged"].append({"index": i, "reason": "low_confidence",
                                     "answer": entry.get("answer"), "confidence": conf})
            jsave(state, state_file)
            continue
        letters = entry.get("answer") or []
        if entry.get("type") == "判断" and entry.get("answer_judge"):
            letters = ["A" if str(entry["answer_judge"]).startswith(("对", "正", "T")) else "B"]
        if not select(page, scope, letters, entry.get("type") == "多选"):
            log("题%d: 选项选中态与答案不一致，未提交。" % i)
            state["flagged"].append({"index": i, "reason": "select_mismatch"})
            jsave(state, state_file)
            continue
        if dry_run:
            log("题%d: [dry-run] 已选 %s，未提交。" % (i, "".join(letters)))
            state.setdefault("dry_run", {})[str(i)] = "".join(letters)
            jsave(state, state_file)
            continue
        if not submit(page, scope):
            log("题%d: 提交按钮不可用，跳过。" % i)
            state["flagged"].append({"index": i, "reason": "submit_unavailable"})
            jsave(state, state_file)
            continue

        # 提交后页面自动跳到下一题，不手动切题；得分统一在 report 阶段读
        page.wait_for_timeout(600)
        if captcha_present(page, scope):
            jsave(state, state_file)
            return stop_on_captcha(page, scope, run_dir)
        state["done"][str(i)] = {"answer": letters}
        log("题%d: 已提交 %s" % (i, "".join(letters)))
        jsave(state, state_file)

    jsave(state, state_file)
    log("apply 结束：完成 %d 题，标记 %d 题"
        % (len(state["done"]), len(state["flagged"])))
    return 0


def score_wrong(scope):
    """成绩单网格里标红（icon…danger）的题号。"""
    try:
        return scope.evaluate("""() => [...document.querySelectorAll('.subject-item.primary')]
            .map(e => {
                const t = (e.innerText || '').trim();
                if (!/^\\d+$/.test(t)) return null;
                return /danger/.test(e.innerHTML) ? parseInt(t, 10) : null;
            }).filter(v => v !== null)""")
    except Exception:
        return []


def phase_report(page, run_dir):
    run = Path(run_dir)
    scope = wait_frame(page)
    if scope is None:
        log("题面未渲染，无法进成绩单。")
        return 1
    # 成绩单是异步渲染的：按钮可能还没出现，边等边点「展开」，直到出现「作业得分」
    body = ""
    clicked = False
    for _ in range(20):
        scope = wait_frame(page) or scope
        if not clicked:
            el = find_button_like(scope, "展开")     # 按钮实际是「展开 »」
            if el is not None:
                el.click()
                clicked = True
        page.wait_for_timeout(800)
        body = text(scope) + "\n" + text(page.main_frame)
        if "作业得分" in body:
            break
    got = full = None
    m = re.search(r"作业得分[：:\s]*(\d+)\s*/\s*(\d+)", body)
    if m:
        got, full = int(m.group(1)), int(m.group(2))
    n = re.search(r"共\s*(\d+)\s*题", body)

    wrong = []
    for idx in score_wrong(scope):
        detail = {"index": idx, "correct_answer": None}
        try:
            loc = scope.locator(".problems-aside").get_by_text(str(idx), exact=True)
            if loc.count():
                loc.first.click(timeout=4000)
                page.wait_for_timeout(900)
                m = re.search(r"正确答案[：:\s]*([A-E]+)", text(scope))
                if m:
                    detail["correct_answer"] = m.group(1)
        except Exception:
            pass
        wrong.append(detail)

    shot = run / "result.png"
    page.screenshot(path=str(shot))
    report = {"exercise_url": page.url, "score": {"got": got, "full": full},
              "wrong": wrong,
              "question_count": int(n.group(1)) if n else None,
              "state": jload(run / "state.json") if (run / "state.json").exists() else {},
              "result_screenshot": str(shot),
              "generated_at": datetime.now().isoformat()}
    jsave(report, run / "report.json")
    passed = got is not None and full and got / full >= 0.9
    lines = ["雨课堂作业执行报告",
             "链接: %s" % page.url,
             "得分: %s" % ("%d/%d" % (got, full) if got is not None else "未抓取到，见 result.png"),
             "是否达到 90%%: %s" % ("是" if passed else "否")]
    if wrong:
        lines.append("错题：%s" % "、".join(
            "第%d题(正确答案 %s)" % (w["index"], w["correct_answer"] or "?") for w in wrong))
    (run / "report.txt").write_text("\n".join(lines), encoding="utf-8")
    log("\n".join(lines))
    el = find_button_like(scope, "收起")     # 收起成绩单，恢复题目视图
    if el is not None:
        el.click()
        page.wait_for_timeout(600)
    return 0


def wait_index_change(page, scope, old, timeout_ms=4000, floor_ms=600):
    """等题号变化（比固定等 1.5 秒快），期间持续巡检验证码。"""
    page.wait_for_timeout(floor_ms)
    for _ in range(int((timeout_ms - floor_ms) / 250)):
        guard(page, scope)
        if current_index(scope) != old:
            return True
        page.wait_for_timeout(250)
    return current_index(scope) != old


def goto_exercise_by_title(page, title, timeout_s=12):
    """在左侧目录里点开指定作业：同页切换 iframe，不重新加载应用。"""
    mf = page.main_frame
    before = page.url
    ok = mf.evaluate("""(t) => {
        const items = [...document.querySelectorAll('.leaf-item')];
        const el = items.find(e => {
            const n = e.querySelector('.leaf-item-title');
            return n && n.innerText.trim() === t;
        });
        if (!el) return false;
        el.click();
        return true;
    }""", title)
    if not ok:
        return False
    for _ in range(int(timeout_s / 0.5)):
        page.wait_for_timeout(500)
        if page.url != before:
            return True
    return page.url != before


def refresh(page, wait_ms=2000):
    """强制重载，让左侧目录的完成状态刷新（页面会缓存旧的勾选状态）。"""
    try:
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(wait_ms)
    except Exception:
        pass


def scan_catalog(page, timeout_s=20):
    """读左侧「目录」里的作业条目，判断哪些还没完成。

    判定依据（实测）：.leaf-item-status 内含 icon-yuanquangou-mianzhuang 图标 = 已完成，
    为空（<!---->） = 未完成。返回 (progress, items)。
    """
    mf = page.main_frame
    # 1) 等目录条目出现
    for _ in range(int(timeout_s / 0.5)):
        try:
            n = mf.evaluate("() => document.querySelectorAll('.leaf-item').length")
        except Exception:
            n = 0
        if n:
            break
        page.wait_for_timeout(500)

    # 2) 等「完成度」渲染出来（进度条比状态图标先到）
    progress = ""
    for _ in range(int(timeout_s / 0.5)):
        try:
            progress = (mf.evaluate(
                "() => { const p = document.querySelector('.progress-num');"
                " return p ? p.innerText.trim() : null; }") or "").strip()
        except Exception:
            progress = ""
        if progress:
            break
        page.wait_for_timeout(500)

    # 3) 状态图标是异步补上的，且全部「未完成」与「尚未渲染」长得一样，
    #    所以固定再等 2.5 秒，避免把已完成误报成未完成
    page.wait_for_timeout(2500)

    items = mf.evaluate("""() => [...document.querySelectorAll('.leaf-item')].map(e => {
        const t = e.querySelector('.leaf-item-title');
        const tag = e.querySelector('.leaf-item-tag');
        const st = e.querySelector('.leaf-item-status');
        return {
            title: (t ? t.innerText : '').trim(),
            tag: (tag ? tag.innerText : '').trim(),
            done: !!st && /icon-yuanquangou/.test(st.innerHTML)
        };
    }).filter(o => o.title)""")
    return progress, items or []


def phase_scan(page, run_dir=None):
    """判断是否还有其他未完成的习题/作业。"""
    refresh(page)
    progress, items = scan_catalog(page)
    homework = [i for i in items if "作业" in i["tag"] or "习题" in i["title"]]
    unfinished = [i for i in homework if not i["done"]]
    result = {"course_url": page.url, "progress": progress,
              "homework_total": len(homework),
              "finished": [i["title"] for i in homework if i["done"]],
              "unfinished": [i["title"] for i in unfinished],
              "unfinished_count": len(unfinished),
              "has_unfinished": bool(unfinished),
              "scanned_at": datetime.now().isoformat()}
    if run_dir:
        jsave(result, Path(run_dir) / "scan.json")

    log("目录完成度：%s" % (progress or "未知"))
    log("作业条目：共 %d 个，已完成 %d 个，**未完成 %d 个**"
        % (len(homework), len(homework) - len(unfinished), len(unfinished)))
    for i in unfinished:
        log("  未完成 - %s" % i["title"])
    if not unfinished:
        log("没有其他未完成的习题。")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


def batch_targets(page):
    """目录里未完成的作业/习题标题。"""
    _, items = scan_catalog(page)
    return [i["title"] for i in items
            if ("作业" in i["tag"] or "习题" in i["title"]) and not i["done"]]


def phase_batch_collect(page, run_root):
    root = Path(run_root)
    root.mkdir(parents=True, exist_ok=True)
    refresh(page)
    titles = batch_targets(page)
    if not titles:
        log("没有未完成的习题。")
        return 0
    log("未完成 %d 份，逐份采集。" % len(titles))
    for t in titles:
        if not goto_exercise_by_title(page, t):
            log("跳过（目录里点不开）：%s" % t)
            continue
        page.wait_for_timeout(1500)
        m = re.search(r"exercise/(\d+)", page.url)
        eid = m.group(1) if m else str(abs(hash(t)) % 10 ** 8)
        run = root / ("run_" + eid)
        log("→ %s" % t)
        rc = phase_collect(page, str(run), True)
        if rc:
            return rc
    return 0


def phase_batch_apply(page, run_root, dry_run=False, allow_low=False):
    root = Path(run_root)
    runs = [d for d in sorted(root.iterdir())
            if d.is_dir() and (d / "manifest.json").exists()]
    if not runs:
        log("运行目录下没有可处理的作业。")
        return 0
    for d in runs:
        ans = d / "answers.json"
        if not ans.exists():
            log("%s：缺少 answers.json，跳过。" % d.name)
            continue
        url = jload(d / "manifest.json")["exercise_url"]
        log("== %s ==" % d.name)
        if page.url != url:
            page.goto(url, wait_until="domcontentloaded")
        rc = phase_apply(page, str(d), str(ans), True, None, dry_run, allow_low)
        if rc:
            return rc
        phase_report(page, str(d))
    return 0


def parse_range(spec):
    if not spec:
        return None
    out = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part.strip():
            out.add(int(part))
    return out


def run_resumable(page, run_dir, call, attempts=3):
    """执行阶段；中途弹验证码就等人工完成再带 resume 续跑（最多 attempts 次）。"""
    for k in range(attempts):
        try:
            return call(run_dir)
        except CaptchaHit:
            if not wait_captcha_clear(page, run_dir):
                return stop_on_captcha(page, None, run_dir or Path.cwd())
            log("验证码已处理，继续执行（第 %d 次续跑）。" % (k + 1))
    log("多次被验证码打断，停止。")
    return EXIT_CAPTCHA


def main():
    ap = argparse.ArgumentParser(description="雨课堂机械执行器（零 LLM）")
    ap.add_argument("--phase", required=True,
                    choices=["scan", "collect", "apply", "report",
                             "batch-collect", "batch-apply"])
    ap.add_argument("--run-dir", help="工作目录；scan 阶段可省略")
    ap.add_argument("--url", help="作业页/课程页地址；collect 阶段必填，其余可从 manifest 读取")
    ap.add_argument("--entry", help="在左侧目录里点开指定作业（同页切换，不重载应用）")
    ap.add_argument("--answers", help="apply 阶段：answers.json")
    ap.add_argument("--only", help="apply 阶段：只跑指定题号，如 13-20")
    ap.add_argument("--resume", action="store_true", help="断点续跑")
    ap.add_argument("--run-root", help="batch 阶段：批量工作根目录")
    ap.add_argument("--dry-run", action="store_true", help="apply：只点选不提交，供人工核对")
    ap.add_argument("--allow-low", action="store_true", help="apply：允许提交非 high 置信度的题")
    ap.add_argument("--cdp", default=DEFAULT_CDP)
    args = ap.parse_args()

    batch = args.phase in ("batch-collect", "batch-apply")
    if batch and not args.run_root:
        print("batch 阶段需要 --run-root", file=sys.stderr)
        return 1

    run = Path(args.run_dir) if args.run_dir else None
    url = args.url
    if not url and run is not None:
        mf = run / "manifest.json"
        url = jload(mf)["exercise_url"] if mf.exists() else None
    no_run_dir = args.phase in ("scan", "batch-collect", "batch-apply")
    if not url and not no_run_dir:
        print("缺少 --url 且运行目录无 manifest.json", file=sys.stderr)
        return 1
    if not no_run_dir and run is None:
        print("该阶段需要 --run-dir", file=sys.stderr)
        return 1
    if args.phase == "apply" and not args.answers:
        print("apply 阶段需要 --answers", file=sys.stderr)
        return 1

    pw, _browser, ctx = connect(args.cdp)
    try:
        if args.phase == "scan":
            page = (open_exercise(ctx, url) if url
                    else (ctx.pages[0] if ctx.pages else ctx.new_page()))
            maximize(page)
            return phase_scan(page, run)
        page = open_exercise(ctx, url) if url else (
            ctx.pages[0] if ctx.pages else ctx.new_page())
        maximize(page)
        if args.entry:
            if not goto_exercise_by_title(page, args.entry):
                print("目录里找不到作业：%s" % args.entry, file=sys.stderr)
                return 1
            log("已同页切换到：%s" % args.entry)
        if is_login_page(page):
            print("[X] 该浏览器未登录雨课堂（跳到登录页）。请先在这个窗口扫码登录，"
                  "或用 --browser 换一个已登录的浏览器。", file=sys.stderr)
            return EXIT_LOGIN

        if args.phase == "batch-collect":
            return run_resumable(page, args.run_root,
                                 lambda r: phase_batch_collect(page, args.run_root))
        if args.phase == "batch-apply":
            return run_resumable(page, args.run_root,
                                 lambda r: phase_batch_apply(page, args.run_root,
                                                             args.dry_run, args.allow_low))
        if args.phase == "collect":
            return run_resumable(page, run,
                                 lambda r: phase_collect(page, run, True))
        if args.phase == "apply":
            return run_resumable(page, run,
                                 lambda r: phase_apply(page, run, args.answers, True,
                                                       parse_range(args.only),
                                                       args.dry_run, args.allow_low))
        return run_resumable(page, run, lambda r: phase_report(page, run))
    except CaptchaHit:
        return stop_on_captcha(page, None, run or Path.cwd())
    finally:
        pw.stop()


if __name__ == "__main__":
    sys.exit(main())
