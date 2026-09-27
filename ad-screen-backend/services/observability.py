"""
可观测性指标注册中心
------------------------------------------------------------------
轻量级自实现 Prometheus 文本格式指标暴露（不依赖 prometheus_client），
避免引入新依赖、避免医院内网无法 pip 安装的窘境。

支持的指标类型：
- Counter（累计计数器）：http_requests_total / model_inference_total
- Histogram（直方图）：http_request_duration_ms
- Gauge（瞬时值）：process_start_time_seconds / batch_active_tasks

使用方式：
    from services.observability import observe_request, observe_inference, metrics_registry
    observe_request(path, method, status, elapsed_ms)
    metrics_registry.render()  # 输出 Prometheus 文本格式

设计要点：
- 线程安全：所有计数操作加锁
- 路径归一化：合并 /api/case/{id} 类动态路径为 /api/case/:id，
  避免高基数标签撑爆内存与破坏聚合
- 直方图分桶：5/10/25/50/100/250/500/1000/2500/5000/10000 ms（覆盖医疗系统典型耗时分布）
"""
import threading
import time

# 进程启动时间（秒 epoch），用于 Prometheus uptime 计算
_PROCESS_START = time.time()

# 直方图分桶边界（ms），覆盖医疗系统典型请求耗时分布
_HIST_BUCKETS_MS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)


def _normalize_path(path: str) -> str:
    """
    路径归一化：合并动态路径段为 :param。
    例如 /api/case/abc-123/run → /api/case/:id/run
    减少 Prometheus 标签基数，避免内存膨胀与聚合破坏。
    """
    if not path or not path.startswith("/api/"):
        # 非业务路径（/、/api、/assets/*）原样返回，便于区分
        return path or "/"

    parts = path.split("/")
    norm = []
    for seg in parts[1:]:  # 跳过开头的空串
        if not seg:
            norm.append("")
            continue
        # UUID（8-4-4-4-12）/ 长哈希（≥16 字符）/ 纯数字 → :id
        if (
            len(seg) >= 16
            or seg.isdigit()
            or (len(seg) == 36 and seg.count("-") == 4)  # UUID v4
        ):
            norm.append(":id")
        else:
            norm.append(seg)
    return "/" + "/".join(norm)


