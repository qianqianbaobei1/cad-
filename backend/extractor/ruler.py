# -*- coding: utf-8 -*-
"""用已冻结的金标准给一次提取打分。

识别看箱号集合，切分看回路是否落在该箱，提取看对上的那一行字段是否与图面一致。
三项都是 1 才算这张图通过。程序自己的上一次输出不能当金标准。
"""
from __future__ import annotations

import re
from typing import Any


def norm(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.upper().replace("×", "X").replace("＊", "*")
    text = text.replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text)


def _same_power(left: Any, right: Any) -> bool:
    a, b = norm(left), norm(right)
    if a in {"", "--", "-"} and b in {"", "--", "-"}:
        return True
    try:
        return abs(float(a) - float(b)) < 0.01
    except ValueError:
        return a == b


def _boxes(payload: dict) -> list[dict]:
    if payload.get("box"):
        return [payload["box"]]
    return list(payload.get("boxes") or [])


def _circuits(payload: dict) -> list[dict]:
    rows = []
    for item in payload.get("circuits") or []:
        rows.append({
            "box": item.get("box") or item.get("code") or "",
            "phase": item.get("phase") or "",
            "breaker": item.get("breaker") or "",
            "circuit_no": item.get("circuit_no") or "",
            "cable": item.get("cable") or "",
            "load_name": item.get("load_name") or "",
            "power_kw": item.get("power_kw") or "",
        })
        if str(item.get("note") or "").strip():
            rows[-1]["note"] = item.get("note")
    return rows


def _box_code(box: dict) -> str:
    return norm(box.get("code"))


def score_extraction(gold: dict, result: dict) -> dict:
    """返回三项召回/精确率和未通过的格子。passed 仅在三项都全对时为真。"""
    gold_boxes = _boxes(gold)
    pred_boxes = _boxes(result)
    gold_codes = [_box_code(box) for box in gold_boxes]
    pred_codes = [_box_code(box) for box in pred_boxes]
    gold_set = set(gold_codes)
    pred_set = set(pred_codes)
    hit_boxes = gold_set & pred_set
    recognize = _ratio(len(hit_boxes), len(gold_set), len(pred_set))

    gold_rows = _circuits(gold)
    if gold_rows and not any(row["box"] for row in gold_rows):
        only = gold_codes[0] if len(gold_codes) == 1 else ""
        for row in gold_rows:
            row["box"] = only
    pred_rows = _circuits(result)
    used: set[int] = set()
    split_hit = 0
    content_hit = 0
    content_fields = 0
    misses: list[str] = []

    for row in gold_rows:
        content_fields += 1
        match = _find_row(row, pred_rows, used)
        if match is None:
            misses.append(f"缺少 {norm(row['box'])} {row['circuit_no'] or row['load_name'] or '未编号'}")
            continue
        index, pred = match
        used.add(index)
        if norm(pred["box"]) == norm(row["box"]):
            split_hit += 1
        else:
            misses.append(
                f"切分错误 {row['circuit_no'] or row['load_name']} 应在 {norm(row['box'])}，"
                f"结果在 {norm(pred['box'])}"
            )
            continue
        field_miss = _field_misses(row, pred)
        if field_miss:
            misses.append(
                f"提取不一致 {norm(row['box'])} {row['circuit_no'] or row['load_name']}: "
                + "；".join(field_miss)
            )
        else:
            content_hit += 1

    extras = []
    for index, pred in enumerate(pred_rows):
        if index not in used:
            extras.append(f"多出 {norm(pred['box'])} {pred['circuit_no'] or pred['load_name'] or '未编号'}")
    split = _ratio(split_hit, len(gold_rows), split_hit + len(extras))
    content = _ratio(content_hit, len(gold_rows), content_hit + len(extras))
    box_miss = _box_field_misses(gold_boxes, pred_boxes)
    if box_miss:
        misses.extend(box_miss)
        content = {
            "recall": 0.0 if content["recall"] == 1.0 else content["recall"],
            "precision": content["precision"],
        }
        if content_hit == len(gold_rows) and not extras:
            content = {"recall": 0.0, "precision": content["precision"]}
    for device in gold.get("extra_devices") or []:
        if not _device_present(device, result.get("components") or []):
            misses.append(f"缺少器件 {device.get('name') or ''} {device.get('spec') or ''}".strip())
    passed = (
        recognize["recall"] == 1.0
        and recognize["precision"] == 1.0
        and split["recall"] == 1.0
        and split["precision"] == 1.0
        and content["recall"] == 1.0
        and content["precision"] == 1.0
        and not box_miss
        and not misses
    )
    return {
        "recognize": recognize,
        "split": split,
        "extract": content,
        "passed": passed,
        "misses": misses + extras,
    }


