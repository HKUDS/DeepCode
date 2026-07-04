# P1 · 安全底座(第一增量)

实现 DEEPCODE_V2_MASTER_PLAN.md §4.3 的安全层——计划标注"现在裸奔,最高优先级"。**机制,非决策**:这些模块只做确定性的判定与包裹,不碰模型、不碰 UI(军规 5/7)。

## 交付

| 组件 | 文件 | 作用 |
|------|------|------|
| 三值权限引擎 | `core/harness/permissions.py` | `allow / ask / deny` 纯函数决策 |
| 平台沙箱库 | `core/harness/sandbox.py` | seatbelt/bwrap 命令包裹 + 降级 |
| 内核权限接缝 | `core/agent_runtime/runner.py` | `permission_checker` / `approval_callback`;拒绝=错误即数据回喂 |
| 实现工作流接线 | `workflows/code_implementation_workflow.py` | FULL_AUTO 模式 + 硬黑名单 |

## 权限引擎(提炼自参考项目,非照抄)

- **不可覆盖的敏感路径黑名单**(源自 OpenHarness):`.ssh / .aws/credentials / .env / deepcode_config.json / *.pem / id_rsa*` 等——**任何规则、任何模式都无法解除**,这是防提示注入的最后防线。评估优先级第一位。
- **二维通配符规则,特异性优先 + last-match-wins**(源自 opencode + 改进):规则同时匹配工具名 × 参数(命令行/路径),`{"execute_bash": {"git push *": "ask", "*": "allow"}}` 按作者直觉工作(具体压过通配)。直接构造的 `PermissionRule` 列表保持纯 last-match-wins。
- **三模式**:`default`(改动型工具 ask)/ `plan`(改动型 deny,只读探索)/ `full_auto`(仅规则,无隐式 ask,供无人值守工作流)。

## 沙箱库

- **macOS seatbelt**:生成 `.sbpl`——读全放行、写仅限工作区(+ /tmp、/private/var/folders 供构建)、默认断网;`sandbox-exec` 用绝对路径调用防 PATH 注入。**已有真实强制执行测试**:区内写成功、区外写被拦、区外读放行。
- **Linux bwrap**:只读绑定根、读写绑定工作区、fresh /tmp、`--unshare-net`。
- **降级**:不可用平台(原生 Windows 等)`sandbox_backend()` 返回 `none`,包裹退化为裸命令并打标——调用方据此转审批优先(接线阶段处理)。

## 内核接缝(错误即数据)

`AgentRunSpec.permission_checker` 在每次工具执行前调用:
- `allow` → 放行;
- `deny` → 变成 `Error: permission denied: <reason>` 的**工具结果**回喂模型(军规 3,不崩溃、不中断);
- `ask` → 有 `approval_callback` 则问(批准放行/拒绝回喂),无(无人值守)则默认拒绝并解释——**自主性永不静默升级**。

fail-closed:checker 或 approver 抛异常 → 一律拒绝。

## 行为影响

实现阶段(FULL_AUTO):**与 P0 完全一致,唯一变化**——agent 再也读不到/写不了凭据库,即便计划或模型要求。回归验证:paper9 全流程行为无回退。

## 增量 b(已完成):沙箱接线 + 交互式审批

**b1 · execute_bash/python 真正经沙箱运行**(`tools/code_implementation_server.py`、`tools/command_executor.py`)
- 两个 MCP server 的 `execute_bash`/`execute_python`(及文件树创建的命令回退)现在经 `core.harness.sandbox.build_exec_command` 包裹:**写围栏到 workspace,读全放行,网络放行**(pip/下载不受影响)。
- 环境门控 `DEEPCODE_SANDBOX`(默认开;`0/false/off/no` 关闭);后端不可用时降级为裸执行并在结果 `sandbox` 字段标注(`seatbelt`/`bwrap`/`none`/`disabled`)。
- **闭合上一节坦白的 shell 凭据边界**:shell 命令把凭据 copy 到自控文件 → 写围栏拦截。已有真实 seatbelt 测试证明(`test_execute_bash_credential_exfil_write_blocked`、区外写拦截、区内写放行、python 同款)。
- 权衡说明:沙箱选择**放行网络**(实现阶段常需 pip),FS 写围栏是高价值低风险的那层保护;要严格隔离可用 `allow_network=False`(库支持)。

**b2 · 交互式审批(ask → 人机确认)**(`core/harness/approval.py`)
- `TerminalApprover`:把 `ask` 变成终端 y/n/always 提示,`always` 按工具名记忆本会话;非交互 stdin → 拒绝(fail-closed);`as_async()` 用线程包裹避免阻塞事件循环。
- **altitude 决策**:自主实现阶段逐工具确认是糟糕 UX,故**默认仍 FULL_AUTO**;`DEEPCODE_PERMISSION_MODE=default|plan` 可主动切换,此时自动挂上审批器。真正"交互式默认"属于 P2 通用 agent 循环。

## 仍待接线(后续)

- 从 `deepcode_config.json` 读 `permissions` 规则块(`rules_from_config` 已就绪,尚未接入配置加载);
- 通用 agent 循环(P2)默认 `default` 模式 + 审批。
