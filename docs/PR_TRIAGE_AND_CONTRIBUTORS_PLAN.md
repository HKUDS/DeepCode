# Open PR 分级处置 & Contributor 计划

调研日期:2026-08-06
基线 commit:`6923382` (`fix(security): refresh audited sidecar dependencies`,2026-08-04)
范围:HKUDS/DeepCode 全部 27 个 open PR
调研方法:把全部 PR head fetch 到本地(`refs/pull/*/head`),逐个 `git merge-tree` 试合并,并对照当前 main 的实际代码验证每个 PR 声称的问题是否仍然存在。**不以 PR 描述为准。**

---

## 0. 决定性的结构事实

判定绝大多数 PR 的分水岭是 v2.0 重构删掉了两个顶层目录:

| 目录 | 状态 | 替代物 |
|---|---|---|
| `nanobot/` | 2026-07-15 `262c0ea` 删除 | `core/`(`core/providers/`、`core/agent_runtime/`) |
| `new_ui/` | 2026-07-17 `5e06553` 重建为 Tauri | `desktop/` + `app_server/`(JSON-RPC over stdio,**已无 HTTP 服务**) |

后果:**多个 PR 的描述仍然成立,但目标文件已经不存在了**。改 `nanobot/` 或 `new_ui/` 的 PR 一律无法机械合并,只能摘取思路重实现。

当前 contributor 8 人(`Zongwei9888`、`LZH-YS1998`、`LarFii`、`chaohuang-ai`、`Jany-M`、`AAG81`、`curator-me`、`clankwright`)。**27 个 open PR 的 17 位人类作者无一在列。**

---

## 1. A 级:该合(5 个)—— 已执行 ✅

> **执行状态(2026-08-06):** 分支 `chore/pr-triage-a-tier`,7 个 commit,**未 push**。
> #139 / #149 / #148 均以 cherry-pick 落地,**原作者的 author 字段完整保留**。
> #122 / #125 未处理 —— 按要求不评论 dependabot。

| commit | author | 内容 |
|---|---|---|
| `8f9999c` | **Osamaali313** | #139 原样 cherry-pick |
| `693c581` | Zongwei9888 | ensure_ascii 回归测试(8 例) |
| `9e4b2c8` | **raymondginger** | #149 原样 cherry-pick |
| `576ed69` | **raymondginger** | #148 rebase + 解冲突 |
| `6bf2f3a` | Zongwei9888 | Windows CI 覆盖修复 |
| `ffbdb3b` | Zongwei9888 | 写保护日志谎报修复 |
| `41784f1` | Zongwei9888 | `windows_sandbox.py` ruff-format |

**验证结果:**
- 全套测试:分支 **1131 passed / 6 skipped / 0 failed**(连跑 2 次一致);干净 main 基线 1119 passed。净增 18 例 = 8(ensure_ascii)+ 4(fences_writes)+ 3(Windows ACL,skip)+ 3(Job Object,skip)。
- 首次运行曾出现 1 例 `test_automation_goal_runs.py::test_legacy_unreserved_turn_is_never_adopted_as_automation_initial_turn` 失败,后两次未复现;该测试用 `threading.Event`/`ThreadPoolExecutor`,属负载敏感的 flaky。已静态确认 #148 的三处新增调用全在 `os.name == "nt"` 分支内、删除的 early-return 也只在 nt 触发 → **POSIX 上严格 no-op**,不可能影响该测试。
- `pre-commit run --from-ref origin/main --to-ref HEAD` 全绿(含 actionlint)。
- #139 回归测试做了反向验证:撤回 fix 后 8 例中 7 例失败。

**仍未验证:** #148 的实际 Windows 行为(3 个用例在 macOS 全 skip)——需 Windows CI,这正是 `6bf2f3a` 的目的。



> 验证环境:conda env `deepcode`,Python 3.13.5,macOS。基线 `tests/test_harness_sandbox.py tests/test_exec_sandbox_wiring.py` = 17 passed。

### #139 — `Osamaali313` — ensure_ascii 配置被反转 ⭐ 最高优先级

**状态:确认为 main 上的活 bug,已实测复现。**

