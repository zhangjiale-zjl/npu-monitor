"""Identify Ascend product generations from npu-smi model fields."""
import re


def board_fields(raw):
    fields = {}
    for line in raw.splitlines():
        match = re.match(r"^\s*(Chip Name|Chip Type|NPU Name)\s*:\s*(.*?)\s*$", line, re.I)
        if match:
            fields[match[1].lower()] = match[2]
    return fields


def model_generation(model, npu_name=""):
    model = re.sub(r"\s+", "", model).lower()
    if re.fullmatch(r"(?:ascend)?950(?:pr|dt)?\d*(?:_[0-9a-f]+)?", model):
        return "A5"
    if re.fullmatch(r"(?:ascend)?910c\d*", model):
        return "A3"
    if re.fullmatch(r"(?:ascend)?910(?:_93[0-9a-f]{2})?", model):
        if "_93" in model or re.fullmatch(r"93[0-9a-f]{2}", npu_name.lower()):
            return "A3"
    if re.fullmatch(r"(?:ascend)?(?:910b|310b)\d*", model):
        return "A2"
    return None


def detect_platform(devices, board=""):
    fields = board_fields(board)
    generation = model_generation(fields.get("chip name", ""), fields.get("npu name", ""))
    if generation:
        return generation
    generations = {model_generation(device["name"]) for device in devices}
    return generations.pop() if len(generations) == 1 else None


def system_product(raw):
    source = "dmidecode"
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("NPU_MONITOR_SYSTEM_SOURCE "):
            source = line.split()[-1]
            lines = []
        elif line and not line.startswith("#"):
            lines.append(line)
    model = re.sub(r"\s+", " ", lines[-1]) if lines else ""
    placeholders = {"not specified", "to be filled by o.e.m.", "system product name", "default string", "unknown", "none"}
    return (model if model.lower() not in placeholders and 0 < len(model) <= 200 else None), source


def server_generation(model):
    generations = set(re.findall(r"(?<![a-z0-9])A[235](?![a-z0-9])", model or "", re.I))
    return generations.pop().upper() if len(generations) == 1 else None
