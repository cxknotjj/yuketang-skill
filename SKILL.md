---
name: yuketang-auto-answer
description: 雨课堂（yuketang.cn）习题/作业自动作答。当用户要求完成雨课堂习题、自动答题、进入作业页作答并提交、读取得分时使用。触发词：雨课堂、yuketang、习题、作业、去完成、自动答题、unfinished。支持 Chrome / Edge / Brave / Chromium 四类浏览器，自带环境自检脚本（自动装 playwright、自动拉起带调试端口的浏览器），首次在陌生电脑上也能直接跑；本地脚本承担全部机械操作，大模型只做一次批量识题。
agent_created: true
---

# 雨课堂习题自动作答

脚本干机械活，大模型只识别答案。20 题作业的大模型调用从 ~20-40 次降到 **1 次批量识题**。

```
scan（看还有哪些没做） → collect（脚本截图取题） → 识别（大模型出 answers.json） → apply（脚本点选提交） → report（脚本抓总分）
```

## 0. 先判断能不能跑

需要三样东西，缺一样都会中途失败：

1. **Python 3.8+**，且能 `import playwright`；
2. **一个 Chromium 系浏览器**（Chrome / Edge / Brave / Chromium 均可）；
3. 该浏览器已登录雨课堂，能以调试端口启动。

第 1 节把它们全部自动化。**不支持 Firefox / Safari**——它们没有 CDP 接口，Playwright 的 `connect_over_cdp` 连不上。

---

## 1. 环境准备（首次必读）

### 1.1 一条命令检查并修好

```
python <skill>/scripts/bootstrap.py
```

依次完成并打印结果：

1. 检查 Python ≥ 3.8；
2. 检查 `playwright`，**缺了自动 `pip install`**（默认源失败自动换清华镜像）；
3. 探测已安装的浏览器（Chrome / Edge / Brave / Chromium）；
4. 关闭选中的浏览器 → 用带调试端口的配置目录重启 → 验证端口。

末行输出 JSON 便于确认：
```json
{"python":"3.8.3","playwright":true,"browser":"Microsoft Edge","cdp":"Edg/151.0.4129.93","port":9333}
```
退出码：`0` 就绪 / `1` 缺依赖或没有浏览器 / `2` 端口或浏览器需人工处理。

**常用参数**

| 参数 | 用途 |
|---|---|
| `--browser edge` | 指定浏览器（`chrome` / `edge` / `brave` / `chromium`） |
| `--port 9444` | 换端口（默认 9333） |
| `--no-kill` | 只检测不动浏览器 |

> ⚠️ 默认会**关掉选中浏览器再重启**（未保存的标签页会丢）。执行前先征得用户同意；只想检测就加 `--no-kill`。

也可让用户双击 `scripts/start_cdp_browser.cmd`（会自动找 Python、调 bootstrap）。

### 1.2 浏览器适配说明

四个浏览器共用同一套 CDP 协议，差别只在三处，脚本已封装在 `scripts/browser_env.py`：

| 浏览器 | 可执行文件 | 用户配置目录 | 进程名 |
|---|---|---|---|
| Chrome | `Program Files\Google\Chrome\Application\chrome.exe` | `%LOCALAPPDATA%\Google\Chrome\User Data` | chrome.exe |
| Edge | `Program Files (x86)\Microsoft\Edge\Application\msedge.exe` | `%LOCALAPPDATA%\Microsoft\Edge\User Data` | msedge.exe |
| Brave | `Program Files\BraveSoftware\Brave-Browser\Application\brave.exe` | `%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data` | brave.exe |
| Chromium | `Program Files\Chromium\Application\chrome.exe` | `%LOCALAPPDATA%\Chromium\User Data` | chromium.exe |

自动选择顺序：有配置目录的 → 已安装的 → 按上表顺序。要指定就加 `--browser`。

**登录态跟着浏览器走**：哪个浏览器登录过雨课堂，就用哪个。若选中的浏览器没登录（打开页面跳到 `/web/?next=...` 登录页），两种处理：
1. 让用户在那个窗口扫码登录一次（副本配置目录会保存，之后一直有效）；
2. 或换用已登录的浏览器：`bootstrap.py --browser chrome`。

**脚本绝不代填账号密码**。看到登录页就停下报告，等用户手动登录。

### 1.3 四个关键点（手动兜底时照这个来）

**① 依赖**
```
python -m pip install playwright      # 只装库，不下载浏览器（复用本机浏览器）
```
没装 Python：`winget install Python.Python.3.12`，或 python.org 下载 3.8+。

**② 端口 9333，别用 9222**
9222 常被 WorkBuddy 桌面端占用。查占用：`netstat -ano | findstr :9333`。