`tools/code_indexer.py` 有 3 处(第 **1131 / 1325 / 1341** 行):

```python
ensure_ascii = not output_config.get("ensure_ascii", False)
```

而 `tools/indexer_config.yaml:100` 明确写的是 `ensure_ascii: false`。`not False` → `True`,于是 `json.dump(..., ensure_ascii=True)`,与配置意图完全相反。

实测(读真实配置文件 + 复刻上述三行):

```
config says ensure_ascii = False
code computes  ensure_ascii = True   <-- inverted

main 实际写出:  {"file": "工作流/代码索引.py", ...}
配置要求的:      {"file": "工作流/代码索引.py", ...}
```

影响:`{repo_name}_index.json`、`indexing_statistics.json`、`indexing_summary.json` 三类产物里所有非 ASCII(中文路径、中文注释、emoji)都被转义成 `\uXXXX`,文件体积膨胀且不可读。同文件第 445 行的 `ensure_ascii=False` 是对的 —— 说明这 3 处确属笔误而非设计。

- 试合并:**CLEAN**,3 行改动(删 3 个 `not`)
- 回归风险:`tests/` 下**无任何测试断言 ensure_ascii 行为**,无回归风险
- 实测:merge 后跑全套 `pytest tests/ -q` → **1119 passed,零失败**(93s)
- CI 覆盖:ubuntu job 跑全套 `pytest -q`,能覆盖
- 归属:`s.osamaali72@gmail.com` → ✅ 正常映射

**动作:直接合。建议补一个断言"配置 false → 输出不转义"的回归测试。**

### #149 — `raymondginger2018-sudo` — Windows Job Object 沙箱后端

**状态:确认为 main 上的真缺口。**

`core/harness/sandbox.py::sandbox_backend()` 当前只有两个分支,Windows 直接落到 `return "none"`;模块 docstring 第 20 行原文即 `Native Windows has no backend.`;`core/harness/` 下没有 `windows_sandbox.py`。

`wrap_argv_command()` 在 `backend == "none"` 时 `return WrappedCommand(argv=list(inner_argv), backend="none")` —— **原样返回,不做任何包裹**。

补充发现(PR 描述里没提,但值得一并修):`describe_backend()` 把 `"none"` 映射成字符串 `"no sandbox (degraded: approval-first)"`,但**代码里没有任何地方真的强制 approval** —— 全仓只有 `tools/code_implementation_server.py:1438,1445` 把这个字符串打日志/塞进返回值。而那条日志是:

```
"Command execution sandbox: %s (writes fenced to workspace)"
```

在 Windows 上会输出 `no sandbox (degraded: approval-first) (writes fenced to workspace)` —— **谎称 writes 被 fence 了**。这条日志文案应当一并修掉。

- 试合并:**CLEAN**(base 只落后 6 个 commit)
- 实测:merge 后 macOS 上 `17 passed, 3 skipped` —— 3 个 Windows 测试正确跳过,**`windows_sandbox.py` 在非 Windows 上 import 安全**(这是我原本担心的点,已排除)
- 质量:文档诚实标注能力边界(明确说明 Job Object 只提供进程树隔离、不伪装 write-fence、不管网络),不吹牛
- 归属:`raymondginger2018@gmail.com` → ✅ 映射到 `raymondginger2018-sudo`

**动作:review 后合。**

### #148 — `raymondginger2018-sudo` — Windows 私有文件 ACL

**状态:确认为 main 上的真缺口。**

`core/private_storage.py` 的每一条 Windows 路径都是显式 no-op:

| 位置 | Windows 行为 |
|---|---|
| `_chmod()` | `if os.name == "nt": return` |
| `open_private_file()` | `if os.name != "nt": os.fchmod(...)` → 跳过 |
| `open_existing_private_file()` | 同上 |
| `harden_private_tree()` | `if os.name == "nt": return base` → 立即 bail |

模块 docstring 自己承认 "Windows access control is inherited from the user's profile"。而 profile ACL 通常给 `Authenticated Users` = Modify,所以 `credentials.json` 在 Windows 上拿不到任何 `0600` 等价保护。PR 用 `icacls /inheritance:r` + `/grant:r <user>:F` 修复,best-effort 失败静默,与 POSIX chmod 的语义一致。

