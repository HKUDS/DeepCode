# User-in-Loop 插件系统使用指南

## 概述

这是一个插件式的用户交互系统，可以像中间件一样无侵入地插入到工作流中。

## 核心概念

```
工作流执行:  [Phase 1] ──▶ [Hook Point] ──▶ [Phase 2] ──▶ [Hook Point] ──▶ [Phase 3]
                              │                              │
                              ▼                              ▼
                         [Plugin A]                     [Plugin B]
                         需求分析                        计划确认
```

## 快速开始

### 1. 在 workflow_service.py 中添加插件支持

```python
# workflow_service.py

from workflows.plugins.integration import WorkflowPluginIntegration
from workflows.plugins import InteractionPoint

class WorkflowService:
    def __init__(self):
        self._tasks = {}
        self._subscribers = {}
        # 添加这一行
        self._plugin_integration = WorkflowPluginIntegration(self)

    async def execute_chat_planning(self, task_id, requirements, enable_indexing=False):
        # ... 原有代码 ...

        # ===== 添加插件支持 (仅需3行代码) =====

        # 1. 创建上下文
        context = self._plugin_integration.create_context(
            task_id=task_id,
            user_input=requirements,
            enable_indexing=enable_indexing,
        )

        # 2. 运行 BEFORE_PLANNING 插件 (需求分析)
        context = await self._plugin_integration.run_hook(
            InteractionPoint.BEFORE_PLANNING,
            context
        )

        # 检查是否被取消
        if context.get("workflow_cancelled"):
            return {"status": "cancelled", "reason": context.get("cancel_reason")}

        # 使用可能被增强的需求
        requirements = context.get("requirements", requirements)

        # ===== 原有的计划生成代码 =====
        planning_result = await run_chat_planning_agent(requirements, logger)

        # ===== 添加计划确认插件 =====
        context["planning_result"] = planning_result
        context = await self._plugin_integration.run_hook(
            InteractionPoint.AFTER_PLANNING,
            context
        )

        if context.get("workflow_cancelled"):
            return {"status": "cancelled", "reason": context.get("cancel_reason")}

        # 使用可能被修改的计划
        planning_result = context.get("planning_result", planning_result)

        # ===== 继续原有的代码实现流程 =====
        ...
```

### 2. 添加用户响应 API

```python
# workflows.py (API routes)

@router.post("/respond/{task_id}")
async def respond_to_interaction(task_id: str, response: InteractionResponseRequest):
    """用户提交交互响应"""
    success = workflow_service._plugin_integration.submit_response(
        task_id=task_id,
        action=response.action,
        data=response.data,
        skipped=response.skipped,
    )

    if not success:
        raise HTTPException(status_code=404, detail="No pending interaction")

    return {"status": "ok"}
```

### 3. 前端处理交互请求

```typescript
// useStreaming.ts

case 'interaction_required':
  // 显示交互面板
  setInteraction({
    type: message.interaction_type,
    title: message.title,
    description: message.description,
    data: message.data,
    options: message.options,
  });
  break;
```

## 配置插件

### 启用/禁用插件

```python
from workflows.plugins import get_default_registry

registry = get_default_registry()

# 禁用需求分析插件
registry.disable("requirement_analysis")

# 启用计划确认插件
registry.enable("plan_review")
```

### 创建自定义插件

```python
from workflows.plugins import InteractionPlugin, InteractionPoint, InteractionRequest

class MyCustomPlugin(InteractionPlugin):
    name = "my_custom_plugin"
    description = "My custom interaction"
    hook_point = InteractionPoint.BEFORE_IMPLEMENTATION
    priority = 50

    async def should_trigger(self, context):
        return context.get("enable_my_plugin", True)

    async def create_interaction(self, context):
        return InteractionRequest(
            interaction_type="custom_interaction",
            title="Custom Check",
            description="Please confirm...",
            data={"key": "value"},
            options={"yes": "Confirm", "no": "Cancel"},
        )

    async def process_response(self, response, context):
        if response.action == "yes":
            context["custom_confirmed"] = True
        else:
            context["workflow_cancelled"] = True
        return context

# 注册插件
registry.register(MyCustomPlugin())
```

## 交互点列表

| Hook Point | 位置 | 默认插件 |
|------------|------|----------|
| `BEFORE_PLANNING` | 生成计划前 | RequirementAnalysisPlugin |
| `AFTER_PLANNING` | 计划生成后 | PlanReviewPlugin |
| `BEFORE_IMPLEMENTATION` | 代码生成前 | (无) |
| `AFTER_IMPLEMENTATION` | 代码生成后 | (无) |

## WebSocket 消息格式

### 后端 → 前端: `interaction_required`

```json
{
  "type": "interaction_required",
  "task_id": "xxx",
  "interaction_type": "requirement_questions",
  "title": "Let's clarify your requirements",
  "description": "Answer these questions...",
  "data": {
    "questions": [...]
  },
  "options": {
    "submit": "Submit Answers",
    "skip": "Skip"
  },
  "timestamp": "2024-01-01T00:00:00Z"
}
```

### 前端 → 后端: POST `/api/v1/workflows/respond/{task_id}`

```json
{
  "action": "submit",
  "data": {
    "answers": {
      "q1": "Answer 1",
      "q2": "Answer 2"
    }
  },
  "skipped": false
}
```

## 优势

1. **无侵入** - 不修改核心工作流逻辑
2. **可插拔** - 随时启用/禁用插件
3. **可扩展** - 轻松添加新的交互点
4. **可配置** - 通过配置文件控制行为
5. **解耦合** - 交互逻辑与业务逻辑分离
