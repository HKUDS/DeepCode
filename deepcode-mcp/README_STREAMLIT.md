# ReproAI Streamlit Web Interface

## 概述

这是ReproAI的Web界面版本，使用Streamlit构建，提供了一个现代化的用户界面来替代原有的CLI模式。

## 功能特性

### 🎯 核心功能
- **📊 智能论文分析**: AI驱动的文档处理和内容提取
- **📁 多格式支持**: PDF、DOCX、PPTX、HTML、TXT等格式
- **🚀 自动化仓库管理**: 智能GitHub集成和代码组织
- **🔬 先进技术栈**: Python • AI • MCP • Docling • LLM

### 🌟 界面特性
- **现代化UI**: 美观的渐变设计和响应式布局
- **实时进度**: 可视化处理进度条和状态更新
- **历史记录**: 处理历史和结果管理
- **文件上传**: 拖拽式文件上传界面
- **URL支持**: 直接输入论文URL进行处理

## 安装和运行

### 1. 安装依赖

```bash
# 安装Streamlit相关依赖
pip install -r requirements_streamlit.txt

# 或者单独安装Streamlit
pip install streamlit>=1.28.0
```

### 2. 启动应用

#### 方法一：使用启动脚本（推荐）
```bash
python run_streamlit.py
```

#### 方法二：直接运行Streamlit
```bash
streamlit run streamlit_app.py
```

### 3. 访问界面

启动后，浏览器会自动打开，或者手动访问：
```
http://localhost:8501
```

## 使用指南

### 📁 文件上传模式
1. 选择"📁 Upload File"选项
2. 拖拽或点击上传研究论文文件
3. 支持格式：PDF、DOCX、DOC、HTML、HTM、TXT、MD
4. 点击"🚀 Start Processing"开始处理

### 🌐 URL输入模式
1. 选择"🌐 Enter URL"选项
2. 输入论文URL（支持arXiv、IEEE、ACM等）
3. 点击"🚀 Start Processing"开始处理

### 📊 处理流程
1. **论文分析**: AI分析论文内容和结构
2. **下载处理**: 处理相关文件和资源
3. **代码准备**: 准备代码仓库和GitHub集成
4. **结果展示**: 显示分析结果和生成的代码

## 界面组件

### 🎛️ 控制面板（侧边栏）
- **引擎状态**: 显示应用初始化状态
- **处理历史**: 查看之前的处理记录和结果

### 📋 结果展示
- **分析结果**: 论文分析的详细输出
- **下载结果**: 文件下载和处理状态
- **仓库结果**: GitHub仓库准备和代码生成结果

## 技术架构

### 前端
- **Streamlit**: Web界面框架
- **自定义CSS**: 现代化样式和响应式设计
- **Session State**: 状态管理和数据持久化

### 后端
- **MCP Agent**: 核心代理系统
- **异步处理**: 非阻塞的任务执行
- **文件处理**: 智能文件上传和临时文件管理

### 集成
- **原有工作流**: 完全兼容现有的CLI工作流
- **错误处理**: 完善的异常处理和用户反馈
- **日志系统**: 详细的处理日志和调试信息

## 与CLI版本的对比

| 特性 | CLI版本 | Streamlit版本 |
|------|---------|---------------|
| 用户界面 | 命令行 | Web界面 |
| 文件上传 | 文件路径/GUI对话框 | 拖拽上传 |
| 进度显示 | 文本进度条 | 可视化进度条 |
| 结果展示 | 终端输出 | 可折叠面板 |
| 历史记录 | 无 | 侧边栏历史 |
| 多任务 | 顺序执行 | 会话状态管理 |

## 故障排除

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
   - 检查文件格式是否支持
   - 确保文件大小不超过限制

4. **处理超时**
   - 检查网络连接
   - 确保MCP服务正常运行

### 调试模式

启用Streamlit调试模式：
```bash
streamlit run streamlit_app.py --logger.level debug
```

## 开发说明

### 文件结构
```
├── streamlit_app.py          # 主Streamlit应用
├── run_streamlit.py          # 启动脚本
├── requirements_streamlit.txt # Streamlit依赖
├── main.py                   # 原CLI版本
└── utils/
    ├── cli_interface.py      # CLI界面组件
    └── file_processor.py     # 文件处理工具
```

### 自定义开发

1. **修改样式**: 编辑`streamlit_app.py`中的CSS样式
2. **添加功能**: 在相应的函数中添加新的处理逻辑
3. **调整布局**: 修改Streamlit组件的排列和配置

## 许可证

MIT License - 详见LICENSE文件

## 贡献

欢迎提交Issue和Pull Request来改进这个项目！

---

🔬 **ReproAI v2.0.0** | Built with ❤️ using Streamlit & AI 