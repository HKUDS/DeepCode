#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepCode Telemetry — 移植自 CODEX.EXE 的 OpenTelemetry 遥测栈
══════════════════════════════════════════════════════════════
对标 CODEX.EXE v0.145.0:
  - opentelemetry-otlp-0.31.0          → OTLP Trace/Metric 导出
  - HttpTracesClient.ExportSucceeded    → HTTP OTLP exporter
  - OTEL_EXPORTER_OTLP_ENDPOINT        → 环境变量控制
  - MeterProvider / TracerProvider      → Provider 抽象
  - session_telemetry.rs               → 会话遥测记录

核心能力:
  1. Trace Spans — 工具调用 / LLM 调用 / 会话生命周期
  2. Metrics — 耗时 / Token 用量 / 错误率 / 成功率
  3. OTLP Export — HTTP JSON 导出到任何兼容后端
  4. Prometheus — /metrics 端点
  5. JSON File — 本地文件导出（默认）

对标:
  CODEX.EXE 使用 OpenTelemetry Rust SDK 记录:
    - plugin_install 事件
    - timing_metrics (responses_duration_excl_engine_and_client_)
    - session_telemetry
    - OTEL_METRIC_EXPORT_INTERVAL

用法:
  # CLI
  python telemetry.py trace --name "tool_call" --attrs '{"tool":"Bash"}'
  python telemetry.py metric --name "token_usage" --value 1500
  python telemetry.py report

  # MCP Server
  python telemetry.py --mcp