def _device_present(device: dict, components: list) -> bool:
    name = norm(device.get("name"))
    spec = norm(device.get("spec"))
    for component in components:
        blob = norm(component.get("name")) + norm(component.get("spec"))
        if name and name not in blob:
            continue
        if spec and spec not in blob:
            continue
        return True
    return False


def _ratio(hit: int, gold_n: int, pred_n: int) -> dict[str, float]:
    recall = 1.0 if gold_n == 0 else hit / gold_n
    precision = 1.0 if pred_n == 0 else hit / pred_n
    return {"recall": round(recall, 4), "precision": round(precision, 4)}


def _find_row(gold_row: dict, pred_rows: list[dict], used: set[int]) -> tuple[int, dict] | None:
    number = norm(gold_row["circuit_no"])
    box = norm(gold_row["box"])
    if number:
        for index, pred in enumerate(pred_rows):
            if index in used:
                continue
            if norm(pred["circuit_no"]) == number and norm(pred["box"]) == box:
                return index, pred
        for index, pred in enumerate(pred_rows):
            if index in used:
                continue
            if norm(pred["circuit_no"]) == number:
                return index, pred
        return None
    same_box = [
        (index, pred)
        for index, pred in enumerate(pred_rows)
        if index not in used and norm(pred["box"]) == box and not norm(pred["circuit_no"])
    ]
    return same_box[0] if same_box else None


def _field_misses(gold_row: dict, pred: dict) -> list[str]:
    misses = []
    for key in ("phase", "breaker", "circuit_no", "cable", "load_name"):
        if norm(gold_row[key]) != norm(pred[key]):
            misses.append(f"{key} 图面 {gold_row[key] or '空'} / 结果 {pred[key] or '空'}")
    if str(gold_row.get("note") or "").strip() and norm(gold_row.get("note")) != norm(pred.get("note")):
        misses.append(f"note 图面 {gold_row.get('note') or '空'} / 结果 {pred.get('note') or '空'}")
    if not _same_power(gold_row["power_kw"], pred["power_kw"]):
        misses.append(f"power_kw 图面 {gold_row['power_kw'] or '空'} / 结果 {pred['power_kw'] or '空'}")
    return misses


def _box_field_misses(gold_boxes: list[dict], pred_boxes: list[dict]) -> list[str]:
    pred_by_code = {_box_code(box): box for box in pred_boxes}
    misses = []
    for gold_box in gold_boxes:
        pred = pred_by_code.get(_box_code(gold_box))
        if pred is None:
            misses.append(f"未识别箱号 {_box_code(gold_box)}")
            continue
        for key in ("name", "ip_rating", "install", "location", "size"):
            if norm(gold_box.get(key)) != norm(pred.get(key)):
                misses.append(
                    f"箱体 {key} 图面 {gold_box.get(key) or '空'} / 结果 {pred.get(key) or '空'}"
                )
        if int(gold_box.get("quantity") or 0) != int(pred.get("quantity") or 0):
            misses.append(
                f"箱体 quantity 图面 {gold_box.get('quantity')} / 结果 {pred.get('quantity')}"
            )
    return misses