- 试合并:**CONFLICT,但极其平凡** —— 唯一冲突是 `PRIVATE_FILE_MODE = 0o600` 之后 main 的 `UnsafePrivateFileError` 和 PR 的 `_windows_identity()` 插在同一位置。解法:**两边都保留**。
- 实测:按上述解法解冲突后 `import core.private_storage` OK,`__all__` 8 项不变(新 helper 是 `_` 私有,正确)
- ⚠️ **本机无法验证**:`tests/test_private_storage_windows.py` 96 行全是 Windows-only,macOS 上 `3 skipped`,零信号

**动作:rebase 后合,但必须在 Windows 上跑测试(见 §5 CI 缺口)。**

### #122 / #125 — `dependabot[bot]` — 依赖下界提升

`requirements.txt` 是权威依赖源(`setup.py::read_requirements()` 读它;`pyproject.toml` 无 `dependencies` 段)。

| PR | 现状 | PR 目标 | PyPI 最新 | 判定 |
|---|---|---|---|---|
| #122 | `PyYAML>=6.0` | `>=6.0.3` | 6.0.3 | 仍适用 |
| #125 | `aiofiles>=0.8.0` | `>=25.1.0` | 25.1.0 | 仍适用 |

**注意:这两个都是 `>=` 下界,pip 本来就会装最新版,所以实际影响很小** —— 只是防止有人钉住旧版本。不是 bug。

**动作:评论 `@dependabot rebase` 让它自己重开,不手工合。**

---

## 2. B 级:PR 不能合,但内容可摘(6 个)

| PR | 作者 | 可摘部分 | 处置 |
|---|---|---|---|
| **#138** | `Thibaultjaigu` | Requesty provider。`core/providers/registry.py`(+11 行 `ProviderSpec`)、`core/providers/openai_compat.py`(attribution headers)、`core/config.py`(+1 行)、`tests/test_requesty_provider.py`(128 行)**均直接适用当前 main**,写法与现有 OpenRouter 完全对称。仅 `new_ui/` 那 265 行是死的。 | cherry-pick core 部分,`Co-authored-by` |
| **#130** | `octo-patch` | MiniMax provider。`core/config.py` + `core/providers/registry.py` 共 9 行 + `tests/minimax_provider_test.py` 160 行,直接可用。`nanobot/` 部分丢弃。 | cherry-pick core 部分。**⚠️ 归属需先解决,见 §3** |
| **#143** | `raymondginger2018-sudo` | 纯 Python filesystem MCP server(952 行,26 工具,零依赖),替代 `npx @modelcontextprotocol/server-filesystem`。main 确实**没有**自带 filesystem MCP(只有 `cli/mcp_server.py`),且 `core/platform_compat.py:23` 那套 `_WINDOWS_SHELL_LAUNCHERS = {"npx","npm",...}` 兼容代码正说明 npx 是真痛点。试合并 **CLEAN**。 | **独立走 review,不要直接拒。**952 行文件系统工具需一次专门的安全 review |
| #116 | `Yiiii0` | Forge provider,只改 `nanobot/`。思路移植到 `core/providers/registry.py` 约 8 行。 | 重实现 + `Co-authored-by` |
| #131 | `yuefengw` | 结构化 workflow 错误上报到 UI。13 文件全在 `new_ui/`(已死),但**思路对** —— `app_server`/`desktop` 同样需要结构化错误传播。等于重写。 | 重实现 + `Co-authored-by`,或转成 issue |
| #49 | `VinodHatti-AI-Developer` | `logs/`、`deepcode_lab/` main 已有;只剩 `mcp_agent.secrets.yaml` 一行仍有价值(`schema/mcp-agent.config.schema.json` 还在)。 | 摘那 1 行 |

---

## 3. Contributor 归属 —— merge ≠ 拿到 contributor 身份

GitHub contributor graph 认的是 **commit author email → 账号映射**。实测各 PR 的真实归属:

