#!/bin/bash
# DeepCode Docker 一键启动脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# docker compose wrapper — always use the correct compose file
dc() {
    docker compose -f "$COMPOSE_FILE" "$@"
}

echo ""
echo "╔════════════════════════════════════════╗"
echo "║   DeepCode - Docker 启动脚本          ║"
echo "╚════════════════════════════════════════╝"
echo ""

# ============ 检查 Docker 环境 ============
check_docker() {
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}❌ 未检测到 Docker，请先安装 Docker Desktop${NC}"
        echo "   下载地址: https://www.docker.com/products/docker-desktop"
        exit 1
    fi

    if ! docker info &> /dev/null 2>&1; then
        echo -e "${RED}❌ Docker 服务未运行，请先启动 Docker Desktop${NC}"
        exit 1
    fi

    echo -e "${GREEN}✓ Docker 环境正常${NC}"
}

# ============ 检查配置文件 ============
check_config() {
    if [ ! -f "$PROJECT_ROOT/deepcode_config.json" ]; then
        if [ -f "$PROJECT_ROOT/deepcode_config.json.example" ]; then
            echo -e "${YELLOW}⚠ 未找到 deepcode_config.json${NC}"
            echo -e "${YELLOW}  正在从模板创建...${NC}"
            cp "$PROJECT_ROOT/deepcode_config.json.example" "$PROJECT_ROOT/deepcode_config.json"
            echo -e "${YELLOW}  ⚡ 请编辑 deepcode_config.json 填入 providers.<name>.apiKey 后重新运行${NC}"
            exit 1
        else
            echo -e "${RED}❌ 缺少 deepcode_config.json 配置文件，且未找到模板${NC}"
            exit 1
        fi
    fi
    echo -e "${GREEN}✓ deepcode_config.json 已找到${NC}"
}

# ============ 创建必要目录 ============
ensure_dirs() {
    mkdir -p "$PROJECT_ROOT/deepcode_lab" "$PROJECT_ROOT/uploads" "$PROJECT_ROOT/logs"
    echo -e "${GREEN}✓ 数据目录已就绪 (deepcode_lab/, uploads/, logs/)${NC}"
}

# ============ 解析命令行参数 ============
ACTION="up"
BUILD_FLAG=""
DETACH_FLAG=""

usage() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  --build       强制重新构建镜像"
    echo "  -d, --detach  后台运行（不占用终端）"
    echo "  stop          停止容器"
    echo "  restart       重启容器"
    echo "  logs          查看容器日志"
    echo "  status        查看容器状态"
    echo "  cli           在 Docker 容器内启动交互式 CLI"
    echo "  clean         停止并删除容器和镜像"
    echo "  -h, --help    显示帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                  # 构建并启动（首次会自动构建）"
    echo "  $0 --build          # 强制重新构建后启动"
    echo "  $0 -d               # 后台启动"
    echo "  $0 stop             # 停止服务"
    echo "  $0 logs             # 查看实时日志"
    echo "  $0 cli              # 启动交互式 CLI"
    echo "  $0 clean            # 完全清理"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --build)
            BUILD_FLAG="--build"
            shift
            ;;
        -d|--detach)
            DETACH_FLAG="-d"
            shift
            ;;
        stop)
            ACTION="stop"
            shift
            ;;
        restart)
            ACTION="restart"
            shift
            ;;
        logs)
            ACTION="logs"
            shift
            ;;
        status)
            ACTION="status"
            shift
            ;;
        clean)
            ACTION="clean"
            shift
            ;;
        cli)
            ACTION="cli"
            shift
            break  # Remaining args passed to CLI
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}未知参数: $1${NC}"
            usage
            exit 1
            ;;
    esac
done

# ============ 执行操作 ============
case $ACTION in
    up)
        check_docker
        check_config
        ensure_dirs

        echo ""
        echo -e "${BLUE}🐳 启动 DeepCode Docker 容器...${NC}"

        # 检查镜像是否存在，首次运行自动构建
        if [ -z "$BUILD_FLAG" ]; then
            if ! docker images | grep -q "deepcode"; then
                echo -e "${YELLOW}⚡ 首次运行，自动构建镜像（可能需要几分钟）...${NC}"
                BUILD_FLAG="--build"
            fi
        fi

        dc up $BUILD_FLAG $DETACH_FLAG

        if [ -n "$DETACH_FLAG" ]; then
            # 后台模式，等待容器启动后显示信息
            echo ""
            echo -e "${YELLOW}⏳ 等待服务启动...${NC}"
            for i in $(seq 1 30); do
                if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
                    echo ""
                    echo "╔════════════════════════════════════════╗"
                    echo -e "║  ${GREEN}DeepCode 已启动! (Docker)${NC}             ║"
                    echo "╠════════════════════════════════════════╣"
                    echo "║                                        ║"
                    echo "║  🌐 访问: http://localhost:8000        ║"
                    echo "║  📚 API:  http://localhost:8000/docs   ║"
                    echo "║                                        ║"
                    echo "║  查看日志: $0 logs                     ║"
                    echo "║  停止服务: $0 stop                     ║"
                    echo "╚════════════════════════════════════════╝"
                    echo ""
                    exit 0
                fi
                sleep 2
            done
            echo -e "${YELLOW}⚠ 服务仍在启动中，请稍后访问 http://localhost:8000${NC}"
            echo -e "   使用 ${CYAN}$0 logs${NC} 查看启动日志"
        fi
        ;;

    stop)
        check_docker
        echo -e "${BLUE}🛑 停止 DeepCode 容器...${NC}"
        dc down
        echo -e "${GREEN}✓ 服务已停止${NC}"
        ;;

    restart)
        check_docker
        echo -e "${BLUE}🔄 重启 DeepCode 容器...${NC}"
        dc down
        dc up -d $BUILD_FLAG
        echo -e "${GREEN}✓ 服务已重启${NC}"
        echo -e "   访问: http://localhost:8000"
        ;;

    logs)
        check_docker
        echo -e "${BLUE}📋 DeepCode 容器日志 (Ctrl+C 退出):${NC}"
        echo ""
        dc logs -f
        ;;

    status)
        check_docker
        echo -e "${BLUE}📊 DeepCode 容器状态:${NC}"
        echo ""
        dc ps
        echo ""
        # 检查健康状态
        if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
            echo -e "${GREEN}✓ 服务运行正常 (http://localhost:8000)${NC}"
        else
            echo -e "${YELLOW}⚠ 服务未响应或未启动${NC}"
        fi
        ;;

    cli)
        check_docker
        check_config
        ensure_dirs
        echo ""
        echo -e "${BLUE}🖥️  启动 DeepCode CLI (Docker)...${NC}"
        echo ""
        dc run --rm -it deepcode cli "$@"
        ;;

    clean)
        check_docker
        echo -e "${YELLOW}⚠ 即将停止并删除 DeepCode 容器和镜像${NC}"
        echo -e "${YELLOW}  (数据目录 deepcode_lab/, uploads/, logs/ 不会被删除)${NC}"
        read -p "确认? [y/N] " confirm
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            dc down --rmi local --remove-orphans
            echo -e "${GREEN}✓ 已清理完成${NC}"
        else
            echo "已取消"
        fi
        ;;
esac