**③ 必须用副本配置目录**
Chrome / Edge 136+ 在**默认用户目录**上会直接忽略 `--remote-debugging-port`，端口永远起不来（实测 Chrome 156、Edge 151 都是如此）。所以复制一份配置目录再用它启动；同系统用户下 Cookie 可正常解密，**登录态保留**：
```
robocopy "%LOCALAPPDATA%\Google\Chrome\User Data"         "%LOCALAPPDATA%\Temp\cdp-chrome-profile"         "Local State"
robocopy "%LOCALAPPDATA%\Google\Chrome\User Data\Default" "%LOCALAPPDATA%\Temp\cdp-chrome-profile\Default" /E /XD Cache "Code Cache" GPUCache ShaderCache "Service Worker" CacheStorage "Extension State"
```
启动（以 Chrome 为例，Edge 把 exe 和目录换成对应的即可）：
```
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --user-data-dir="%LOCALAPPDATA%\Temp\cdp-chrome-profile" ^
  --profile-directory=Default --remote-debugging-port=9333 ^
  --remote-allow-origins=* --no-first-run --no-default-browser-check
```

**④ 代理必须绕过回环**
若本机设了 `HTTP(S)_PROXY`（如 `127.0.0.1:1684`），连 `127.0.0.1:9333` 也会被转发，报 `ECONNREFUSED`。执行器启动时已清理代理变量；手工验证要加 `--noproxy "*"`：
```
curl -s --max-time 4 --noproxy "*" http://127.0.0.1:9333/json/version
```

### 1.4 保活

浏览器被关掉脚本就连不上。后台常驻看门狗，每 10 秒探测、掉了自动按上次成功的浏览器与配置目录模式拉起：
```
python <skill>/scripts/cdp_watchdog.py               # 自动选浏览器
python <skill>/scripts/cdp_watchdog.py --browser edge --port 9333
```
- 日志：`%LOCALAPPDATA%\Temp\cdp-watchdog.log`；状态：`%LOCALAPPDATA%\Temp\cdp-browser-state.json`
- WorkBuddy 会话结束后台任务会被回收；要跨会话保活，让用户双击 `start_cdp_browser.cmd`（可设开机启动）。

### 1.5 环境问题对照表

| 现象 | 原因 | 处置 |
|---|---|---|
| `No module named playwright` | 依赖没装 | 跑 `bootstrap.py`，或 `python -m pip install playwright` |
| 找不到 Chrome/Edge/Brave/Chromium | 没装 Chromium 系浏览器 | 装 Chrome 或 Edge；Firefox/Safari 不支持 |
| `connect ECONNREFUSED 127.0.0.1:9333` | 浏览器没起 / 端口被占 / 走了代理 | 跑 `bootstrap.py`；curl 加 `--noproxy "*"` 复测 |
| 端口有响应但不是 CDP 的 JSON | 被别的程序占用 | `--port 9444`，执行器同步加 `--cdp http://127.0.0.1:9444` |
| 端口起不来但浏览器正常 | 136+ 封了默认目录的调试端口 | 用副本配置目录（1.3 ③），bootstrap 会自动做 |
| 启动几秒后浏览器自己消失 | 进程被调用方回收 | 用看门狗，或让用户双击 `start_cdp_browser.cmd` |
| 打开页面跳到登录页 | 该浏览器没登录雨课堂 | 让用户在该窗口登录一次，或 `--browser` 换成已登录的浏览器 |
| 页面文字全是乱码 | 题面用加密字体渲染 | 正常现象，**读题只信截图**，别用 DOM 文本判题 |
| 非 Windows | 脚本用了 taskkill/robocopy | 手动准备环境与端口，执行器本身跨平台 |

---

## 2. 执行流程

命令里的 `PY` 指能跑 playwright 的 Python（有 conda 环境就用绝对路径，如 `D:\env\conda\python.exe`）。若浏览器不在默认端口，加 `--cdp http://127.0.0.1:<port>`。

### 阶段 0：scan（脚本，零 LLM，可选）

先判断这份课里**还有哪些习题没做完**，避免盲目开工或漏做：

```
PY <skill>/scripts/ykt_executor.py --phase scan --url "<任意作业页或课程页URL>" [--run-dir <run目录>]
```

- 读左侧「目录」里的全部条目，按 `leaf-item-status` 是否含 `icon-yuanquangou` 图标判断完成状态。
- 输出：目录完成度、作业条目总数、已/未完成清单；有 `--run-dir` 时另存 `scan.json`。
- 末行打印一行 JSON，便于程序判断：`{"has_unfinished": true, "unfinished": ["…"], "unfinished_count": 2}`。

判定规则：`has_unfinished=true` 就继续往下做；`false` 说明这门课的作业已经全部完成，直接告诉用户即可。

### 阶段 A：collect（脚本，零 LLM）
```
PY <skill>/scripts/ykt_executor.py --phase collect --url "<习题页URL>" --run-dir <run目录>
```
- 先读顶部计数器 `X/Y 题` 得**总题数 Y**，再按题号 1..Y **点左侧题号直达**逐题截图。
- 不点选项、不提交。中断可加 `--resume` 续采。
- 产物：`manifest.json` + `shots/qNN.png`。

### 阶段 B：识别（唯一的大模型环节）
逐张看 `shots/q*.png`（只信截图），一次写出 `answers.json`：
```json
{"answers":[
  {"index":1,"type":"单选","answer":["B"],"confidence":"high","reason":"…"},
  {"index":4,"type":"多选","answer":["A","B","C","D","E"],"confidence":"high","reason":"…"},
  {"index":9,"type":"填空","text":"…"}
]}
```
规则：单选恰好 1 个字母；**多选必须给全（少选不得分）**；判断 `type:"判断", answer:["A"]`（A=对/B=错）；没把握标 `confidence:"low"`，不编造。

