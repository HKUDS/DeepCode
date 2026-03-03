# DeepCode New UI

Modern, intelligent UI for DeepCode - AI-powered code generation platform.

## Technology Stack

- **Backend**: FastAPI (Python)
- **Frontend**: React 18 + TypeScript + Vite
- **Styling**: Tailwind CSS + shadcn/ui
- **State Management**: Zustand
- **Real-time Communication**: WebSocket
- **Workflow Visualization**: React Flow
- **Code Display**: Monaco Editor

## Features

### Intelligent Features

1. **Real-time Streaming Output** - Watch code generation in real-time, like ChatGPT
2. **Smart Context Awareness** - Remembers conversation history, provides intelligent suggestions
3. **Adaptive Interface** - Layout adjusts based on task type
4. **Visual Workflow** - Draggable flow-chart style task visualization

### Design Style

- Clean, modern design inspired by Notion/Linear
- Light theme with blue accent colors
- Inter font for text, JetBrains Mono for code

## Project Structure

```
new_ui/
├── backend/                    # FastAPI Backend
│   ├── main.py                # Entry point
│   ├── config.py              # Configuration
│   ├── api/
│   │   ├── routes/            # REST API endpoints
│   │   └── websockets/        # WebSocket handlers
│   ├── services/              # Business logic
│   └── models/                # Pydantic models
│
├── frontend/                   # React Frontend
│   ├── src/
│   │   ├── components/        # React components
│   │   ├── pages/             # Page components
│   │   ├── hooks/             # Custom hooks
│   │   ├── stores/            # Zustand stores
│   │   ├── services/          # API client
│   │   └── types/             # TypeScript types
│   ├── package.json
│   └── vite.config.ts
│
└── scripts/
    ├── start_dev.sh           # Development startup
    └── build.sh               # Production build
```

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+
- npm or yarn

### Development

1. **Start both backend and frontend:**

```bash
cd new_ui
chmod +x scripts/start_dev.sh
./scripts/start_dev.sh
```

2. **Or start separately:**

Backend:
```bash
cd new_ui/backend
pip install -r requirements.txt  # First time only
uvicorn main:app --reload --port 8000
```

Frontend:
```bash
cd new_ui/frontend
npm install  # First time only
npm run dev
```

3. **Access the application:**
   - Frontend: http://localhost:5173
   - Backend API: http://localhost:8000
   - API Documentation: http://localhost:8000/docs

### Production Build

```bash
cd new_ui
chmod +x scripts/build.sh
./scripts/build.sh
```

## API Endpoints

### REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/workflows/paper-to-code` | Start paper-to-code workflow |
| POST | `/api/v1/workflows/chat-planning` | Start chat-based planning |
| GET | `/api/v1/workflows/status/{task_id}` | Get workflow status |
| POST | `/api/v1/requirements/questions` | Generate guiding questions |
| POST | `/api/v1/requirements/summarize` | Summarize requirements |
| POST | `/api/v1/files/upload` | Upload file |
| GET | `/api/v1/config/settings` | Get settings |

### WebSocket Endpoints

| Endpoint | Description |
|----------|-------------|
| `/ws/workflow/{task_id}` | Real-time workflow progress |
| `/ws/code-stream/{task_id}` | Streaming code output |
| `/ws/logs/{session_id}` | Live log streaming |

## Configuration

The new UI reads configuration from the existing DeepCode config files:

- `mcp_agent.config.yaml` - LLM provider, models, MCP server settings
- `mcp_agent.secrets.yaml` - API keys

## Integration

The new UI integrates with existing DeepCode components:

- `workflows/agent_orchestration_engine.py` - Core workflow execution
- `workflows/agents/` - Specialized agents
- `utils/llm_utils.py` - LLM provider management

## Browser Support

- Chrome (recommended)
- Firefox
- Safari
- Edge

## License

MIT License - see main DeepCode license.
