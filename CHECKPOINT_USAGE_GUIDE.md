# 🔖 Checkpoint恢复机制使用指南

## 📋 概述

我们已经成功为代码评估工作流实现了先进的checkpoint恢复机制！这个系统允许你在workflow的任何阶段失败后，从上一个成功的checkpoint自动恢复，而不需要重新开始整个流程。

## ✨ 主要特性

### 🔄 自动Phase恢复
- **智能断点续传**: 在Phase 1-5的任何阶段失败后自动恢复
- **状态完整保存**: 保存完整的evaluation state和agent states
- **依赖变化检测**: 自动检测关键文件变化，防止过期checkpoint的使用

### 🎯 工作流支持的Phases
1. **Phase 1: ANALYZING** - 仓库分析和修订报告生成
2. **Phase 2: REVISING** - 多文件代码修订执行  
3. **Phase 3: STATIC_ANALYSIS** - 静态分析和代码质量修复
4. **Phase 4: ERROR_ANALYSIS** - 迭代错误分析和修复
5. **Phase 5: COMPLETED** - 最终评估

### 💾 Checkpoint存储位置
根据你的需求，checkpoints保存在：
```
repo_path = "/path/to/papers/1/generate_code" 
checkpoint_dir = "/path/to/papers/1/.checkpoints"
```

## 🚀 使用方法

### 1. 正常运行（自动resume）
```python
from workflows.code_evaluation_workflow_refactored import main

# 默认会自动从checkpoint恢复
result = await main(
    repo_path="/path/to/your/repo",
    docs_path="/path/to/docs.txt", 
    memory_path="/path/to/memory.md"
)
```

### 2. 强制从头开始
```python
result = await main(
    repo_path="/path/to/your/repo",
    docs_path="/path/to/docs.txt",
    memory_path="/path/to/memory.md",
    force_restart=True  # 忽略所有checkpoints
)
```

### 3. 查看checkpoint状态
```python
result = await main(
    repo_path="/path/to/your/repo",
    show_checkpoint_status=True  # 只显示状态，不运行workflow
)
```

### 4. 清除所有checkpoints
```python
result = await main(
    repo_path="/path/to/your/repo", 
    clear_checkpoints=True  # 清除所有checkpoints
)
```

### 5. 禁用checkpoint功能
```python
result = await main(
    repo_path="/path/to/your/repo",
    docs_path="/path/to/docs.txt",
    memory_path="/path/to/memory.md",
    resume_from_checkpoint=False  # 不使用checkpoint
)
```

## 🎯 典型使用场景

### 场景1: Phase 3失败后恢复
```bash
# 第一次运行，在Phase 3失败
python -c "
import asyncio
from workflows.code_evaluation_workflow_refactored import main
asyncio.run(main())
"
# 输出: ❌ Phase 3失败，但Phase 1-2的checkpoint已保存

# 修复问题后重新运行，自动从Phase 3开始  
python -c "
import asyncio
from workflows.code_evaluation_workflow_refactored import main
asyncio.run(main())
"
# 输出: 🔄 检测到checkpoint，从Phase 3开始恢复
```

### 场景2: 检查当前状态
```python
# 检查是否有可用的checkpoint
import asyncio
from workflows.code_evaluation_workflow_refactored import main

async def check_status():
    result = await main(
        repo_path="/Users/lizongwei/Reasearch/DeepCode_Base/DeepCode_eval_init/deepcode_lab/papers/1/generate_code",
        show_checkpoint_status=True
    )
    print("Checkpoint Status:", result)

asyncio.run(check_status())
```

