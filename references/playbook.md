# 雨课堂作答 Playbook（脚本细节与排障）

配套 `SKILL.md`。本文件只记录**已跑通**的实现细节，不含历史探索过程。

## 1. 环境与启动

### 1.1 运行时
一台机器上可能有多个 Python，`pip` 与 `python` 未必指向同一个。先确认哪个能 import playwright：
```
python -c "from playwright.sync_api import sync_playwright; print('ok')"
```
本机可用示例：`D:\env\conda\python.exe`（3.8.3 + playwright 1.48.0 预装）。
改完脚本做编译检查：
```
<python> -m py_compile <skill>\scripts\ykt_executor.py <skill>\scripts\browser_env.py <skill>\scripts\bootstrap.py
```

### 1.2 浏览器与调试端口
支持 Chrome / Edge / Brave / Chromium（Chromium 系共用 CDP 协议）。
端口默认 **9333**（9222 被 WorkBuddy 桌面端占用）。

三处差异由 `scripts/browser_env.py` 的 `BROWSERS` 表统一管理：可执行文件路径、用户配置目录、进程名。新增浏览器只需在这张表里加一项。

**关键限制**：Chrome / Edge 136+ 在默认用户目录上会忽略 `--remote-debugging-port`（实测 Chrome 156、Edge 151 均如此），必须用副本配置目录：
```
%LOCALAPPDATA%\Temp\cdp-<browser>-profile      # 如 cdp-chrome-profile / cdp-edge-profile
```
（同系统用户下 Cookie 可解密，登录态保留；实测 Chrome 副本登录态正常）

一键准备：
```
<python> <skill>\scripts\bootstrap.py [--browser edge] [--port 9333] [--no-kill]
```
它做的事：检查 Python → 装 playwright（缺则 pip，失败换清华镜像）→ 探测浏览器 → 关掉目标浏览器 → 先试真实目录、失败改副本目录 → 验证端口。

就绪判定：
```
curl -s --max-time 4 --noproxy "*" http://127.0.0.1:9333/json/version
```

### 1.3 看门狗
```
<python> <skill>\scripts\cdp_watchdog.py [--browser edge] [--port 9333]   # run_in_background
```
- 每 10 秒探测端口；掉了按「上次成功的浏览器 + 配置目录模式」拉起。
- 状态：`%LOCALAPPDATA%\Temp\cdp-browser-state.json`（记录 browser/port/profile）
- 日志：`%LOCALAPPDATA%\Temp\cdp-watchdog.log`
- 会话结束后台任务被回收 → 跨会话保活让用户双击 `start_cdp_browser.cmd`。

### 1.4 代理
本机 `HTTP(S)_PROXY=127.0.0.1:1684` 会把回环请求也转发出去。脚本开头清理代理变量并设 `NO_PROXY=127.0.0.1,localhost`；外部 curl 验证时加 `--noproxy "*"`。

## 2. 数据契约

### 2.1 manifest.json（collect 产出）
```json
{
  "exercise_url": "https://www.yuketang.cn/ai-workspace/lms-graph/<cid>/exercise/<eid>?...",
  "collected_at": "2026-11-02T15:00:00",
  "questions": [{"index": 1, "screenshot": ".../shots/q01.png"}],
  "total": 20
}
```

### 2.2 answers.json（大模型产出，脚本唯一输入）
```json
{
  "answers": [
    {"index": 1,  "type": "单选", "answer": ["B"], "confidence": "high", "reason": "…"},
    {"index": 11, "type": "多选", "answer": ["A","B","C","D"], "confidence": "high", "reason": "…"},
    {"index": 21, "type": "填空", "text": "…"}
  ]
}
```
- `index` 必须覆盖 manifest 全部题号，缺题脚本直接退出、零提交。
- 多选必须给全部正确字母（少选不得分）。
- 判断题用 `type:"判断", answer:["A"]`（A=对 / B=错）。
- 没把握的题标 `confidence:"low"`，不编造。

### 2.3 state.json（apply 产出）
```json
{"done": {"13": {"answer": ["A","B","C"], "score": 6, "correct_answer": "ABC"}},
 "flagged": [{"index": 9, "reason": "select_mismatch"}]}
```
`score == 0` 的题即失分题，report 时一并列出。

### 2.4 scan.json（scan 产出，可选）
```json
{"progress": "完成度 75.00%", "homework_total": 8,
 "finished": ["习题A"], "unfinished": ["习题B"], "unfinished_count": 1,
 "has_unfinished": true}
```

### 2.5 report.json（report 产出）
```json
{"exercise_url": "…", "score": {"got": 95, "full": 100}, "question_count": 20,
 "wrong": [{"index": 17, "correct_answer": "ABCD"}],
 "result_screenshot": "…/result.png"}
```

## 3. 脚本内部要点

