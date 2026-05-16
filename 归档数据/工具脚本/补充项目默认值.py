#!/usr/bin/env python3
"""Enrich 项目默认值库 with executable default specs and standard refs."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
KB_PATH = ROOT / "项目数据" / "项目默认值库.json"


def compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def first_range_mid(text: str) -> float | None:
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(text or ""))]
    if not nums:
        return None
    if len(nums) >= 2 and re.search(r"\d+(?:\.\d+)?\s*[-~～至]\s*\d+(?:\.\d+)?", str(text)):
        return round((nums[0] + nums[1]) / 2, 3)
    return nums[0]


def extract_default_specs(rec: dict) -> dict:
    spec_text = str(rec.get("typical_spec") or "").strip()
    text = f"{spec_text} {rec.get('material_name', '')}"
    defaults: dict = {}
    existing = rec.get("default_spec_values")
    if isinstance(existing, dict):
        defaults.update({k: v for k, v in existing.items() if not str(k).startswith("_")})

    if spec_text:
        defaults.setdefault("spec_text", spec_text)

    spec_params = rec.get("spec_params") or {}
    if isinstance(spec_params, dict):
        for key, value in spec_params.items():
            if value in (None, ""):
                continue
            key_norm = str(key)
            if key_norm not in defaults:
                defaults[key_norm] = value
            if any(x in key_norm for x in ["thickness", "厚度"]):
                parsed = first_range_mid(str(value))
                if parsed is not None:
                    defaults.setdefault("thickness_mm", parsed)
            if any(x in key_norm for x in ["density", "容重", "密度"]):
                parsed = first_range_mid(str(value))
                if parsed is not None:
                    defaults.setdefault("density_kg_m3", parsed)

    fire = re.search(r"(?:防火|燃烧)?\s*([AB]\s*\d?级)", text, re.I)
    if fire:
        defaults.setdefault("fire_rating", re.sub(r"\s+", "", fire.group(1)).upper())

    concrete = re.search(r"\b(C\d{2,3})\b", text, re.I)
    if concrete:
        defaults.setdefault("concrete_grade", concrete.group(1).upper())

    steel = re.search(r"\b(HRB\d{3}E?|HPB\d{3}|Q\d{3}[A-Z]?)\b", text, re.I)
    if steel:
        defaults.setdefault("grade", steel.group(1).upper())

    thickness_match = (
        re.search(r"(\d+(?:\.\d+)?)\s*[-~～至]\s*(\d+(?:\.\d+)?)\s*mm\s*厚", text, re.I)
        or re.search(r"(\d+(?:\.\d+)?)\s*mm\s*厚", text, re.I)
        or re.search(r"厚(?:度)?\s*(\d+(?:\.\d+)?)\s*mm", text, re.I)
    )
    if thickness_match:
        nums = [float(x) for x in thickness_match.groups() if x]
        if nums:
            defaults.setdefault("thickness_mm", round(sum(nums) / len(nums), 3))

    density = re.search(r"(?:容重|密度)\s*[≥>=]?\s*(\d+(?:\.\d+)?)(?:\s*[-~～至]\s*(\d+(?:\.\d+)?))?\s*kg/?m", text, re.I)
    if density:
        nums = [float(x) for x in density.groups() if x]
        defaults.setdefault("density_kg_m3", round(sum(nums) / len(nums), 3))

    dim = re.search(r"\d+(?:\.\d+)?\s*[×xX*]\s*\d+(?:\.\d+)?(?:\s*[×xX*]\s*\d+(?:\.\d+)?)?\s*mm?", text)
    if dim:
        defaults.setdefault("dimensions", re.sub(r"\s+", "", dim.group(0)))

    diameter = re.search(r"(?:Φ|φ|DN)\s*\d+(?:\.\d+)?(?:\s*[-~～至]\s*(?:Φ|φ|DN)?\s*\d+(?:\.\d+)?)?", text, re.I)
    if diameter:
        defaults.setdefault("diameter", re.sub(r"\s+", "", diameter.group(0)))

    weight = re.search(r"(\d+(?:\.\d+)?)\s*g/m²", text, re.I)
    if weight:
        defaults.setdefault("weight_g_m2", float(weight.group(1)))

    thickness_context = (
        "厚" in spec_text
        or any("thickness" in str(k) or "厚" in str(k) for k in spec_params.keys())
        or "default_thickness_mm" in (rec.get("quantity_rule") or {})
    )
    if not thickness_context:
        defaults.pop("thickness_mm", None)

    return defaults


def standards_from_ref(ref: dict) -> list[str]:
    return [
        s.get("standard_id")
        for s in ref.get("standards", []) or []
        if isinstance(s, dict) and s.get("standard_id")
    ]


def ref_match_score(name: str, ref_name: str) -> int:
    n, r = compact(name), compact(ref_name)
    if not n or not r:
        return 0
    if n == r:
        return 100
    if n in r or r in n:
        return min(len(n), len(r))
    bridges = [
        (["xps", "挤塑"], ["xps", "挤塑", "聚苯"]),
        (["岩棉"], ["岩棉"]),
        (["玻璃", "门", "窗"], ["玻璃", "门", "窗", "幕墙"]),
        (["幕墙"], ["幕墙"]),
        (["铝单板", "铝板"], ["铝单板", "铝板"]),
        (["砂浆"], ["砂浆"]),
        (["涂料"], ["涂料"]),
        (["防水"], ["防水"]),
        (["地砖", "瓷砖", "面砖", "玻化砖"], ["陶瓷砖", "瓷砖", "砖"]),
        (["密封胶", "密封膏"], ["密封胶", "密封"]),
        (["焊接", "焊材"], ["焊接", "焊接材料"]),
        (["螺栓"], ["螺栓"]),
    ]
    for left, right in bridges:
        if any(x in n for x in left) and any(x in r for x in right):
            return 20
    return 0


def match_standard_refs(rec: dict, refs: list[dict]) -> list[dict]:
    scored = []
    for ref in refs:
        score = ref_match_score(rec.get("material_name", ""), ref.get("material_name", ""))
        if score:
            scored.append((score, ref))
    return [ref for _score, ref in sorted(scored, key=lambda x: -x[0])[:3]]


def heuristic_basis(rec: dict) -> list[str]:
    name = compact(rec.get("material_name", ""))
    system = compact(rec.get("system_id", ""))
    phase = compact(rec.get("phase", ""))
    basis: list[str] = []
    if any(k in name for k in ["门", "窗", "玻璃", "隔断", "栏杆", "吊顶", "墙面", "地板", "地砖", "瓷砖", "卫浴", "橱柜"]):
        basis.append("GB 50210-2018")
    if any(k in name for k in ["玻璃", "幕墙"]):
        basis.append("JGJ 113-2015")
    if "幕墙" in name:
        basis.extend(["GB/T 21086-2007", "JGJ 102-2003"])
    if any(k in name for k in ["铝合金", "铝镁锰", "铝单板", "铝板"]):
        basis.append("GB/T 3880-2012")
    if any(k in name for k in ["彩钢", "压型", "夹芯板", "泛水", "金属屋面", "天沟", "风帽"]):
        basis.append("GB 50207-2012")
    if any(k in name for k in ["防水", "密封", "堵漏", "排汽管"]):
        basis.append("GB 50208-2011")
    if any(k in name for k in ["隧道", "锚杆", "钢拱架"]):
        basis.append("JTG/T 3660-2020")
    if any(k in name for k in ["桥", "泄水管", "护栏", "伸缩缝", "路面", "沥青"]):
        basis.append("JTG/T 3650-2020")
    if any(k in name for k in ["医疗", "医用", "洁净", "抗菌", "手术室", "设备带"]):
        basis.append("GB 50333-2013")
    if any(k in system for k in ["roof", "屋面"]):
        basis.append("GB 50207-2012")
    if not basis:
        basis.append("GB 50210-2018")
    return list(dict.fromkeys(basis))


def main() -> int:
    kb = json.loads(KB_PATH.read_text(encoding="utf-8"))
    refs = kb.get("standard_refs", []) or []
    changed = {"default_spec_values": 0, "basis": 0, "standard_ref_ids": 0, "required_params": 0}

    for rec in kb.get("material_recommendations", []) or []:
        defaults = extract_default_specs(rec)
        if defaults and defaults != rec.get("default_spec_values"):
            rec["default_spec_values"] = defaults
            changed["default_spec_values"] += 1

        matched_refs = match_standard_refs(rec, refs)
        ref_ids = [r.get("ref_id") for r in matched_refs if r.get("ref_id")]
        if ref_ids and rec.get("standard_ref_ids") != ref_ids:
            rec["standard_ref_ids"] = ref_ids
            changed["standard_ref_ids"] += 1

        basis = list(rec.get("basis") or [])
        for ref in matched_refs:
            basis.extend(standards_from_ref(ref))
        if not basis:
            basis.extend(heuristic_basis(rec))
        basis = list(dict.fromkeys(x for x in basis if x))
        if basis != rec.get("basis"):
            rec["basis"] = basis
            changed["basis"] += 1

        if rec.get("required_params") is None:
            rec["required_params"] = []
            changed["required_params"] += 1

    meta = kb.setdefault("meta", {})
    changelog = meta.setdefault("changelog", [])
    note = "v2.2.0: 补齐 default_spec_values/spec_text、standard_ref_ids 与 basis，规范 required_params 空值。"
    if note not in changelog:
        changelog.append(note)
    meta["version"] = "2.2.0"

    KB_PATH.write_text(json.dumps(kb, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("enriched 项目默认值库")
    for key, value in changed.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