"""

import asyncio
import json
import os
import sys
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# ── 配置 ──────────────────────────────────────────────────────

DEFAULT_TELEMETRY_DIR = Path(__file__).resolve().parent.parent.parent.parent / "telemetry_data"

ENV_OTLP_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
ENV_OTLP_HEADERS = "OTEL_EXPORTER_OTLP_HEADERS"
ENV_OTLP_INSECURE = "OTEL_EXPORTER_OTLP_INSECURE"
ENV_PROMETHEUS_HOST = "OTEL_EXPORTER_PROMETHEUS_HOST"
ENV_PROMETHEUS_PORT = "OTEL_EXPORTER_PROMETHEUS_PORT"
ENV_METRIC_INTERVAL = "OTEL_METRIC_EXPORT_INTERVAL"


class ExportFormat(str, Enum):
    JSON_FILE = "json_file"
    OTLP_HTTP = "otlp_http"
    PROMETHEUS = "prometheus"
    CONSOLE = "console"


class SpanKind(str, Enum):
    INTERNAL = "internal"
    CLIENT = "client"
    SERVER = "server"
    PRODUCER = "producer"
    CONSUMER = "consumer"


class SpanStatus(str, Enum):
    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


# ── 数据类 ────────────────────────────────────────────────────

@dataclass
class TelemetrySpan:
    """Trace Span — 对标 OTel Span"""
    id: str = ""
    trace_id: str = ""
    parent_id: str = ""
    name: str = ""
    kind: SpanKind = SpanKind.INTERNAL
    status: SpanStatus = SpanStatus.UNSET
    start_time: str = ""
    end_time: str = ""
    duration_ms: int = 0
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"span_{uuid.uuid4().hex[:16]}"
        if not self.trace_id:
            self.trace_id = f"trace_{uuid.uuid4().hex[:16]}"
        if not self.start_time:
            self.start_time = datetime.now(timezone.utc).isoformat()

    def finish(self, status: SpanStatus = SpanStatus.OK, error: str = ""):
        self.end_time = datetime.now(timezone.utc).isoformat()
        try:
            s = datetime.fromisoformat(self.start_time)
            e = datetime.fromisoformat(self.end_time)
            self.duration_ms = int((e - s).total_seconds() * 1000)
        except Exception:
            pass
        self.status = status
        self.error = error

    def add_event(self, name: str, attrs: Dict[str, Any] = None):
        self.events.append({
            "name": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attributes": attrs or {},
        })

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind.value,
            "status": self.status.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "events": self.events,
            "error": self.error,
        }


@dataclass
class TelemetryMetric:
    """指标 — 对标 OTel Metric"""
    name: str = ""
    value: float = 0.0
    unit: str = ""
    timestamp: str = ""
    labels: Dict[str, str] = field(default_factory=dict)
    type: str = "gauge"  # gauge | counter | histogram

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "timestamp": self.timestamp,
            "labels": self.labels,
            "type": self.type,
        }


# ── 遥测引擎 ──────────────────────────────────────────────────

class TelemetryEngine:
    """
    OpenTelemetry 遥测引擎 — 对标 CODEX.EXE 的 OTEL 栈

    架构:
      SpanProcessor → Exporters (JSON / OTLP / Prometheus / Console)
      MetricReader  → PeriodicExportingMetricReader

    对标:
      CODEX.EXE:
        - session_telemetry.rs → 会话级 Span
        - timing_metrics → 计时指标
        - plugin_install 事件
        - MeterProvider / TracerProvider
    """

    def __init__(
        self,
        export_format: ExportFormat = ExportFormat.JSON_FILE,
        export_dir: str = "",
        otlp_endpoint: str = "",
        prometheus_host: str = "127.0.0.1",
        prometheus_port: int = 9464,
    ):
        self.export_format = export_format
        self.export_dir = Path(export_dir or DEFAULT_TELEMETRY_DIR)
        self.otlp_endpoint = otlp_endpoint or os.environ.get(ENV_OTLP_ENDPOINT, "")
        self.prometheus_host = prometheus_host or os.environ.get(ENV_PROMETHEUS_HOST, "127.0.0.1")
        self.prometheus_port = int(os.environ.get(ENV_PROMETHEUS_PORT, str(prometheus_port)))

        # 存储
        self._spans: List[TelemetrySpan] = []
        self._metrics: List[TelemetryMetric] = []
        self._counters: Dict[str, float] = defaultdict(float)
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

        # 当前 trace
        self._active_trace_id: str = ""
        self._active_spans: Dict[str, TelemetrySpan] = {}

        # 统计
        self._export_count = 0
        self._last_export = ""
        self._span_count = 0
        self._metric_count = 0

        # 确保导出目录存在
        self.export_dir.mkdir(parents=True, exist_ok=True)

        # ── OpenTelemetry SDK 集成 ──
        self._otlp_tracer = None
        self._otlp_meter = None
        self._init_otel_sdk()

        # ── Prometheus HTTP 服务器 ──
        self._promhttp_server = None
        self._promhttp_thread = None
        self._promhttp_registry = None

    def _init_otel_sdk(self):
        """初始化 OpenTelemetry SDK — 使用真实 exporter"""
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider as SdkTracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource

            # 仅在有 endpoint 时初始化真实 SDK
            if self.otlp_endpoint:
                resource = Resource.create({
                    "service.name": "deepcode",
                    "service.version": "1.0.0",
                })
                provider = SdkTracerProvider(resource=resource)
                exporter = OTLPSpanExporter(
                    endpoint=f"{self.otlp_endpoint}/v1/traces",
                    headers=self._parse_otlp_headers(),
                    timeout=5,
                )
                provider.add_span_processor(BatchSpanProcessor(exporter))
                self._otlp_tracer = provider.get_tracer("deepcode-telemetry")
                print(f"[telemetry] OTLP SDK initialized → {self.otlp_endpoint}", file=sys.stderr, flush=True)
        except ImportError:
            pass  # SDK not installed, fall back to custom engine
        except Exception as e:
            print(f"[telemetry] OTLP SDK init failed: {e}", file=sys.stderr, flush=True)

    def _parse_otlp_headers(self) -> dict:
        """解析 OTEL_EXPORTER_OTLP_HEADERS 环境变量"""
        headers = {}
        raw = os.environ.get(ENV_OTLP_HEADERS, "")
        if raw:
            for pair in raw.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    headers[k.strip()] = v.strip()
        return headers

    # ── Trace API ──────────────────────────────────────────

    def start_trace(self, name: str = "") -> str:
        """开始一个新的 Trace"""
        self._active_trace_id = f"trace_{uuid.uuid4().hex[:16]}"
        if name:
            self.start_span(name, kind=SpanKind.INTERNAL)
        return self._active_trace_id

    def start_span(self, name: str, kind: SpanKind = SpanKind.INTERNAL,
                   parent_id: str = "", attributes: Dict[str, Any] = None) -> str:
        """创建 Span — 对标 OTel Tracer.start_span"""
        if not self._active_trace_id:
            self._active_trace_id = f"trace_{uuid.uuid4().hex[:16]}"

        span = TelemetrySpan(
            name=name,
            kind=kind,
            trace_id=self._active_trace_id,
            parent_id=parent_id,
            attributes=attributes or {},
        )

        with self._lock:
            self._spans.append(span)
            self._active_spans[span.id] = span
            self._span_count += 1

        return span.id

    def end_span(self, span_id: str, status: SpanStatus = SpanStatus.OK,
                 error: str = ""):
        """结束 Span"""
        with self._lock:
            span = self._active_spans.pop(span_id, None)
        if span:
            span.finish(status, error)

    def add_event(self, span_id: str, event_name: str,
                  attributes: Dict[str, Any] = None):
        """添加事件到 Span — 对标 OTel Span.add_event"""
        with self._lock:
            span = self._active_spans.get(span_id)
        if span:
            span.add_event(event_name, attributes)

    # ── Metrics API ────────────────────────────────────────

    def record_metric(self, name: str, value: float, unit: str = "",
                      labels: Dict[str, str] = None, metric_type: str = "gauge"):
        """记录指标 — 对标 OTel Meter.record"""
        metric = TelemetryMetric(
            name=name, value=value, unit=unit,
            labels=labels or {}, type=metric_type,
        )
        with self._lock:
            self._metrics.append(metric)
            self._metric_count += 1

    def increment_counter(self, name: str, delta: float = 1.0):
        """递增计数器"""
        with self._lock:
            self._counters[name] += delta
        self.record_metric(name, self._counters[name], metric_type="counter")

    def record_histogram(self, name: str, value: float, unit: str = ""):
        """记录直方图值"""
        with self._lock:
            self._histograms[name].append(value)
        vals = self._histograms[name]
        self.record_metric(
            name, value, unit,
            labels={"avg": str(sum(vals) / len(vals)),
                    "min": str(min(vals)), "max": str(max(vals)),
                    "count": str(len(vals))},
            metric_type="histogram",
        )

    def record_tool_call(self, tool_name: str, duration_ms: int,
                         success: bool, token_usage: int = 0):
        """记录工具调用 — 对标 CODEX plugin_install.tool_*"""
        self.increment_counter(f"tool.{tool_name}.calls")
        self.record_histogram(f"tool.{tool_name}.duration_ms", duration_ms)
        if not success:
            self.increment_counter(f"tool.{tool_name}.errors")
        if token_usage:
            self.record_histogram("llm.token_usage", token_usage)

    def record_session(self, session_id: str, duration_ms: int,
                       tool_calls: int, commands: int):
        """记录会话 — 对标 CODEX session_telemetry"""
        self.record_histogram("session.duration_ms", duration_ms)
        self.record_metric("session.tool_calls", tool_calls, metric_type="counter")
        self.record_metric("session.commands", commands, metric_type="counter")

    # ── 导出 ──────────────────────────────────────────────

    def export(self) -> Dict[str, Any]:
        """导出遥测数据 — 对标 OTel Exporter.export"""
        with self._lock:
            data = {
                "service": "deepcode",
                "version": "1.0.0",
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "spans": [s.to_dict() for s in self._spans],
                "metrics": [m.to_dict() for m in self._metrics],
                "counters": dict(self._counters),
                "summary": self.summary(),
            }

        if self.export_format == ExportFormat.JSON_FILE:
            self._export_json_file(data)
        elif self.export_format == ExportFormat.OTLP_HTTP:
            self._export_otlp(data)
        elif self.export_format == ExportFormat.CONSOLE:
            print(json.dumps(data, indent=2, default=str), flush=True)

        self._export_count += 1
        self._last_export = datetime.now(timezone.utc).isoformat()
        return data

    def _export_json_file(self, data: Dict):
        """导出到 JSON 文件"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self.export_dir / f"telemetry_{timestamp}.json"
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str, ensure_ascii=False)

    def _export_otlp(self, data: Dict):
        """导出到 OTLP HTTP endpoint"""
        endpoint = self.otlp_endpoint
        if not endpoint:
            return

        import urllib.request

        body = json.dumps({
            "resourceSpans": [{
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "deepcode"}},
                    ]
                },
                "scopeSpans": [{
                    "spans": data["spans"],
                }],
            }],
        }).encode()

        headers = {"Content-Type": "application/json"}
        otlp_headers = os.environ.get(ENV_OTLP_HEADERS, "")
        if otlp_headers:
            for pair in otlp_headers.split(","):
                k, v = pair.split("=", 1)
                headers[k.strip()] = v.strip()

        try:
            req = urllib.request.Request(
                f"{endpoint}/v1/traces", data=body, headers=headers,
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

    def start_prometheus_server(self) -> bool:
        """启动 Prometheus HTTP /metrics 端点 (prometheus_client)"""
        if self._promhttp_server is not None:
            return True
        try:
            from prometheus_client import start_http_server, REGISTRY, Counter, Gauge, Histogram
            self._promhttp_registry = REGISTRY
            self._prom_counter_calls = Counter("deepcode_tool_calls_total",
                                               "Total tool calls", ["tool", "status"])
            self._prom_gauge_active = Gauge("deepcode_active_spans",
                                            "Currently active spans")
            self._prom_histogram_duration = Histogram("deepcode_tool_duration_ms",
                                                      "Tool call duration (ms)",
                                                      ["tool"], buckets=[10, 50, 100, 500, 1000, 5000, 10000])
            def _serve():
                try:
                    start_http_server(self.prometheus_port, addr=self.prometheus_host)
                    print(f"[telemetry] Prometheus /metrics → http://{self.prometheus_host}:{self.prometheus_port}",
                          file=sys.stderr, flush=True)
                except Exception as e:
                    print(f"[telemetry] Prometheus server failed: {e}", file=sys.stderr, flush=True)
            self._promhttp_thread = threading.Thread(target=_serve, daemon=True)
            self._promhttp_thread.start()
            self._promhttp_server = True
            return True
        except ImportError:
            print("[telemetry] prometheus_client not installed", file=sys.stderr, flush=True)
            return False
        except Exception as e:
            print(f"[telemetry] Prometheus init error: {e}", file=sys.stderr, flush=True)
            return False

    def export_prometheus(self) -> str:
        """导出 Prometheus 格式 (同时同步到 prometheus_client 指标)"""
        try:
            if hasattr(self, '_prom_counter_calls'):
                for n, v in self._counters.items():
                    tool = n.replace("tool.", "").replace(".calls", "").replace(".errors", "")
                    status = "error" if "error" in n else "success"
                    self._prom_counter_calls.labels(tool=tool, status=status).inc(v)
                if hasattr(self, '_prom_gauge_active'):
                    self._prom_gauge_active.set(len(self._active_spans))
        except Exception:
            pass

        lines = []
        for name, value in self._counters.items():
            safe = name.replace(".", "_").replace("-", "_")
            lines.append(f"# TYPE deepcode_{safe} counter")
            lines.append(f"deepcode_{safe} {value}")

        for name, metrics in self._histograms.items():
            safe = name.replace(".", "_").replace("-", "_")
            if metrics:
                lines.append(f"# TYPE deepcode_{safe} summary")
                lines.append(f"deepcode_{safe}_sum {sum(metrics)}")
                lines.append(f"deepcode_{safe}_count {len(metrics)}")

        lines.extend([
            f"# TYPE deepcode_span_count gauge",
            f"deepcode_span_count {self._span_count}",
            f"# TYPE deepcode_metric_count gauge",
            f"deepcode_metric_count {self._metric_count}",
            f"# TYPE deepcode_export_count counter",
            f"deepcode_export_count {self._export_count}",
        ])

        return "\n".join(lines) + "\n"

    # ── 统计 ──────────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        """生成摘要"""
        with self._lock:
            success_spans = sum(1 for s in self._spans if s.status == SpanStatus.OK)
            error_spans = sum(1 for s in self._spans if s.status == SpanStatus.ERROR)
            avg_duration = int(sum(s.duration_ms for s in self._spans if s.duration_ms) /
                              max(1, sum(1 for s in self._spans if s.duration_ms)))

            return {
                "total_spans": self._span_count,
                "active_spans": len(self._active_spans),
                "success_spans": success_spans,
                "error_spans": error_spans,
                "avg_duration_ms": avg_duration,
                "total_metrics": self._metric_count,
                "counters": dict(self._counters),
                "export_count": self._export_count,
                "last_export": self._last_export,
            }

    def reset(self):
        """重置所有数据"""
        with self._lock:
            self._spans.clear()
            self._metrics.clear()
            self._counters.clear()
            self._histograms.clear()
            self._active_spans.clear()
            self._span_count = 0
            self._metric_count = 0


# ── 全局单例 ──────────────────────────────────────────────────

_telemetry: Optional[TelemetryEngine] = None


def get_telemetry() -> TelemetryEngine:
    global _telemetry
    if _telemetry is None:
        fmt_str = os.environ.get("DEEPCODE_TELEMETRY_FORMAT", "json_file")
        try:
            fmt = ExportFormat(fmt_str)
        except ValueError:
            fmt = ExportFormat.JSON_FILE
        _telemetry = TelemetryEngine(export_format=fmt)
    return _telemetry


# ── 便捷上下文管理器 ──────────────────────────────────────────

class TraceSpan:
    """Span 上下文管理器 — 对标 OTel with tracer.start_as_current_span()"""

    def __init__(self, name: str, kind: SpanKind = SpanKind.INTERNAL,
                 attributes: Dict[str, Any] = None, parent_id: str = ""):
        self._t = get_telemetry()
        self._span_id = self._t.start_span(name, kind, parent_id, attributes)

    def __enter__(self):
        return self._span_id

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._t.end_span(self._span_id, SpanStatus.ERROR, str(exc_val))
        else:
            self._t.end_span(self._span_id, SpanStatus.OK)

    def add_event(self, name: str, attrs: Dict[str, Any] = None):
        self._t.add_event(self._span_id, name, attrs)


# ── MCP Server ─────────────────────────────────────────────────

async def run_mcp():
    """MCP Server 模式"""
    t = get_telemetry()

    TOOLS = {
        "telemetry_start_trace": {
            "description": "开始一个新的 Trace",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
            },
        },
        "telemetry_start_span": {
            "description": "创建 Span — 对标 OTel Tracer.start_span",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "description": "internal/client/server"},
                    "attributes": {"type": "object"},
                },
                "required": ["name"],
            },
        },
        "telemetry_end_span": {
            "description": "结束 Span",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "span_id": {"type": "string"},
                    "error": {"type": "string"},
                },
                "required": ["span_id"],
            },
        },
        "telemetry_record_metric": {
            "description": "记录指标",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "number"},
                    "unit": {"type": "string"},
                    "labels": {"type": "object"},
                },
                "required": ["name", "value"],
            },
        },
        "telemetry_tool_call": {
            "description": "记录工具调用遥测 — 对标 CODEX plugin_install 事件",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string"},
                    "duration_ms": {"type": "number"},
                    "success": {"type": "boolean"},
                    "token_usage": {"type": "number"},
                },
                "required": ["tool_name", "duration_ms", "success"],
            },
        },
        "telemetry_export": {
            "description": "导出遥测数据",
            "inputSchema": {"type": "object", "properties": {}},
        },
        "telemetry_summary": {
            "description": "获取遥测摘要",
            "inputSchema": {"type": "object", "properties": {}},
        },
        "telemetry_prometheus": {
            "description": "导出 Prometheus 格式指标",
            "inputSchema": {"type": "object", "properties": {}},
        },
        "telemetry_start_prometheus": {
            "description": "启动 Prometheus HTTP /metrics 服务器 (prometheus_client)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "port": {"type": "number", "description": "监听端口 (默认 9464)"},
                },
            },
        },
    }

    # ── 标准 MCP JSON-RPC 2.0 stdio ──
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            err = {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": f"Parse error: {e}"},
                "id": None,
            }
            sys.stdout.write(json.dumps(err, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        method = req.get("method", "")
        params = req.get("params", {})
        rid = req.get("id", "")

        # ── initialize ──
        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "deepcode-telemetry", "version": "1.0.0"},
                },
            }
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        # ── notifications/initialized ──
        if method == "notifications/initialized":
            continue

        if method == "tools/list":
            print(json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "result": {"tools": [
                    {"name": k, "description": v["description"],
                     "inputSchema": v["inputSchema"]}
                    for k, v in TOOLS.items()
                ]},
            }), flush=True)

        elif method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {})
            result = {}

            try:
                if name == "telemetry_start_trace":
                    tid = t.start_trace(args.get("name", ""))
                    result = {"trace_id": tid}

                elif name == "telemetry_start_span":
                    kind = SpanKind(args.get("kind", "internal"))
                    sid = t.start_span(args["name"], kind,
                                       attributes=args.get("attributes"))
                    result = {"span_id": sid}

                elif name == "telemetry_end_span":
                    t.end_span(args["span_id"], error=args.get("error", ""))
                    result = {"ok": True}

                elif name == "telemetry_record_metric":
                    t.record_metric(
                        args["name"], args["value"],
                        unit=args.get("unit", ""),
                        labels=args.get("labels"),
                    )
                    result = {"ok": True}

                elif name == "telemetry_tool_call":
                    t.record_tool_call(
                        args["tool_name"], int(args["duration_ms"]),
                        bool(args["success"]),
                        token_usage=int(args.get("token_usage", 0)),
                    )
                    result = {"ok": True}

                elif name == "telemetry_export":
                    data = t.export()
                    result = t.summary()

                elif name == "telemetry_summary":
                    result = t.summary()

                elif name == "telemetry_prometheus":
                    result = {"metrics": t.export_prometheus()}

                elif name == "telemetry_start_prometheus":
                    port = args.get("port", t.prometheus_port)
                    t.prometheus_port = int(port)
                    ok = t.start_prometheus_server()
                    result = {"ok": ok, "url": f"http://{t.prometheus_host}:{t.prometheus_port}/metrics"}

                else:
                    result = {"error": f"Unknown: {name}"}

            except Exception as e:
                result = {"error": str(e)}

            print(json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "result": {
                    "content": [{"type": "text",
                                 "text": json.dumps(result, ensure_ascii=False)}],
                },
            }), flush=True)