| 环节 | 实现 |
|---|---|
| 连接 | `connect_over_cdp("http://127.0.0.1:9333")`，取 `browser.contexts[0]` |
| 找页面 | 已存在含 `exercise/<id>` 的标签页则复用，否则新开 |
| 找题面 | 遍历 `page.frames`，取正文含「提交」或「下一题」的**子 frame**（题面在 iframe 里） |
| 题号 | 题面首行匹配 `^(\d{1,2})\s*[.、．]`；顶部 `X/Y 题` 中 X 是已完成计数、Y 是总题数（决定采集范围） |
| 切题 | 优先点 `.problems-aside` 里的题号数字 `.subject-item.J_order` 直达（1 次点击）；失败才退回「上一题/下一题」翻页 |
| 切题守卫 | 当前题有未提交作答时跳题会弹「刚刚答完的习题还没有提交，需要提交吗？」，`dismiss_guard()` 点「先不用了」后跳转才生效 |
| 选项 | `label.el-checkbox, label.el-radio`，过滤 `width/height>2 && x>0`（滤掉 -10000 诱饵） |
| 选中态 | 元素 class 含 `is-checked` |
| 点选 | toggle 语义：多选只点「该选未选」+「已选不该选」；单选已选则不动 |
| 提交 | 点「提交」→ 页面**自动跳到下一题**，不手动切题 |
| 读分 | 只在 report 阶段读「作业得分 X/Y」；apply 不逐题回翻 |
| 节奏 | 事件驱动：点击后每 250ms 看题号是否变化（下限 0.6 秒），变了立刻继续；20 题 collect 约 30 秒 |
| 翻页 | 兜底路径：`get_by_text("上一题"/"下一题", exact=True)`，跳过禁用态（`closest('.is-disabled,[disabled]')`）与屏幕外诱饵 |
| 截图 | `scope.frame_element().screenshot()`，失败回退整页 |
| 目录扫描 | `.leaf-item` 列表；`.leaf-item-title` 标题、`.leaf-item-tag` 类型、`.leaf-item-status` 内含 `icon-yuanquangou` 即已完成 |
| 成绩单 | 按钮文本是「展开 »」/「收起 «」，用 `find_button_like` 包含匹配；点开后异步渲染，轮询等「作业得分」再读；网格 `.subject-item.primary` 内 `danger` = 错题、`success` = 对；不收起会挡住底部按钮 |
| 答案闸门 | `confidence != high` 默认不提交（`--allow-low` 放行）；`--dry-run` 只点选不提交 |
| 验证码 | 每 300ms 巡检 `#homework-automation-risk-challenge` / `[data-risk-target="challenge"]`；命中后截图并**等待最多 120 秒**，期间每 2 秒轮询是否消失，消失即带 `--resume` 续跑，超时才退出码 2 |

## 4. 端到端命令

```bash
SK="C:/Users/<user>/.workbuddy/skills/yuketang-auto-answer"
PY="D:/env/conda/python.exe"
URL="https://www.yuketang.cn/ai-workspace/lms-graph/<cid>/exercise/<eid>?node_id=...&is_chapter=1"
RUN="<工作目录>/run_<eid>"

# A. 采题（零 LLM）
$PY "$SK/scripts/ykt_executor.py" --phase collect --url "$URL" --run-dir "$RUN"

# B. 大模型读 $RUN/shots/q*.png → 写 $RUN/answers.json

# C. 作答（零 LLM）
$PY "$SK/scripts/ykt_executor.py" --phase apply --run-dir "$RUN" --answers "$RUN/answers.json"

# D. 抓分（零 LLM）
$PY "$SK/scripts/ykt_executor.py" --phase report --run-dir "$RUN"
```

续跑：`--resume`（collect 续采 / apply 跳过已完成）。
只跑部分题：`--only 13-20` 或 `--only 3,7,9`。

## 5. 排障

| 现象 | 原因 | 处置 |
|---|---|---|
| `connect ECONNREFUSED 127.0.0.1:9333` | 浏览器未运行 / 端口被占 / 走了代理 | 跑 `bootstrap.py` 或起看门狗；也可双击 `start_cdp_browser.cmd`；curl 加 `--noproxy "*"` 验证 |
| 端口有响应但不是 CDP JSON | 端口被别的程序占用 | `netstat -ano \| findstr :9333` 换端口 |
| 找不到题面（frame 为空） | 页面还没渲染完 | `wait_frame` 已等 30 秒；仍失败刷新页面 |
| 点「下一题」超时几十秒 | 末题按钮是禁用态 | 已按 `.is-disabled` 跳过，不应再出现 |
| 点不动任何按钮 | 成绩单覆盖层开着，或切题守卫弹窗未关 | 点「收起」/ 点「先不用了」 |
| 多选点了却没选中 | 曾用坐标点 iframe 内元素（缺 offset） | 已改为元素点击，勿回退坐标点击 |
| 多选选错（该选的反被取消） | 未读选中态直接点全部答案 | 已改为差额 toggle，勿回退 |
| 提交后读不到得分 | 提交自动跳到下一题 | 属正常；得分统一在 report 读 |
| 得分 0 且不可重做 | 平台多数作业只允许提交一次 | 如实报告失分题与正确答案 |

## 6. 异常上报

需要发邮件时（用户明确要求并给出邮箱）：Agent Mail，主题含「雨课堂习题异常」，正文含作业名/链接、当前得分、已尝试次数、错误信息，然后暂停任务等待用户。

## 7. 收尾清单

1. 汇总表：作业名 / 题数 / 各题得分 / 失分题与正确答案 / 是否全部 ≥90%。
2. 说明跳过的课堂签到条目。
3. 说明验证码是否中断过、停在哪一题。
4. 需要用户接管时保留浏览器窗口，说明当前停在哪一步。