| PR | commit author | 实际映射到 |
|---|---|---|
| #141 / #146 | `DeepCode <deepcode@deepcode.ai>` | 🔴 **`DeepCodeClone`** —— **不是** `raymondginger2018-sudo` |
| #130 | `octo-patch <octo-patch@github.com>` | 🔴 **完全未归属**(合了等于谁都没记上) |
| #143 / #144 / #148 / #149 | `raymondginger2018@gmail.com` | ✅ `raymondginger2018-sudo` |
| 其余 14 位 | gmail / qq / `users.noreply.github.com` | ✅ 均正常映射 |

**所以照现状合并 #141/#146,功劳记到 `DeepCodeClone` 头上;合并 #130,谁都不记。** 这两处必须改 author 或加 `Co-authored-by:` trailer。

### 两条独立机制

1. **git contributor graph** —— 需要 author 或 `Co-authored-by:` 邮箱能映射到其账号。关键:**不合他们的代码也行** —— 自己写移植/修复,commit 末尾加 `Co-authored-by: Name <已绑定邮箱>`,GitHub 会把 co-author 计入 contributors 图。前提是邮箱已绑定其账号。
   - 依据:[GitHub Docs — Creating a commit with multiple authors](https://docs.github.com/en/pull-requests/committing-changes-to-your-project/creating-and-editing-commits/creating-a-commit-with-multiple-authors)、[GitHub Blog — Commit together with co-authors](https://github.blog/news-insights/product-news/commit-together-with-co-authors/)
2. **显式致谢名录** —— `CONTRIBUTORS.md` / README 致谢 / all-contributors bot。对 17/17 全部有效,不依赖代码。**仓库目前没有任何 CONTRIBUTORS/AUTHORS 文件**,是个干净的新增点。

### 覆盖率结论

**17 位人类作者中 14 位可以拿到货真价实的 git contributor 身份:**

- A 级直接合:`Osamaali313`、`raymondginger2018-sudo`
- B 级摘取/重实现挂 co-author:`Thibaultjaigu`、`octo-patch`(需先拿绑定邮箱)、`Yiiii0`、`yuefengw`、`VinodHatti-AI-Developer`
- D 级里 `mrluanma` 的 WebSearchTool 可按 env-var-only 重写后挂 co-author
- C 级 9 位虽无 commit 可落,但当年确实发现了真问题 → CONTRIBUTORS.md + release notes 记为"独立发现 / 先行修复"

**剩下 3 位没有任何可用内容**:`User1234608`(#20 玩具 registry)、`lindomar8`(#21 删 docstring)、`PacoMortal`(#23 README 涂鸦)。

> 让这 3 位进 contributor graph,只能凭空造一个 commit 挂他们名字 —— **那是伪造归属,不建议**。诚实做法:走机制 2 的名录,或回帖邀请他们提一个真实的小改动(如 #49 剩下那行 gitignore、补一个测试)。
>
> **待定:此项需 owner 决策。**

---

## 4. C 级:已过时,礼貌关闭(9 个)

这些**当时都是真问题,后来被独立修掉了** —— 关闭时务必说清这一点并给出证据 commit,有依据的关闭不会让人不爽。

| PR | 作者 | 过时原因(已验证) |
|---|---|---|
| #14 | `muhammadibrahim313` | f-string 反斜杠;那一行已不在 main 上 |
| #27 | `Mirza-Samad-Ahmed-Baig` | 命令注入修复;main 上 `os.system` **已全部清零**,`cleanup_cache` 函数本身已删 |
| #48 | `dingdinglz` | main 已是 `shutil.copy2`(第 1020 行),并**正确地没有采纳** PR 里的 `chmod 0o777` |
| #46 | `brucezo` | `openai>=1.55.0` 已在 requirements.txt |
| #119 | `crsmillan` | main `code_implementation_workflow.py:58` 已有该 import |
| #135 | `AAtomical` | 修 `new_ui/backend/main.py` 路径穿越;该文件已删,**main 上已完全没有静态文件服务**(全仓无 `StaticFiles`/`FileResponse`) |
| #121 | `dependabot` | 要求 aiohttp≥3.13.5,main 已是 ≥3.14.1(更高) |
| #123 / #124 | `dependabot` | websockets / uvicorn 已从 requirements.txt 移除 |

---

## 5. D 级:不该合(7 个)

| PR | 作者 | 阻塞原因 |
|---|---|---|
| #23 | `PacoMortal` | README 第一行插入 `//contribucion desde mi fork =)`。试水/涂鸦 |
| #21 | `lindomar8` | **纯删除 docstring** 并弄坏缩进。纯回退,且目标文件已不存在 |
| #20 | `User1234608` | 51 行玩具 `ToolRegistry`,自带 `hello_world` 示例。main 已有 `core/agent_runtime/tools/registry.py` |
| **#133** | `mrluanma` | 🔴 **硬编码真实 Metaso API key** 作为 fallback 默认值(`mk-E384C1DD…`,`nanobot/nanobot/agent/tools/web.py`)。会被 gitleaks/security-ci 拦下。且只改 `nanobot/`。**该 key 已在公开 PR 里暴露 → 应私下通知作者轮换。** WebSearchTool 思路本身可取,按只读 env var 重实现 |
| **#141** | `raymondginger2018-sudo` | 🔴 引入 git 子模块 `deepcode-engine-mcp`(gitlink mode `160000`,commit `6823ef0`)**但没有 `.gitmodules`** → 破坏 clone,且指向外部/私有仓库。另有 4 个 "bump submodule" commit。其 `filesystem_mcp_server.py` 与 #143 **blob 完全相同**(`3a7a328`),二者重复。归属也错(→ `DeepCodeClone`) |
| **#144** | `raymondginger2018-sudo` | 🔴 `.mcp.json` **实际是个 tar 归档**(`file` 判定 POSIX tar,内含一个同名 4421 字节 `.mcp.json`),不是 JSON。还提交了 `.deepcode/settings.local.json`(本地配置)。5751 行,在 `.deepcode/skills/` 下另建一套与 main 已发布的 `core/skills/` 平行冲突的 skills 体系 |
| #146 | `raymondginger2018-sudo` | 3306 行,同样是 `.deepcode/skills/` 平行体系,与 main 的 `core/skills/`(catalog/runtime/management + desktop UI + 4 套测试)架构冲突。作者自述 #142 曾误提交 SQLite/JSONL 运行时文件 —— 重复的卫生问题。归属也错(→ `DeepCodeClone`) |

---

## 6. 顺带发现的 CI 缺口(阻塞 #148/#149 的验证)

`.github/workflows/python-ci.yml`:

| job | runs-on | 范围 |
|---|---|---|
| test | `ubuntu-latest` | **全套** `pytest -q` |
| `windows-lifecycle` | `windows-2022` | ⚠️ **只跑 4 个指定文件**:`test_application_lease.py`、`test_automation_scheduler_leadership.py`、`test_session_deletion_service.py`、`test_execution_coordinator.py` |

`security-ci.yml` 三个 job 全是 `ubuntu-24.04`。

**后果:合了 #148/#149,它们的 Windows-only 测试在 CI 里永远不会真正执行**(ubuntu 上全部 skip)。

**动作:合 #148/#149 的同时,把 `tests/test_private_storage_windows.py` 和 `tests/test_harness_sandbox.py` 加进 `windows-lifecycle` job 的文件列表。** 否则这两个 PR 等于零验证覆盖。

---

## 7. 执行计划

### Step 1 — A 级落地 ✅ 代码已完成,待 push

- [x] #139 cherry-pick(`8f9999c`,author = Osamaali313)
- [x] ensure_ascii 回归测试 8 例(`693c581`),反向验证:撤回 fix 后 7/8 失败
- [x] #149 cherry-pick(`9e4b2c8`,author = raymondginger)
- [x] #148 rebase + 解冲突(`576ed69`,author = raymondginger)
- [x] 扩 `windows-lifecycle` job 测试列表(`6bf2f3a`)
- [x] 修谎称 writes fenced 的日志(`ffbdb3b`)—— 决定**保留**,理由见 §9
- [x] `windows_sandbox.py` ruff-format(`41784f1`)
- [ ] **push 分支 → 开 PR → 等 CI**(`windows-2022` job 是 #148 唯一的真实验证场)
- [ ] CI 绿后合 main
- [ ] 手动关闭 PR #139/#148/#149(cherry-pick 后 SHA 变了,GitHub 不会自动标 merged)
- ~~#122 / #125 评论 `@dependabot rebase`~~ —— 按 owner 要求不评论

### Step 2 — 两个对外请求(阻塞项,与 Step 1 并行发出)

- [ ] 问 `octo-patch` 要一个已绑定 GitHub 的邮箱 —— **不解决则 #130 做了也不计入 contributor**
- [ ] 通知 `mrluanma` 轮换 Metaso key —— 独立的安全动作,与合不合 #133 无关

### Step 3 — 建 `CONTRIBUTORS.md`

**这是 17/17 覆盖的唯一机制**(git 图天花板只有 8/17,见 §3)。与所有代码工作解耦,随时可做。

### Step 4 — B 级第一批:#138 + #116 + #49

低风险纯增量,再添 3 位。#138 的 `core/` + `tests/` 部分实测 `git apply --check` 通过(208 行)。

### Step 5 — C 级 9 个 PR 批量关闭

每条附"main 上现在是什么样 + 在哪个 commit 被修掉"。配合 §3 的名录,比让它们继续挂着更像认真对待贡献者。

### 往后排

- **#143** 单独排一次安全 review(952 行文件系统工具,整体 apply 干净,需审路径穿越/符号链接/写越界)
- **#131** 决定重写还是转 issue —— 取决于 `app_server`/`desktop` 的错误处理是否当前痛点
- **#141/#144/#146** 要求作者清理(submodule / tar 伪装的 `.mcp.json` / 本地配置),说明与 `core/skills/` 的架构冲突
- **#130** 待邮箱到位后摘取
- **#133** 按只读 env var 重实现 WebSearchTool

---

## 8. 可达性核验 —— A 级改动对当前 main 是否有意义

前提:`release-desktop.yml` 有 `Windows x64 / windows-2022 / bundles: nsis` + 代码签名 → **Windows 是正式出货平台**。

| 改动 | 活性 | 证据 |
|---|---|---|
| **#148** private_storage | ✅ 最高 | **12 处调用**全在新架构核心:`core/providers/credentials.py`(API key)、`core/sessions/store.py`、`core/persistence/database.py`、`core/application/config_store.py`、`core/file_lock.py`、`cli/` |
| **#149** sandbox | ✅ 高 | `core/harness/tools/shell.py:99`、`core/harness/code_mode/tool.py:153` —— 活的 agent shell/code 工具。Windows 上现在是 `backend="none"`,零隔离 |
| **#139** code_indexer | ✅ 有效但窄 | 活链路:`core/application/application.py` → `workflow_adapter` → `agent_orchestration_engine:1511` → `codebase_index_workflow` → `load_or_create_indexer_config()` **显式加载 `tools/indexer_config.yaml`**(带 `ensure_ascii: false`)→ `generate_summary_report()`(第 585 行)。影响是产出 JSON 里中文被转义,可读性问题,非正确性/安全问题 |
| `6bf2f3a` CI | ✅ | 前两项的唯一验证入口 |
| `ffbdb3b` 日志 | ⚠️ 弱 | 唯一消费者 `tools/code_implementation_server.py` 在 `core/`/`cli/`/`app_server/`/`desktop/`/`workflows/` 中**零引用**,`tools/command_executor.py` 同样 —— 孤立的独立 MCP server 脚本,仓库无默认配置启动它 |

## 9. `ffbdb3b` 的取舍

**决定:保留。** 它确实打在休眠文件上,但修的是一条"谎称有写保护"的假安全声明;代码已写完且有 4 个测试,摘除反而要额外动手。价值低 ≠ 该删。

## 10. 本次调研未覆盖的部分

- **未逐行 review #143 那 952 行** filesystem MCP 实现 —— 它是文件系统工具,值得一次独立安全 review。
- **#148 的 Windows 行为未实测** —— 手上只有 macOS,其 96 行测试全 skip。需 Windows 机器或 CI(见 §6)。
- **未验证 B 级 cherry-pick 后的测试结果** —— 只确认了 diff 可移植,没实际 apply 跑测试。
- 环境无 `gh` CLI 也无 token,用的是公开 REST API + 本地 git。实际执行合并/回帖需先 `brew install gh && gh auth login`。
