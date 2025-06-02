# 🚀 ReproAI Streamlit 快速启动指南

## 📋 前置要求

- Python 3.8+
- 已安装项目的基本依赖
- 网络连接（用于下载论文和GitHub仓库）

## ⚡ 快速启动

### 1. 安装Streamlit依赖

```bash
# 安装Streamlit相关依赖
pip install -r requirements_streamlit.txt
```

### 2. 测试环境（可选）

```bash
# 运行测试脚本检查环境
python test_streamlit.py
```

### 3. 启动应用

#### 方法一：使用启动脚本（推荐）
```bash
# Windows
start_streamlit.bat

# Linux/macOS
python run_streamlit.py
```

#### 方法二：直接运行
```bash
streamlit run streamlit_app.py
```

### 4. 访问界面

浏览器会自动打开，或手动访问：
```
http://localhost:8501
```

## 🎯 使用流程

### 📁 文件上传模式
1. 点击"📁 Upload File"
2. 拖拽或选择论文文件（PDF、DOCX等）
3. 点击"🚀 Start Processing"

### 🌐 URL输入模式
1. 点击"🌐 Enter URL"
2. 输入论文URL（如arXiv链接）
3. 点击"🚀 Start Processing"

## 📊 界面功能

### 主要区域
- **头部**: 应用logo和介绍
- **功能卡片**: 核心能力展示
- **输入区域**: 文件上传或URL输入
- **处理进度**: 实时进度显示
- **结果展示**: 分析结果和生成代码

### 侧边栏
- **控制面板**: 引擎状态显示
- **系统信息**: Python版本和平台信息
- **处理历史**: 历史任务记录
- **清除历史**: 清理历史记录

## 🔧 故障排除

### 常见问题

1. **Streamlit未安装**
   ```bash
   pip install streamlit>=1.28.0
   ```

2. **端口被占用**
   ```bash
   streamlit run streamlit_app.py --server.port 8502
   ```

3. **文件上传失败**
   - 检查文件格式（支持：PDF、DOCX、HTML、TXT等）
   - 确保文件大小合理（建议<100MB）

4. **处理超时**
   - 检查网络连接
   - 确保MCP服务正常运行
   - 不要在处理过程中刷新页面

5. **依赖缺失**
   ```bash
   # 安装缺失的依赖
   pip install nest-asyncio pathlib2
   ```

### 调试模式

```bash
# 启用详细日志
streamlit run streamlit_app.py --logger.level debug

# 查看详细错误信息
python -c "import streamlit_app; streamlit_app.main()"
```

## 💡 使用技巧

1. **保持页面打开**: 处理过程中不要关闭浏览器标签页
2. **网络稳定**: 确保网络连接稳定，特别是处理URL时
3. **文件格式**: 优先使用PDF格式的论文文件
4. **历史记录**: 利用侧边栏查看之前的处理结果
5. **错误信息**: 点击错误详情查看完整的错误信息

## 🔄 与CLI版本对比

| 特性 | CLI版本 | Streamlit版本 |
|------|---------|---------------|
| 启动方式 | `python main.py` | `streamlit run streamlit_app.py` |
| 界面 | 命令行 | Web界面 |
| 文件上传 | 文件路径 | 拖拽上传 |
| 进度显示 | 文本 | 可视化进度条 |
| 结果查看 | 终端输出 | 分栏展示 |
| 历史记录 | 无 | 侧边栏历史 |

## 📞 获取帮助

如果遇到问题：

1. 查看控制台错误信息
2. 运行测试脚本：`python test_streamlit.py`
3. 检查依赖安装：`pip list | grep streamlit`
4. 查看详细文档：`README_STREAMLIT.md`

---

🎉 **享受使用ReproAI Streamlit界面！** 