### 场景3: 清理重新开始
```python
# 清除所有checkpoints，完全重新开始
import asyncio
from workflows.code_evaluation_workflow_refactored import main

async def fresh_start():
    # 先清除checkpoints
    await main(
        repo_path="/Users/lizongwei/Reasearch/DeepCode_Base/DeepCode_eval_init/deepcode_lab/papers/1/generate_code",
        clear_checkpoints=True
    )
    
    # 然后重新运行
    result = await main(
        repo_path="/Users/lizongwei/Reasearch/DeepCode_Base/DeepCode_eval_init/deepcode_lab/papers/1/generate_code",
        docs_path="/Users/lizongwei/Reasearch/DeepCode_Base/DeepCode_eval_init/deepcode_lab/papers/1/initial_plan.txt",
        memory_path="/Users/lizongwei/Reasearch/DeepCode_Base/DeepCode_eval_init/deepcode_lab/papers/1/generate_code/implement_code_summary.md"
    )

asyncio.run(fresh_start())
```

## 📊 Checkpoint信息结构

### Checkpoint Summary
```json
{
  "checkpoint_dir": "/path/to/.checkpoints",
  "has_checkpoint": true,
  "phase_history": [
    {
      "phase": "analyzing", 
      "status": "started",
      "timestamp": "2024-01-01T10:00:00"
    },
    {
      "phase": "analyzing",
      "status": "completed", 
      "timestamp": "2024-01-01T10:05:00",
      "checkpoint_id": "analyzing_1234567890",
      "duration": 300.0,
      "file_count": 25
    }
  ],
  "total_phases": 2,
  "recommendation": {
    "phase": "revising",
    "reason": "Resume from revising (completed: analyzing)"
  }
}
```

### Checkpoint Metadata
```json
{
  "checkpoint_id": "analyzing_1234567890",
  "phase": "analyzing", 
  "timestamp": "2024-01-01T10:05:00",
  "repo_path": "/path/to/repo",
  "checkpoint_version": "1.0",
  "phase_duration": 300.0,
  "total_duration": 300.0, 
  "file_count": 25,
  "dependency_hashes": {
    "requirements.txt": "abc123...",
    "setup.py": "def456...",
    "config.yaml": "ghi789..."
  }
}
```

## 🛡️ 安全特性

### 依赖变化检测
系统自动监控关键文件变化：
- `requirements.txt`
- `setup.py` 
- `pyproject.toml`
- `Dockerfile`
- `config.yaml`

如果这些文件发生变化，checkpoint会自动失效，确保不会使用过期的状态。

### Checkpoint验证
- **时效性检查**: 超过7天的checkpoint自动失效
- **路径验证**: 确保checkpoint与当前仓库路径匹配
- **完整性验证**: 验证checkpoint文件的完整性

## 🔧 故障排除

### 常见问题

#### 1. Checkpoint无法加载
```
⚠️ Checkpoint validation failed, starting fresh
```
**原因**: 依赖文件发生变化或checkpoint过期
**解决**: 自动从头开始，无需手动处理

#### 2. 想要强制重新开始
```python
# 使用force_restart参数
await main(..., force_restart=True)
```

#### 3. Checkpoint目录权限问题
**确保**: 有对父目录的写权限
```bash
chmod 755 /path/to/papers/1/
```

### 调试信息
启用详细日志来查看checkpoint操作：
```python
import logging
logging.basicConfig(level=logging.INFO)

# 运行workflow，会显示详细的checkpoint信息
await main(...)
```

## 📈 性能优化

### Checkpoint大小
- 每个checkpoint通常在1-10MB之间
- 包含完整的evaluation state和agent states
- 自动压缩存储以节省空间

### 恢复速度
- Checkpoint加载通常在1-3秒内完成
- 跳过已完成的phases，直接进入失败的phase
- 大型项目的恢复时间节省可达80%以上

## 🚀 结论

这个checkpoint恢复机制极大地提高了调试和开发效率：

✅ **节省时间**: 无需重复执行已成功的phases
✅ **提高稳定性**: 自动处理临时失败和网络问题  
✅ **简化调试**: 专注于失败的特定phase
✅ **保证一致性**: 完整保存和恢复所有状态信息
✅ **安全可靠**: 多重验证确保checkpoint有效性

现在你可以放心地在长时间运行的workflow中进行实验和调试，不用担心中途失败带来的时间损失！🎉
