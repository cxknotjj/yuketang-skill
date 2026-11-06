# yuketang-skill

雨课堂（yuketang.cn）习题自动作答技能包，供支持 Skill 机制的 AI 助手使用（WorkBuddy / Claude Code 等）。

核心思路：**脚本干机械活，大模型只识别答案。**

```
collect（脚本截图取题） → 识别（大模型出 answers.json） → apply（脚本点选提交） → report（脚本抓总分）
```

一份 20 题作业，大模型调用从 20~40 次降到 **1 次批量识题**，全程约 2 分钟。

> ⚠️ **本项目仅供个人学习自动化技术使用。使用即表示你已知悉并接受[免责声明](#免责声明)，请自行确认符合所在学校与平台的规定。**

## 在哪个页面生效

本技能只作用于雨课堂的**习题/作业作答页**，也就是下面这个界面：

![雨课堂习题作答页](assets/exercise-page.png)

页面结构与本技能的对应关系：

| 页面区域 | 脚本怎么用 |
|---|---|
| 左侧「目录」 | 只用来确认当前在课程/作业列表里，不做任何操作 |
| 中间「展开 »」+ 题号面板 | **按题号点击直达某一题**，这是主要的切题方式（比点「下一题」安全得多） |
| 右侧题干 / 选项 | 截图交给大模型识题；选项按 `label.el-radio / label.el-checkbox` 点选 |
| 底部「上一题 / 提交 / 下一题」 | 仅作兜底翻页；提交后页面会自动跳到下一题 |

不适用于：课程视频播放页、直播页、考试（监考）页、讨论区等其它页面。

## 特性

- **开箱自检**：`bootstrap.py` 自动检查 Python、安装 playwright、挑浏览器、起调试端口
- **多浏览器**：Chrome / Edge / Brave / Chromium 均可（Chromium 系共用 CDP 协议）
- **零 LLM 操作**：切题、点选项、提交、抓分全在本地脚本完成
- **先查后做**：`scan` 阶段读取左侧目录，先告诉你这门课还有哪些习题没做完
- **验证码等待**：不做任何破解，截图后等你手动划完（最多 120 秒），好了自动续跑
- **错题回溯**：report 会解析成绩单，直接告诉你第几题错了、正确答案是什么

不支持 Firefox / Safari —— 它们没有 CDP 接口。

## 快速开始

```bash
# 1. 环境自检与准备（会重启浏览器，未保存标签会丢）
python scripts/bootstrap.py

# 2. 指定浏览器 / 端口（可选）
python scripts/bootstrap.py --browser edge --port 9333
```

看到类似输出即就绪：

```json
{"python":"3.8.3","playwright":true,"browser":"Google Chrome","cdp":"Chrome/156.0.8078.80","port":9333}
```

然后在支持 Skill 的客户端里导入本目录，对助手说：

> 用 yuketang-auto-answer 完成这个作业：https://www.yuketang.cn/ai-workspace/lms-graph/.../exercise/xxxx

## 手动执行五个阶段

```bash
PY=python                                    # 换成能跑 playwright 的解释器
URL="https://www.yuketang.cn/ai-workspace/lms-graph/<cid>/exercise/<eid>?node_id=...&is_chapter=1"
RUN=./run_<eid>

# 0. 先扫一遍：这门课还有哪些习题没做完（零 LLM）
$PY scripts/ykt_executor.py --phase scan --url "$URL"
#    输出：目录完成度 / 作业总数 / 未完成清单，末行一行 JSON 含 has_unfinished

# A. 采题（零 LLM）：读顶部计数器拿总题数，点左侧题号直达逐题截图
$PY scripts/ykt_executor.py --phase collect --url "$URL" --run-dir "$RUN"

# B. 识别：查看 $RUN/shots/q*.png，写出 $RUN/answers.json
#    {"answers":[{"index":1,"type":"单选","answer":["B"],"confidence":"high","reason":"…"}]}

# C. 作答（零 LLM）：点选 + 提交，提交后页面自动跳下一题
$PY scripts/ykt_executor.py --phase apply --run-dir "$RUN" --answers "$RUN/answers.json"

# D. 抓分（零 LLM）：进成绩单读「作业得分 X/Y」
$PY scripts/ykt_executor.py --phase report --run-dir "$RUN"
```

常用参数：

| 参数 | 作用 |
|---|---|
| `--resume` | 断点续跑 |
| `--only 3,7,9` | 只跑指定题 |
| `--entry "<作业名>"` | 在左侧目录点开该作业（同页切换，不重载） |
| `--dry-run` | 只点选不提交，先人工核对 |
| `--allow-low` | 允许提交 `confidence != high` 的题（默认闸门会跳过） |
| `--run-root` | batch 阶段的根目录 |
| `--cdp http://127.0.0.1:9444` | 换调试端口 |

整门课批量：

```bash
$PY scripts/ykt_executor.py --phase batch-collect --url "$URL" --run-root ./runs
# 为每个 runs/run_<id>/ 写 answers.json
$PY scripts/ykt_executor.py --phase batch-apply --run-root ./runs
```

## 目录结构

```
SKILL.md                 技能主文档（执行流程、页面机制、异常处理）
references/playbook.md   数据契约、脚本要点、排障表
scripts/bootstrap.py     环境自检与自动准备
scripts/browser_env.py   浏览器适配层（Chrome/Edge/Brave/Chromium）
scripts/ykt_executor.py  执行器：collect / apply / report
scripts/cdp_watchdog.py  调试端口保活
scripts/start_cdp_browser.cmd  双击启动带调试端口的浏览器
```

## 工作原理要点

| 机制 | 说明 |
|---|---|
| 题面位置 | 在 iframe 内，必须进入对应 frame 操作 |
| 跳题方式 | 点左侧题号面板直达，**不要逐题点「下一题」**（最容易触发人机验证） |
| 选项点击 | toggle 语义，必须先读 `is-checked`，只点差额 |
| 提交 | 提交后自动跳下一题，得分统一在 report 读 |
| 验证码 | 遮罩 `#homework-automation-risk-challenge` 出现即停止并提示人工处理 |
| 调试端口 | 默认 9333；Chrome/Edge 136+ 必须用副本配置目录才能开端口 |

更多细节见 `SKILL.md` 与 `references/playbook.md`。

## 注意事项

- 不代填任何账号密码：需要登录时请在浏览器里手动登录。
- 脚本会关闭并重启浏览器以开启调试端口，注意先保存工作。
- 平台随时可能调整页面结构或风控策略，脚本可能需要相应更新。

## 免责声明

**请在使用前完整阅读以下内容。下载、安装、运行或以任何方式使用本项目，即视为你已阅读、理解并同意本声明全部条款。**

1. **用途限制**
   本项目仅用于个人学习浏览器自动化、Playwright/CDP 技术与 AI Agent 技能编排等**技术研究目的**。严禁将其用于任何形式的作弊、替考、代写、批量刷课、代替他人完成学习任务、商业牟利，或违反所在学校、教育机构规章制度及雨课堂（学堂在线）用户协议的任何行为。

2. **合规责任自负**
   使用自动化工具完成课程作业是否被允许，取决于你所在学校、教师与平台的具体规定。**使用者有责任自行确认并遵守相关规定**。因使用本项目导致的任何后果——包括但不限于成绩作废、作业判零、课程重修、学术处分、账号封禁或限制——**全部由使用者自行承担**。

3. **无官方关联**
   本项目为个人独立开发的开源项目，与雨课堂、学堂在线、清华大学及其关联机构**没有任何隶属、合作或授权关系**，也不代表其立场。相关商标、名称的权利归各自所有者。

4. **数据与隐私**
   本项目所有操作均在**使用者本机的浏览器**中完成，不收集、不上传、不存储任何账号信息、课程数据或作答内容；不包含任何账号密码代填逻辑，登录态由使用者自行在浏览器中维护。仓库中不含任何真实用户数据或个人隐私信息（示例截图已做模糊处理）。

5. **按「现状」提供**
   本项目按 **AS IS** 提供，不附带任何明示或暗示的担保，包括但不限于适销性、特定用途适用性与不侵权的担保。作者不保证其功能正确、运行不中断、答案准确或得分达标，也不保证其始终适配平台页面变化。

6. **责任限制**
   在适用法律允许的最大范围内，作者及贡献者**不对**因使用或无法使用本项目而产生的任何直接、间接、偶然、特殊或后果性损害承担责任，包括但不限于数据丢失、设备损坏、成绩损失、账号损失及时间损失。

7. **使用建议**
   建议先在 `--dry-run`（只点选不提交）模式下核对结果；作业通常不支持重复提交，一旦提交无法撤回，请自行判断是否值得冒险。遇到平台安全核验（验证码）时，本项目会停止并提示人工处理，**不提供任何绕过或破解手段**。

8. **条款变更与终止**
   作者可在任何时候修改本声明或停止维护本项目。若你不同意本声明的任何内容，请立即停止使用并删除本项目全部文件。

## License

MIT
