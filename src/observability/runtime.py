"""Process resource metrics and optional NVIDIA NVML device telemetry."""

import logging

import psutil
from opentelemetry.metrics import Observation

logger = logging.getLogger(__name__)


class RuntimeMetrics:
    def __init__(self, meter, gpu_enabled: bool = False):
        self.process = psutil.Process()
        self.nvml = None
        self.handles = []
        self.warned = set()
        if gpu_enabled:
            import pynvml

            try:
                pynvml.nvmlInit()
                self.handles = [pynvml.nvmlDeviceGetHandleByIndex(i)
                                for i in range(pynvml.nvmlDeviceGetCount())]
                self.nvml = pynvml
                if not self.handles:
                    logger.warning("GPU telemetry requested but no NVIDIA devices were found")
            except pynvml.NVMLError as error:
                logger.warning("GPU telemetry unavailable: %s", error)
        meter.create_observable_gauge("gridcast.process.memory", unit="By",
                                      callbacks=[self.memory])
        meter.create_observable_counter("gridcast.process.cpu_time", unit="s",
                                        callbacks=[self.cpu])
        meter.create_observable_gauge("gridcast.process.threads",
                                      callbacks=[self.threads])
        meter.create_observable_gauge("gridcast.ml.cost_rate_configured",
                                      callbacks=[self.cost_configured])
        meter.create_observable_gauge("gridcast.gpu.monitoring_available",
                                      callbacks=[lambda _: [Observation(int(bool(self.handles)))]])
        for name, unit in (("utilization", "1"), ("memory", "By"), ("power", "W"),
                           ("temperature", "Cel")):
            meter.create_observable_gauge(f"gridcast.gpu.{name}", unit=unit,
                                          callbacks=[self.gpu_callback(name)])

    def memory(self, _):
        return [Observation(self.process.memory_info().rss)]

    def cpu(self, _):
        times = self.process.cpu_times()
        return [Observation(times.user + times.system)]

    def threads(self, _):
        return [Observation(self.process.num_threads())]

    def cost_configured(self, _):
        from src.observability.operations import compute_rate

        return [Observation(int(compute_rate() is not None))]

    def gpu_callback(self, field: str):
        def observe(_):
            for index, handle in enumerate(self.handles):
                try:
                    if field == "utilization":
                        value = self.nvml.nvmlDeviceGetUtilizationRates(handle).gpu / 100
                    elif field == "memory":
                        value = self.nvml.nvmlDeviceGetMemoryInfo(handle).used
                    elif field == "power":
                        value = self.nvml.nvmlDeviceGetPowerUsage(handle) / 1000
                    else:
                        value = self.nvml.nvmlDeviceGetTemperature(handle, 0)
                    yield Observation(value, {"gpu.index": str(index)})
                except self.nvml.NVMLError as error:
                    if (index, field) not in self.warned:
                        logger.warning("GPU %s %s metric unavailable: %s", index, field, error)
                        self.warned.add((index, field))
        return observe
