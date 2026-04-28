#!/bin/bash
# ============================================================
# Nanobot + DeepCode 一键启动脚本
# 自动检查环境、配置、构建 Docker 镜像并启动服务
# 实现飞书 <-> Nanobot <-> DeepCode 全链路通信
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/deepcode_docker/docker-compose.yml"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# docker compose wrapper
dc() {
    docker compose -f "$COMPOSE_FILE" "$@"
}

print_banner() {
    echo ""
    echo "╔══════════════════════════════════════════════╗"
    echo "║   Nanobot + DeepCode  一键启动脚本          ║"
    echo "║   飞书 <-> Nanobot <-> DeepCode             ║"
    echo "╚══════════════════════════════════════════════╝"
    echo ""
}

# ============ 检查 Docker 环境 ============
check_docker() {
    echo -e "${BLUE}[1/5] 检查 Docker 环境...${NC}"

    if ! command -v docker &> /dev/null; then
        echo -e "${RED}❌ 未检测到 Docker，请先安装 Docker Desktop${NC}"
        echo "   下载地址: https://www.docker.com/products/docker-desktop"
        exit 1
    fi

    if ! docker info &> /dev/null 2>&1; then
        echo -e "${RED}❌ Docker 服务未运行，请先启动 Docker Desktop${NC}"
        exit 1
    fi

    echo -e "${GREEN}   ✓ Docker 环境正常${NC}"
}

# ============ 检查 DeepCode 配置文件 ============
check_deepcode_config() {
    echo -e "${BLUE}[2/5] 检查 DeepCode 配置文件...${NC}"

    if [ ! -f "$PROJECT_ROOT/deepcode_config.json" ]; then
        if [ -f "$PROJECT_ROOT/deepcode_config.json.example" ]; then
            echo -e "${YELLOW}   ⚠ 未找到 deepcode_config.json，从模板创建...${NC}"
            cp "$PROJECT_ROOT/deepcode_config.json.example" "$PROJECT_ROOT/deepcode_config.json"
            echo -e "${YELLOW}   ⚡ 请编辑 deepcode_config.json 填入 providers.<name>.apiKey 后重新运行${NC}"
            echo -e "      文件路径: $PROJECT_ROOT/deepcode_config.json"
            exit 1
        else
            echo -e "${RED}   ❌ 缺少 deepcode_config.json 且无模板文件${NC}"
            exit 1
        fi
    fi
    echo -e "${GREEN}   ✓ deepcode_config.json${NC}"
}

# ============ 检查 Nanobot 配置文件 ============
check_nanobot_config() {
    echo -e "${BLUE}[3/5] 检查 Nanobot 配置文件 (飞书等渠道)...${NC}"

    if [ ! -f "$PROJECT_ROOT/nanobot_config.json" ]; then
        if [ -f "$PROJECT_ROOT/nanobot_config.json.example" ]; then
            echo -e "${YELLOW}   ⚠ 未找到 nanobot_config.json，从模板创建...${NC}"
            cp "$PROJECT_ROOT/nanobot_config.json.example" "$PROJECT_ROOT/nanobot_config.json"
            echo -e "${YELLOW}   ⚡ 请编辑 nanobot_config.json 填入以下信息后重新运行:${NC}"
            echo -e "      - 飞书 appId 和 appSecret"
            echo -e "      - LLM Provider API Key (如 OpenRouter)"
            echo -e "      文件路径: $PROJECT_ROOT/nanobot_config.json"
            exit 1
        else
            echo -e "${RED}   ❌ 缺少 nanobot_config.json 且无模板文件${NC}"
            exit 1
        fi
    fi

    # 检查飞书配置是否还是占位符
    if grep -q '"your_feishu_app_id"' "$PROJECT_ROOT/nanobot_config.json" 2>/dev/null; then
        echo -e "${YELLOW}   ⚠ nanobot_config.json 中飞书配置仍为占位符${NC}"
        echo -e "      请编辑 $PROJECT_ROOT/nanobot_config.json"
        echo -e "      填入真实的 appId 和 appSecret"
        echo ""
        read -p "   是否已配置好，继续启动? [y/N] " confirm
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi

    echo -e "${GREEN}   ✓ nanobot_config.json${NC}"
}

# ============ 创建必要目录 ============
ensure_dirs() {
    echo -e "${BLUE}[4/5] 检查数据目录...${NC}"
    mkdir -p "$PROJECT_ROOT/deepcode_lab" "$PROJECT_ROOT/uploads" "$PROJECT_ROOT/logs"
    echo -e "${GREEN}   ✓ deepcode_lab/ uploads/ logs/ 已就绪${NC}"
}

# ============ 检查并构建 Docker 镜像 ============
check_and_build() {
    echo -e "${BLUE}[5/5] 检查 Docker 镜像...${NC}"

    local need_build_deepcode=false
    local need_build_nanobot=false

    # 检查 deepcode 镜像是否存在
    if ! docker images --format '{{.Repository}}' | grep -q "deepcode"; then
        need_build_deepcode=true
    fi

    # 检查 nanobot 镜像是否存在
    if ! docker images --format '{{.Repository}}' | grep -q "nanobot"; then
        need_build_nanobot=true
    fi

    if [ "$FORCE_BUILD" = true ]; then
        echo -e "${YELLOW}   强制重新构建所有镜像...${NC}"
        BUILD_FLAG="--build"
    elif [ "$need_build_deepcode" = true ] || [ "$need_build_nanobot" = true ]; then
        echo -e "${YELLOW}   检测到缺少镜像，首次构建中...${NC}"
        if [ "$need_build_deepcode" = true ]; then
            echo -e "${YELLOW}   - deepcode 镜像需要构建${NC}"
        fi
        if [ "$need_build_nanobot" = true ]; then
            echo -e "${YELLOW}   - nanobot 镜像需要构建${NC}"
        fi
        BUILD_FLAG="--build"
    else
        echo -e "${GREEN}   ✓ deepcode 镜像已存在，跳过构建${NC}"
        echo -e "${GREEN}   ✓ nanobot 镜像已存在，跳过构建${NC}"
        BUILD_FLAG=""
    fi
}