### 阶段 C：apply（脚本，零 LLM）
```
PY <skill>/scripts/ykt_executor.py --phase apply --run-dir <run目录> --answers <run目录>/answers.json
```
- 校验答案是否覆盖全部题号，缺题直接退出、**零提交**。
- 逐题：题号直达 → 读当前选中态 → 只补差额 → 校验一致 → 提交（提交后**自动跳下一题**）。
- 节奏固定 1.5 秒/步，过快会触发人机验证。
- `--only 3,7,9` 只跑指定题；`--resume` 跳过 `state.json` 里已完成的题。

### 阶段 D：report（脚本，零 LLM）
```
PY <skill>/scripts/ykt_executor.py --phase report --run-dir <run目录>
```
- 点「展开」进成绩单 → 读「作业得分 X/Y」「共N题」→ 截图 `result.png` → 点「收起」。
- 产物：`report.json` / `report.txt`。

### 交付给用户
汇总表：作业名 / 题数 / 各题答案 / 总分 / 是否 ≥90%；课堂签到类条目注明已跳过；验证码是否中断过、停在哪题。

---

## 3. 页面机制（改脚本前必读）

| 机制 | 结论 |
|---|---|
| 题面位置 | 在 **iframe** 内（`/v2/web/iframe-exercise/...`），必须进入对应 frame |
| 题号 | 题面首行 `N.` / `N、` / `N．`；顶部 `X/Y 题` 的 X 是已完成计数、**Y 是总题数** |
| 跳题 | **点左侧题号面板**（`.problems-aside` 里的 `.subject-item.J_order`）一次直达；逐题点「下一题」最容易触发验证 |
| 切题守卫 | 当前题有未提交作答时跳题会弹「刚刚答完的习题还没有提交，需要提交吗？」→ 点**「先不用了」**才继续 |
| 选项结构 | 单选 `label.el-radio`、多选 `label.el-checkbox`，**都没有 role 属性** |
| 选项点击 | **toggle 语义**：再点一次会取消。必须先读 `is-checked`，只点「该选未选」和「已选不该选」 |
| 提交 | 点「提交」后**自动跳到下一题**，不用手动切题；得分统一在 report 读 |
| 末题 | 「下一题」是禁用态（`.is-disabled`），必须跳过 |
| 诱饵元素 | 有坐标 `-10000` 的诱饵（`data-risk-decoy-type`），按可见尺寸过滤 |
| 目录/完成状态 | 左侧每个条目是 `.leaf-item`，`.leaf-item-tag` 是「作业/视频」等类型；`.leaf-item-status` 含 `icon-yuanquangou` = 已完成，为空 = 未完成 |
| 成绩单 | 按钮实际文本是「展开 »」「收起 «」，要用**包含匹配**；点开后异步渲染，需轮询等「作业得分」出现；开着时是覆盖层，会挡住底部按钮 |
| 验证码 | 遮罩 `#homework-automation-risk-challenge` / `[data-risk-target="challenge"]`，拦截所有点击 → **停止并上报**。题面文字是加密字体，只能靠 DOM 标记判断 |

---

## 4. 异常处理

| 现象 | 处置 |
|---|---|
| 出现安全核验（验证码） | 脚本截图存证后**退出码 2**；转告用户手动完成，不自动破解 |
| 端口无响应 | 看门狗自动拉起；仍失败让用户双击 `start_cdp_browser.cmd` |
| 页面停在成绩单视图 | 点「收起」 |
| 停在切题守卫弹窗 | 点「先不用了」 |
| 选中态与答案不符 | 脚本不提交该题，记入 `flagged`，继续下一题 |
| 得分 < 90% | 平台多数不允许重做，如实报告失分题与正确答案 |

需要邮件上报时（用户明确要求并给出邮箱）用 Agent Mail，主题含「雨课堂习题异常」，正文含作业名/链接、当前得分、已尝试次数、错误信息。

---

## 5. 文件清单

| 文件 | 作用 |
|---|---|
| `scripts/bootstrap.py` | 环境自检与自动准备（装依赖、挑浏览器、起调试端口） |
| `scripts/browser_env.py` | 浏览器探测/启动/配置目录复制（四浏览器适配层） |
| `scripts/ykt_executor.py` | 执行器：scan / collect / apply / report |
| `scripts/cdp_watchdog.py` | 调试端口保活 |
| `scripts/start_cdp_browser.cmd` | 双击启动带调试端口的浏览器 |
| `references/playbook.md` | 数据契约、脚本内部要点、排障与踩坑记录 |

### 数据契约（速查）
- `manifest.json`：`{exercise_url, questions:[{index, screenshot}], total}`
- `answers.json`：见阶段 B
- `state.json`：`{done:{"3":{answer:[…]}}, flagged:[{index, reason}]}`
- `report.json`：`{score:{got, full}, question_count, result_screenshot}`