class MetricsRegistry:
    """
    轻量级指标注册中心（线程安全）。
    仅实现 Prometheus 文本暴露所需的最小逻辑，不做客户端聚合（由 Prometheus 服务端聚合）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Counter: {(name, labels_dict)} → 累计值
        self._counters: dict[tuple, dict] = {}
        # Histogram: {(name, labels_dict)} → {buckets: [count, ...], sum, count}
        self._histograms: dict[tuple, dict] = {}
        # Gauge: {(name, labels_dict)} → 瞬时值
        self._gauges: dict[tuple, dict] = {}

    def inc_counter(self, name: str, labels: dict, value: float = 1.0) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            entry = self._counters.setdefault(key, {"labels": labels, "value": 0.0})
            entry["value"] += value

    def observe_histogram(self, name: str, labels: dict, value_ms: float) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            entry = self._histograms.get(key)
            if entry is None:
                entry = {
                    "labels": labels,
                    "buckets": [0] * len(_HIST_BUCKETS_MS),
                    "sum": 0.0,
                    "count": 0,
                }
                self._histograms[key] = entry
            # 分桶累加（每个 <= le 的 bucket 都 +1）
            for i, le in enumerate(_HIST_BUCKETS_MS):
                if value_ms <= le:
                    entry["buckets"][i] += 1
            entry["sum"] += value_ms
            entry["count"] += 1

    def set_gauge(self, name: str, labels: dict, value: float) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._gauges[key] = {"labels": labels, "value": value}

    def render(self) -> str:
        """输出 Prometheus 文本格式（供 /api/metrics 端点返回）"""
        with self._lock:
            lines = []

            # 1. 进程启动时间（Gauge）
            lines.append(f"# HELP process_start_time_seconds 进程启动时间（Unix epoch 秒）")
            lines.append(f"# TYPE process_start_time_seconds gauge")
            lines.append(f"process_start_time_seconds {_PROCESS_START}")

            # 2. HTTP 请求数（Counter）
            lines.append(f"# HELP http_requests_total HTTP 请求累计计数")
            lines.append(f"# TYPE http_requests_total counter")
            for (name, _), entry in sorted(self._counters.items()):
                if name == "http_requests_total":
                    label_str = self._format_labels(entry["labels"])
                    lines.append(f"http_requests_total{label_str} {entry['value']}")

            # 3. HTTP 请求耗时（Histogram）
            lines.append(f"# HELP http_request_duration_ms HTTP 请求耗时（毫秒）")
            lines.append(f"# TYPE http_request_duration_ms histogram")
            for (name, _), entry in sorted(self._histograms.items()):
                if name == "http_request_duration_ms":
                    base_labels = entry["labels"]
                    cum = 0
                    for i, le in enumerate(_HIST_BUCKETS_MS):
                        cum = entry["buckets"][i]
                        labels = {**base_labels, "le": str(le)}
                        lines.append(
                            f"http_request_duration_ms_bucket{self._format_labels(labels)} {cum}"
                        )
                    # +Inf 桶
                    labels_inf = {**base_labels, "le": "+Inf"}
                    lines.append(
                        f"http_request_duration_ms_bucket{self._format_labels(labels_inf)} {entry['count']}"
                    )
                    # sum 与 count
                    lines.append(
                        f"http_request_duration_ms_sum{self._format_labels(base_labels)} {entry['sum']:.2f}"
                    )
                    lines.append(
                        f"http_request_duration_ms_count{self._format_labels(base_labels)} {entry['count']}"
                    )

            # 4. 模型推理次数（Counter，由 observe_inference 维护）
            lines.append(f"# HELP model_inference_total TransMF 模型推理累计次数")
            lines.append(f"# TYPE model_inference_total counter")
            for (name, _), entry in sorted(self._counters.items()):
                if name == "model_inference_total":
                    label_str = self._format_labels(entry["labels"])
                    lines.append(f"model_inference_total{label_str} {entry['value']}")

            # 5. 模型推理耗时（Histogram）
            lines.append(f"# HELP model_inference_duration_ms TransMF 推理耗时（毫秒）")
            lines.append(f"# TYPE model_inference_duration_ms histogram")
            for (name, _), entry in sorted(self._histograms.items()):
                if name == "model_inference_duration_ms":
                    base_labels = entry["labels"]
                    cum = 0
                    for i, le in enumerate(_HIST_BUCKETS_MS):
                        cum = entry["buckets"][i]
                        labels = {**base_labels, "le": str(le)}
                        lines.append(
                            f"model_inference_duration_ms_bucket{self._format_labels(labels)} {cum}"
                        )
                    labels_inf = {**base_labels, "le": "+Inf"}
                    lines.append(
                        f"model_inference_duration_ms_bucket{self._format_labels(labels_inf)} {entry['count']}"
                    )
                    lines.append(
                        f"model_inference_duration_ms_sum{self._format_labels(base_labels)} {entry['sum']:.2f}"
                    )
                    lines.append(
                        f"model_inference_duration_ms_count{self._format_labels(base_labels)} {entry['count']}"
                    )

            # 6. 其他 Gauge（活跃批量任务等）
            for (name, _), entry in sorted(self._gauges.items()):
                if name != "process_start_time_seconds":
                    lines.append(f"# HELP {name} gauge 指标")
                    lines.append(f"# TYPE {name} gauge")
                    label_str = self._format_labels(entry["labels"])
                    lines.append(f"{name}{label_str} {entry['value']}")

            return "\n".join(lines) + "\n"

    @staticmethod
    def _format_labels(labels: dict) -> str:
        if not labels:
            return ""
        items = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return "{" + items + "}"


# 全局单例
metrics_registry = MetricsRegistry()


def observe_request(path: str, method: str, status: int, elapsed_ms: float) -> None:
    """
    在请求结束时调用，记录 HTTP 指标。
    路径会被归一化以控制标签基数。
    """
    norm_path = _normalize_path(path)
    status_class = f"{status // 100}xx"
    labels = {
        "method": method,
        "path": norm_path,
        "status": str(status),
        "status_class": status_class,
    }
    metrics_registry.inc_counter("http_requests_total", labels)
    metrics_registry.observe_histogram("http_request_duration_ms", labels, elapsed_ms)


def observe_inference(elapsed_ms: float, device: str = "cpu", success: bool = True) -> None:
    """
    在模型推理结束时调用，记录推理指标。
    """
    labels = {
        "device": device,
        "result": "success" if success else "failure",
    }
    metrics_registry.inc_counter("model_inference_total", labels)
    metrics_registry.observe_histogram("model_inference_duration_ms", labels, elapsed_ms)


def set_gauge(name: str, value: float, **labels) -> None:
    """设置 Gauge 瞬时值（如批量任务活跃数）"""
    metrics_registry.set_gauge(name, labels, value)