# ── CLI ────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="DeepCode Telemetry")
    parser.add_argument("--mcp", action="store_true", help="MCP Server 模式")
    parser.add_argument("--format", choices=["json_file", "otlp_http", "prometheus", "console"],
                        default="json_file")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("trace", help="创建 Trace")
    p.add_argument("--name", required=True)
    p.add_argument("--attrs", default="{}")

    p = sub.add_parser("metric", help="记录指标")
    p.add_argument("--name", required=True)
    p.add_argument("--value", type=float, required=True)
    p.add_argument("--unit", default="")

    sub.add_parser("report", help="导出并显示报告")
    sub.add_parser("summary", help="显示摘要")
    sub.add_parser("prometheus", help="Prometheus 格式")
    sub.add_parser("demo", help="运行演示")

    args = parser.parse_args()

    if args.mcp:
        asyncio.run(run_mcp())
        return

    t = get_telemetry()
    t.export_format = ExportFormat(args.format)

    if args.command == "trace":
        attrs = json.loads(args.attrs)
        sid = t.start_span(args.name, attributes=attrs)
        t.end_span(sid)
        print(f"Span: {sid}")

    elif args.command == "metric":
        t.record_metric(args.name, args.value, unit=args.unit)
        print(f"Metric: {args.name}={args.value}{args.unit}")

    elif args.command == "report":
        data = t.export()
        print(json.dumps(t.summary(), indent=2, default=str))

    elif args.command == "summary":
        print(json.dumps(t.summary(), indent=2, default=str))

    elif args.command == "prometheus":
        print(t.export_prometheus())

    elif args.command in ("demo", None):
        # Demo
        print("=== Telemetry Demo ===\n")
        t.start_trace("demo")

        with TraceSpan("tool_call_bash", SpanKind.CLIENT,
                       {"tool.name": "Bash", "tool.command": "ls -la"}) as sid:
            t.add_event(sid, "command_started", {"cwd": os.getcwd()})
            time.sleep(0.1)
            t.add_event(sid, "command_completed", {"exit_code": 0, "stdout_len": 42})

        with TraceSpan("llm_call", SpanKind.CLIENT,
                       {"model": "deepseek-v4-pro", "prompt_len": 512}) as sid2:
            time.sleep(0.05)
            t.record_metric("llm.token_usage", 1500, labels={"model": "deepseek-v4-pro"})

        t.record_tool_call("Bash", 100, True, 0)
        t.record_tool_call("Read", 15, True, 500)
        t.record_tool_call("Write", 30, False, 200)

        t.record_session("demo_session", 5000, 3, 1)

        print(json.dumps(t.summary(), indent=2, default=str))
        print(f"\n--- Prometheus ---\n{t.export_prometheus()}")


if __name__ == "__main__":
    main()
