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
    chip_identifier = None
    pending = None
    incomplete = 0
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
            incomplete += pending is not None
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
            identifiers = cells[0].split()
            chip_identifier = identifiers[1].lower() if len(identifiers) > 1 else None
            continue
        # A3 prints "Chip Phy-ID"; some drivers print "Chip Device".
        # These numeric rows must be handled before "NPU Name" rows.
        ids = re.fullmatch(r"(\d+)(?:\s+(\d+))?", cells[0])
        if ids:
            if pending and bottom_headers:
                values = metric_values(cells[-1], bottom_headers)
                utilization = number(values.get("AICore(%)", ""))
                if utilization is not None and not 0 <= utilization <= 100:
                    utilization = None
                identifier = int(ids[2]) if ids[2] is not None else None
                devices.append({**pending, "chip_id": int(ids[1]), "bus_id": cells[1],
                                "phy_id": identifier if chip_identifier == "phy-id" else None,
                                "device_id": identifier if chip_identifier == "device" else None,
                                "aicore_percent": utilization,
                                "memory": memory(values.get("Memory-Usage(MB)", "")),
                                "hbm": memory(values.get("HBM-Usage(MB)", ""))})
                pending = None
            continue
        match = re.fullmatch(r"(\d+)\s+(\S.*)", cells[0])
        if match and top_headers:
            incomplete += pending is not None
            values = metric_values(cells[-1], top_headers)
            pending = {"npu_id": int(match.group(1)), "name": match.group(2).strip(),
                       "health": cells[1], "power_w": number(values.get("Power(W)", "")),
                       "temperature_c": number(values.get("Temp(C)", ""))}
            continue
    incomplete += pending is not None
    warning = "未识别到 NPU 表格，请查看原始输出。" if not devices else (
        f"有 {incomplete} 条 NPU / die 信息未完整解析，请查看原始输出。" if incomplete else None)
    return {"version": version, "devices": devices, "processes": processes, "warning": warning}
