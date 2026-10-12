"""Parse the paired-row tables printed by Ascend npu-smi info."""

import re

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PAIR = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")
NUMBER = re.compile(r"^\d+(?:\.\d+)?$")


def number(value):
    value = value.strip().rstrip("%")
    return float(value) if NUMBER.fullmatch(value) else None


def memory(value):
    match = PAIR.fullmatch(value.strip())
    if not match:
        return {"used_mb": None, "total_mb": None, "percent": None}
    used, total = map(float, match.groups())
    percent = round(used * 100 / total, 2) if total > 0 else None
    return {"used_mb": used, "total_mb": total, "percent": percent}


def metric_values(value, headers):
    # A memory pair is one column even when spaces surround '/'.
    tokens = re.findall(r"\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?|\S+", value)
    return dict(zip(headers, tokens))


def parse_info(output):
    """Return devices and processes; unknown formats retain the raw output."""
    devices, processes = [], []
    top_headers, bottom_headers = [], []
    pending = None
    process_mode = False
    version = None
    for line in ANSI.sub("", output).splitlines():
        if "npu-smi" in line and "Version:" in line:
            match = re.search(r"npu-smi\s+(\S+)", line)
            version = match.group(1) if match else None
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        joined = " ".join(cells)
        if "process id" in joined.lower():
            process_mode = True
            pending = None
            continue
        if process_mode:
            ids = re.fullmatch(r"(\d+)\s+(\d+)", cells[0])
            if ids and len(cells) >= 4 and cells[1].isdigit() and number(cells[3]) is not None:
                container_pid = int(cells[4]) if len(cells) > 4 and cells[4].isdigit() else None
                processes.append({"npu_id": int(ids.group(1)), "chip_id": int(ids.group(2)),
                                  "pid": int(cells[1]), "name": cells[2],
                                  "memory_mb": number(cells[3]), "container_pid": container_pid})
                continue
            match = re.fullmatch(r"(\d+)\s+(\d+)\s+(\d+)\s+(.+?)\s+(\d+(?:\.\d+)?)", joined)
            if match:
                npu, chip, pid, name, used = match.groups()
                processes.append({"npu_id": int(npu), "chip_id": int(chip),
                                  "pid": int(pid), "name": name, "memory_mb": float(used), "container_pid": None})
            continue
        if len(cells) < 3:
            continue
        if "Power(W)" in cells[-1] and "Temp(C)" in cells[-1]:
            top_headers = re.findall(r"[A-Za-z][A-Za-z0-9_-]*\([^)]*\)", cells[-1])
            continue
        if "AICore(%)" in cells[-1]:
            bottom_headers = re.findall(r"[A-Za-z][A-Za-z0-9_-]*\([^)]*\)", cells[-1])
            continue
        match = re.fullmatch(r"(\d+)\s+(\S.*)", cells[0])
        if match and top_headers:
            values = metric_values(cells[-1], top_headers)
            pending = {"npu_id": int(match.group(1)), "name": match.group(2).strip(),
                       "health": cells[1], "power_w": number(values.get("Power(W)", "")),
                       "temperature_c": number(values.get("Temp(C)", ""))}
            continue
        if pending and re.fullmatch(r"\d+", cells[0]) and bottom_headers:
            values = metric_values(cells[-1], bottom_headers)
            utilization = number(values.get("AICore(%)", ""))
            if utilization is not None and not 0 <= utilization <= 100:
                utilization = None
            devices.append({**pending, "chip_id": int(cells[0]), "bus_id": cells[1],
                            "aicore_percent": utilization,
                            "memory": memory(values.get("Memory-Usage(MB)", "")),
                            "hbm": memory(values.get("HBM-Usage(MB)", ""))})
            pending = None
    warning = None if devices else "未识别到 NPU 表格，请查看原始输出。"
    return {"version": version, "devices": devices, "processes": processes, "warning": warning}
