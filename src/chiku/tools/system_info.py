"""Read-only system information tool."""

from __future__ import annotations

import platform
import time
from datetime import datetime
from typing import Literal

import psutil
from pydantic import BaseModel, ConfigDict

from chiku.hardware import cpu_name, gpu_info
from chiku.tools.base import Tool, ToolContext, ToolResult

Topic = Literal["all", "battery", "cpu", "memory", "disk", "gpu", "os", "time"]


class SystemInfoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: Topic = "all"


class SystemInfoTool(Tool):
    name = "get_system_info"
    description = (
        "Read basic information about this computer: battery, CPU load, memory, disk space, GPU, "
        "Windows version, uptime and the current time."
    )
    Input = SystemInfoInput
    timeout_s = 10.0

    def run(self, args: SystemInfoInput, ctx: ToolContext) -> ToolResult:
        t = args.topic
        data: dict[str, object] = {}
        if t in ("all", "time"):
            data["local_time"] = datetime.now().strftime("%A %d %B %Y, %H:%M")
            data["uptime_hours"] = round((time.time() - psutil.boot_time()) / 3600, 1)
        if t in ("all", "os"):
            data["os"] = f"{platform.system()} {platform.release()} (build {platform.version()})"
        if t in ("all", "cpu"):
            data["cpu"] = cpu_name()
            data["cpu_load_percent"] = psutil.cpu_percent(interval=0.3)
            data["logical_cores"] = psutil.cpu_count()
        if t in ("all", "memory"):
            vm = psutil.virtual_memory()
            data["ram_total_gb"] = round(vm.total / 2**30, 1)
            data["ram_used_percent"] = vm.percent
        if t in ("all", "disk"):
            du = psutil.disk_usage("C:\\")
            data["disk_c_free_gb"] = round(du.free / 2**30, 1)
            data["disk_c_total_gb"] = round(du.total / 2**30, 1)
        if t in ("all", "battery"):
            b = psutil.sensors_battery()
            if b is None:
                data["battery"] = "no battery detected"
            else:
                data["battery_percent"] = round(b.percent)
                data["plugged_in"] = bool(b.power_plugged)
        if t in ("all", "gpu"):
            data["gpus"] = gpu_info() or "no NVIDIA GPU reported"
        return ToolResult.success("System information collected.", **data)