# ============ 启动服务 ============
start_services() {
    echo ""
    echo -e "${BLUE}🚀 启动 DeepCode + Nanobot 服务...${NC}"
    echo ""

    dc up $BUILD_FLAG $DETACH_FLAG

    if [ -n "$DETACH_FLAG" ]; then
        echo ""
        echo -e "${YELLOW}⏳ 等待服务启动...${NC}"
        for i in $(seq 1 30); do
            if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
                echo ""
                echo "╔══════════════════════════════════════════════╗"
                echo -e "║  ${GREEN}✓ DeepCode + Nanobot 已启动!${NC}               ║"
                echo "╠══════════════════════════════════════════════╣"
                echo "║                                              ║"
                echo "║  DeepCode API:  http://localhost:8000        ║"
                echo "║  DeepCode Docs: http://localhost:8000/docs   ║"
                echo "║  Nanobot 网关:  http://localhost:18790       ║"
                echo "║                                              ║"
                echo "║  飞书机器人已通过 WebSocket 长连接接入       ║"
                echo "║  现在可以在飞书中与机器人对话了!             ║"
                echo "║                                              ║"
                echo -e "║  查看日志: ${CYAN}$0 logs${NC}                       ║"
                echo -e "║  停止服务: ${CYAN}$0 stop${NC}                       ║"
                echo "╚══════════════════════════════════════════════╝"
                echo ""
                return 0
            fi
            sleep 2
        done
        echo -e "${YELLOW}⚠ 服务仍在启动中，请稍后检查${NC}"
        echo -e "   使用 ${CYAN}$0 logs${NC} 查看启动日志"
    fi
}

# ============ 帮助信息 ============
usage() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  (无参数)      检查环境并启动所有服务 (前台运行)"
    echo "  -d, --detach  后台运行"
    echo "  --build       强制重新构建 Docker 镜像"
    echo "  stop          停止所有服务"
    echo "  restart       重启所有服务"
    echo "  logs          查看实时日志"
    echo "  status        查看服务状态"
    echo "  clean         停止并删除容器和镜像"
    echo "  -h, --help    显示帮助信息"
    echo ""
    echo "示例:"
    echo "  $0              # 首次运行: 检查配置 → 构建镜像 → 启动"
    echo "  $0              # 再次运行: 跳过构建 → 直接启动"
    echo "  $0 -d           # 后台启动"
    echo "  $0 --build      # 强制重新构建后启动"
    echo "  $0 stop         # 停止服务"
    echo "  $0 logs         # 查看日志"
}

# ============ 解析命令行参数 ============
ACTION="up"
BUILD_FLAG=""
DETACH_FLAG=""
FORCE_BUILD=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --build)
            FORCE_BUILD=true
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

# ============ 主流程 ============
case $ACTION in
    up)
        print_banner
        check_docker
        check_deepcode_config
        check_nanobot_config
        ensure_dirs
        check_and_build
        start_services
        ;;

    stop)
        check_docker
        echo -e "${BLUE}🛑 停止 DeepCode + Nanobot 服务...${NC}"
        dc down
        echo -e "${GREEN}✓ 所有服务已停止${NC}"
        ;;

    restart)
        check_docker
        echo -e "${BLUE}🔄 重启 DeepCode + Nanobot 服务...${NC}"
        dc down
        check_deepcode_config
        check_nanobot_config
        ensure_dirs
        check_and_build
        dc up -d $BUILD_FLAG
        echo -e "${GREEN}✓ 服务已重启${NC}"
        echo -e "   DeepCode: http://localhost:8000"
        echo -e "   Nanobot:  http://localhost:18790"
        ;;

    logs)
        check_docker
        echo -e "${BLUE}📋 服务日志 (Ctrl+C 退出):${NC}"
        echo ""
        dc logs -f
        ;;

    status)
        check_docker
        echo -e "${BLUE}📊 服务状态:${NC}"
        echo ""
        dc ps
        echo ""
        # DeepCode 健康检查
        if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
            echo -e "${GREEN}✓ DeepCode 运行正常 (http://localhost:8000)${NC}"
        else
            echo -e "${YELLOW}⚠ DeepCode 未响应${NC}"
        fi
        # Nanobot 端口检查
        if curl -sf http://localhost:18790 > /dev/null 2>&1 || \
           nc -z localhost 18790 2>/dev/null; then
            echo -e "${GREEN}✓ Nanobot 网关运行中 (http://localhost:18790)${NC}"
        else
            echo -e "${YELLOW}⚠ Nanobot 网关未响应${NC}"
        fi
        ;;

    clean)
        check_docker
        echo -e "${YELLOW}⚠ 即将停止并删除 DeepCode + Nanobot 容器和镜像${NC}"
        echo -e "${YELLOW}  (数据目录 deepcode_lab/, uploads/, logs/ 不会被删除)${NC}"
        read -p "确认? [y/N] " confirm
        if [[ "$confirm" =~ ^[Yy]$ ]]; then
            dc down --rmi local --remove-orphans -v
            echo -e "${GREEN}✓ 已清理完成${NC}"
        else
            echo "已取消"
        fi
        ;;
esac
