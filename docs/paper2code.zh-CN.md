# Paper2Code

Paper2Code 是 DeepCode 的起点：一个把论文或技术文档变成可运行代码库的多智能体
工作流。DeepCode 现在已经发展为通用的 coding agent，Paper2Code 作为其中一个
专门工作流继续保留。

方法见论文 [DeepCode: Open Agentic Coding](https://arxiv.org/abs/2512.07921)。
English: [paper2code.md](paper2code.md).

## 运行 Paper2Code

Paper2Code 在 Desktop 和 Web 客户端中运行：

1. 启动 `deepcode desktop` 或 `deepcode web`，打开一个已信任的项目。
2. 点击会话标题栏的 **Paper**（中文界面为"论文"），或在输入框输入 `/paper`，新建一个 Paper2Code 会话。
3. 添加来源：文档、URL、代码仓库或文字需求。
4. 工作流请你审阅计划时进行确认，然后查看各阶段进度。生成的文件和产物显示在
   右侧检查器里。

原来运行旧 Paper2Code 脚本的 `deepcode test` 命令已经移除。工作流的运行时设计见
[P5 Paper2Code architecture](P5_PAPER2CODE_ARCHITECTURE.md)。

## 工作方式

论文复现不是一次性代码生成任务。一个中央编排智能体负责协调多个边界清晰的专业
角色，依次完成来源理解、复现规划、参考发现与索引、代码实现以及结果验证。

<p align="center">
  <img src="../assets/readme/framework2.png" alt="Paper2Code 从来源文档到代码生成、验证与改进的完整框架" width="1080" />
</p>

各专业角色继续保持原系统中清晰的职责分离：

| 角色                   | 职责                                                                                 |
| ---------------------- | ------------------------------------------------------------------------------------ |
| **中央编排智能体**     | 判断整体进度、选择下一阶段、协调专业角色，并在新证据出现时调整计划。                 |
| **意图理解智能体**     | 将用户目标转化为明确的功能需求、技术约束与可执行的任务分解。                         |
| **文档解析智能体**     | 处理论文与技术文档，提取算法、公式、方法、假设和实现要求。                           |
| **代码规划智能体**     | 把已理解的方法转换为实现路线、模块边界、依赖、接口与验证目标。                       |
| **代码参考挖掘智能体** | 发现相关仓库、库与实现模式，并判断它们的相关性、兼容性和集成价值。                   |
| **代码索引智能体**     | 将检索到的代码组织为可搜索的语义索引与知识图谱，使生成阶段可以恢复关键组件及其关系。 |
| **代码生成智能体**     | 综合计划与证据形成可执行实现，并生成可复现结果所需的接口、测试和文档。               |

四个核心思想把这些角色连接为一个整体：

- **智能编排。** 中央 Agent 根据任务状态选择并回访不同阶段，而不是把论文复现
  当作固定的一次性 Prompt 链。
- **文档与意图对齐。** 论文、规格、URL 与附件会先转化为明确实现要求，再进入
  编码阶段。
- **记忆与 CodeRAG。** 长文档与参考仓库经过分段、索引和按需检索，以有边界的
  上下文进入模型，而不是反复塞入整个窗口。
- **迭代验证。** 执行、测试与真实失败会回流到规划和实现阶段，直到交付结果
  拥有可以检查的证据。

配套工具层同样延续这一职责划分：

| 层次         | 作用                                                      |
| ------------ | --------------------------------------------------------- |
| 文档导入     | 获取并规范化论文、URL、PDF、DOCX、演示文稿、文本与 HTML。 |
| 文档分段     | 将大型技术材料切分为语义连贯、可恢复的分析单元。          |
| 参考发现     | 搜索候选仓库与支持性实现。                                |
| 代码参考索引 | 为外部与本地代码建立可搜索上下文，并保留跨文件关系。      |
| 实现执行     | 读写文件、运行 Shell 或 Python、检查项目结构并记录过程。  |
| 验证与交付   | 运行测试、保存结果，并交付代码库、文档与 Artifacts。      |

新版产品围绕这套流程增加了持久化计划、显式计划审查、检查点、有边界重试与
交互式检查能力。这些变化增强了恢复和监督，但没有改变 Paper2Code 的架构
职责与推理顺序。

## 研究结果

DeepCode 原始研究使用
[PaperBench](https://openai.com/index/paperbench/) 评估科研代码复现能力。
该基准要求 Agent 复现 20 篇 ICML 2024 论文，共包含 8,316 个可评分组件。

<table align="center" width="100%">
<tr>
<td width="25%" align="center">
  <strong>75.9%</strong><br/>
  <sub>人类专家子集<br/>领先 3.5 个百分点</sub>
</td>
<td width="25%" align="center">
  <strong>84.8%</strong><br/>
  <sub>商业 Agent 子集<br/>领先 26.1 个百分点</sub>
</td>
<td width="25%" align="center">
  <strong>73.5%</strong><br/>
  <sub>科研编程<br/>领先 22.4 个百分点</sub>
</td>
<td width="25%" align="center">
  <strong>73.5%</strong><br/>
  <sub>LLM Agent 基线<br/>领先 30.2 个百分点</sub>
</td>
</tr>
</table>

<p align="center">
  <img src="../assets/result_main02.jpg" alt="DeepCode PaperBench 结果" width="920" />
</p>

| 评估子集        | DeepCode | 论文中报告的对比结果        | 差值           |
| --------------- | -------- | --------------------------- | -------------- |
| 人类专家子集    | 75.9%    | 论文中最佳人类基线：72.4%   | +3.5 个百分点  |
| 商业 Agent 子集 | 84.8%    | 论文中最佳商业 Agent：58.7% | +26.1 个百分点 |
| 科研编程        | 73.5%    | PaperCoder：51.1%           | +22.4 个百分点 |
| LLM Agent 基线  | 73.5%    | 论文中最佳 LLM Agent：43.3% | +30.2 个百分点 |

以上是原论文报告的 PaperBench 专项结果，不代表通用编程基准，也不代表对
持续更新中的商业产品进行实时比较。

评估方法、范围、模型和基线详情请阅读
[论文](https://arxiv.org/abs/2512.07921)。

## 早期演示

下面的录像展示了早期 DeepCode 工作流生成的项目结果。它们是输出案例,不是当前
界面的截图。

<table align="center" width="100%">
<tr>
<td width="33%" align="center">

**Paper2Code**

**从研究到实现**

<a href="https://www.youtube.com/watch?v=MQZYpLkzsbw">
  <img src="https://img.youtube.com/vi/MQZYpLkzsbw/maxresdefault.jpg" alt="Paper2Code 演示" width="100%" />
</a>

**[▶ 观看演示](https://www.youtube.com/watch?v=MQZYpLkzsbw)**

<sub>将研究论文复现为可以执行的工程项目。</sub>

</td>
<td width="33%" align="center">

**生成式视觉项目**

**图像工作流案例**

<a href="https://www.youtube.com/watch?v=nFt5mLaMEac">
  <img src="https://img.youtube.com/vi/nFt5mLaMEac/maxresdefault.jpg" alt="生成的图像处理项目" width="100%" />
</a>

**[▶ 观看演示](https://www.youtube.com/watch?v=nFt5mLaMEac)**

<sub>查看早期 DeepCode 生成的图像处理工作流。</sub>

</td>
<td width="33%" align="center">

**生成式 Web 项目**

**前端实现案例**

<a href="https://www.youtube.com/watch?v=78wx3dkTaAU">
  <img src="https://img.youtube.com/vi/78wx3dkTaAU/maxresdefault.jpg" alt="生成的前端项目" width="100%" />
</a>

**[▶ 观看演示](https://www.youtube.com/watch?v=78wx3dkTaAU)**

<sub>从想法开始，查看完整前端项目的实现结果。</sub>

</td>
</tr>
</table>
