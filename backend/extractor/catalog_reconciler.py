# -*- coding: utf-8 -*-
"""图纸目录 vs 实际提取覆盖对账引擎 (Drawing Catalog Reconciliation).

根治"范围缺失"导致的漏柜（如 JX1~JX21 漏柜是因为配电箱系统图图幅根本未进流水线）。
通过识别图纸目录/图纸清单，将设计声明包含的配电箱与实际解析出的箱体进行原子级对账。
若发现整张图幅或箱体范围缺失，立即给出 ERROR 级范围告警，避免带病交付。
"""

from __future__ import annotations
import re
from typing import Any, Iterable, List, Optional, Set

from .schema import (
    Box,
    CatalogItem,
    CatalogReconciliation,
    Uncertainty,
    normalize_code,
)

# 箱号词法与箱体提取共用 extract_panel_code，目录不再维护一份例外名单。
from .cad_extractor import extract_panel_code, iter_panel_codes
from .config import domain as _domain

_CATALOG = _domain()["catalog"]
PANEL_PREFIX_PATTERN = _CATALOG["panel_prefix_pattern"]
_CAD_DOMAIN = _domain()["cad"]


_NOISE_PANEL_CODE = re.compile(
    r"^(DN\d+|(\d+)?MM2?|RG\d+|SCB?\d*|KVA\d*)$",
    re.IGNORECASE,
)


def _is_noise_panel_code(code: str) -> bool:
    """管径/线径/灯具危险等级等不是配电箱号。"""
    nc = normalize_code(code)
    return bool(nc) and bool(_NOISE_PANEL_CODE.match(nc))


def _is_noise_catalog_text(text: str) -> bool:
    """设计说明/材料注记不是图纸目录或平面范围声明。"""
    t = (text or "").strip()
    if not t:
        return True
    if re.search(
        r"管径|孔径|间距|长度|宽度|变压器|kVA|KVA|SCZB|"
        r"不应|应符合|选用|也可以|灯具|铝导体|铜导体|视看距离",
        t,
    ):
        return True
    # 编号说明长句：2.道路照明由… / （5）儿童及青少年…
    if re.match(r"^[(（]?\d+[)）．、.\s]", t) and len(t) > 28:
        return True
    if len(t) > 90 and not re.search(r"系统图|图纸目录|一览|配电箱|配电柜", t):
        return True
    return False


def _accept_panel_code(code: str) -> bool:
    """与箱体提取同一词法：过不了 extract_panel_code 的就不是目录里的箱号。"""
    if _is_noise_panel_code(code):
        return False
    got = extract_panel_code(code)
    return bool(got) and normalize_code(got) == normalize_code(code)


def _split_slash_group(code: str) -> list[str]:
    """AP1/AP2 是多台；AW1/2/3 这种斜杠后为数字的是一台并排箱。"""
    parts = [part for part in code.split("/") if part]
    if len(parts) > 1 and all(_accept_panel_code(part) for part in parts):
        return [normalize_code(part) for part in parts]
    return [normalize_code(code)]


def _is_schedule_line(text: str, declared: list[str]) -> bool:
    """单独一个代号是图面标注。范围、多台，或带箱体/系统图字样，才是目录声明。"""
    if len(declared) >= 2 or re.search(r"[~～至到]", text):
        return True
    cues = list(_CAD_DOMAIN.get("unit_caption_suffix") or []) + list(_CAD_DOMAIN.get("system_keywords_include") or [])
    return any(cue and cue in text for cue in cues)


def _title_markers() -> list[str]:
    markers: list[str] = []
    for key in ("system_title_markers", "plan_title_markers", "system_keywords_include", "system_keywords_exclude"):
        markers.extend(list(_CAD_DOMAIN.get(key) or []))
    # 稳定去重保序
    seen: set[str] = set()
    out: list[str] = []
    for m in markers:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def _is_bare_sheet_title(text: str, declared: list[str]) -> bool:
    """图幅标题如「00AL01道路照明系统图」不是「目录声明了箱 00AL01」。

    特征：全文只声明 1 个箱号，去掉该号后剩余几乎全是图名关键词（系统图/平面图等），
    且不含箱体后缀（配电箱/控制箱…）与范围符。
    """
    if len(declared) != 1:
        return False
    if re.search(r"[~～至到]", text):
        return False
    suffixes = list(_CAD_DOMAIN.get("unit_caption_suffix") or [])
    if any(s and s in text for s in suffixes):
        return False
    code = declared[0]
    # 允许代号紧贴图名：00AL01道路照明系统图 / 00AL01 道路照明系统图
    rest = re.sub(rf"^{re.escape(code)}\s*", "", text.strip(), count=1, flags=re.IGNORECASE)
    rest = rest.strip(" \t-_./（）()【】[]")
    if not rest:
        return True
    markers = _title_markers()
    hit = [m for m in markers if m and m in rest]
    if not hit:
        return False
    stripped = rest
    for m in sorted(hit, key=len, reverse=True):
        stripped = stripped.replace(m, "")
    stripped = re.sub(r"[\s\-_/（）()【】\[\]一二三四五六七八九十\d]+", "", stripped)
    # 剩余若无拉丁字母（不再含箱号词法），视为纯中文图名描述，如「道路照明」
    if not stripped:
        return True
    return not re.search(r"[A-Za-z]", stripped)


def _looks_like_plan_or_schedule(sheet_no: str, sheet_title: str, kind: str) -> bool:
    """平面图/表栏/范围声明：对账缺失不得升为 system_unit_missing ERROR。"""
    if kind == "range_annotation":
        return True
    blob = f"{sheet_no} {sheet_title}"
    plan_markers = list(_CAD_DOMAIN.get("plan_title_markers") or [])
    if any(m and m in blob for m in plan_markers):
        return True
    soft = ("一览", "表栏", "监控", "平面", "布置", "材料汇总", "设备表", "箱柜表")
    return any(m in blob for m in soft)


def classify_deficit_kind(kind: str, sheet_no: str = "", sheet_title: str = "") -> str:
    """缺失项分类：system_unit_missing | plan_or_schedule_declared。"""
    if _looks_like_plan_or_schedule(sheet_no, sheet_title, kind or ""):
        return "plan_or_schedule_declared"
    return "system_unit_missing"


def _panels_key(panels: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({normalize_code(c) for c in panels if c and str(c).strip()}))


def collect_catalog_items(texts: Iterable[str]) -> list[CatalogItem]:
    """从 CAD/OCR 文字收集目录项：parse + harvest，按声明箱号集合去重。"""
    items: list[CatalogItem] = []
    seen: set[tuple[str, ...]] = set()
    for raw in texts or []:
        text = (raw or "").strip()
        if not text:
            continue
        parsed = DrawingCatalogReconciler.parse_catalog_line(text)
        if parsed and parsed.declared_panels:
            key = _panels_key(parsed.declared_panels)
            if key and key not in seen:
                seen.add(key)
                items.append(parsed)
    for item in harvest_range_annotations(texts):
        key = _panels_key(item.declared_panels)
        if key and key not in seen:
            seen.add(key)
            items.append(item)
    return items


def expand_panel_range(text: str) -> list[str]:
    """将图纸目录中的配电箱范围表达式展开为离散编号列表。

    示例：
    - "1AL1~1AL5" -> ["1AL1", "1AL2", "1AL3", "1AL4", "1AL5"]
    - "1AL1~5"    -> ["1AL1", "1AL2", "1AL3", "1AL4", "1AL5"]
    - "JX1~JX21"  -> ["JX1", "JX2", ..., "JX21"]
    - "JX1~21"    -> ["JX1", "JX2", ..., "JX21"]
    - "2AP1-4"    -> ["2AP1", "2AP2", "2AP3", "2AP4"]
    - "1AL1, 1AL2, 2AP1" -> ["1AL1", "1AL2", "2AP1"]
    """
    if not text:
        return []

    cleaned = text.strip()
    results: list[str] = []
    seen: set[str] = set()

    def add_code(c: str):
        nc = normalize_code(c)
        if nc and nc not in seen and _accept_panel_code(nc):
            seen.add(nc)
            results.append(nc)

    # 1. 匹配带波浪号或短横线的区间写法：如 1AL1~1AL5, JX1~21, 1AP1~5, 1AL1-1AL5
    range_regex = re.compile(
        r"([A-Za-z0-9_\-]+?)"                    # 前缀+前编号前段
        r"(\d+)"                                # 前编号尾部连续数字
        r"\s*(~|至|到|-|–|—)\s*"                # 范围连接符
        r"([A-Za-z0-9_\-]+?)?"                  # 后编号可选前缀
        r"(\d+)",                               # 后编号尾部数字
        re.IGNORECASE
    )

    remaining_text = cleaned
    # 楼层在前、字母后缀在后：3~10RDAL → 3RDAL…10RDAL。纯数字区间仍然不是箱号。
    suffix_range = re.compile(
        r"(?<![A-Za-z0-9])(\d+)\s*[~～至到]\s*(\d+)([A-Za-z][A-Za-z0-9]*)"
    )
    for match in suffix_range.finditer(cleaned):
        start_n = int(match.group(1))
        end_n = int(match.group(2))
        suffix = match.group(3)
        if 0 < end_n - start_n <= 150 and start_n > 0:
            # 仅当起始数字本身带前导零时保留位宽（DQHZ01）；3~10RDAL 不补零
            raw_start = match.group(1)
            width = len(raw_start) if raw_start != str(start_n) else 0
            for i in range(start_n, end_n + 1):
                num = str(i).zfill(width) if width else str(i)
                add_code(f"{num}{suffix}")
            remaining_text = remaining_text.replace(match.group(0), " ")

    for match in range_regex.finditer(cleaned):
        prefix1, num1_str, sep, prefix2, num2_str = match.groups()
        prefix2 = prefix2 or ""

        norm_p1 = prefix1.upper()
        norm_p2 = prefix2.upper()

        if not norm_p2 or norm_p1 == norm_p2:
            effective_prefix = prefix1
            # 纯数字区间（页码、尺寸、电流）不是箱号范围，展开会造出 12、13 这类假漏柜。
            if not re.search(r"[A-Za-z]", effective_prefix):
                continue
            try:
                start_n = int(num1_str)
                end_n = int(num2_str)
                # 范围需 end_n > start_n，且跨度合理 (1 ~ 150)
                if 0 < end_n - start_n <= 150 and start_n > 0:
                    width = len(num1_str) if num1_str != str(start_n) else 0
                    for i in range(start_n, end_n + 1):
                        num = str(i).zfill(width) if width else str(i)
                        add_code(f"{effective_prefix}{num}")
                    remaining_text = remaining_text.replace(match.group(0), " ")
            except ValueError:
                pass

    # 2. 离散箱号与并排箱，走和提取器相同的词法，不再按一份前缀表另判。
    for code in iter_panel_codes(remaining_text):
        for piece in _split_slash_group(code):
            add_code(piece)

    return results




def harvest_range_annotations(texts: Iterable[str]) -> list[CatalogItem]:
    """从平面图/表栏文字中收获范围声明（DQHZ01~DQHZ05 等），不假装切块。

    只收「含范围符且能展开出 ≥2 个箱号」的行；结果 declaration_kind=range_annotation，
    对账缺失时文案为「目录声明、图面未切出单元」。
    """
    items: list[CatalogItem] = []
    seen_key: set[str] = set()
    for raw in texts or []:
        text = (raw or "").strip()
        if not text or len(text) > 120:
            continue
        if not re.search(r"[~～至到]", text):
            continue
        if _is_noise_catalog_text(text):
            continue
        # 说明长句里的范围不是目录
        if any(m in text for m in ("详见", "应符合", "不得", "采用", "敷设")):
            continue
        declared = expand_panel_range(text)
        if len(declared) < 2:
            continue
        # 管径/尺寸噪声：DN65~DN150 等不是配电箱
        if all(re.fullmatch(r"DN\d+", c, re.I) for c in declared):
            continue
        if re.search(r"管径|孔径|间距|长度|宽度|变压器|kVA|KVA|SCZB", text):
            continue
        key = ",".join(declared)
        if key in seen_key:
            continue
        seen_key.add(key)
        items.append(CatalogItem(
            sheet_no="",
            sheet_title=text[:80],
            declared_panels=declared,
            matched_panels=[],
            missing_panels=list(declared),
            status="MISSING",
            declaration_kind="range_annotation",
            note="平面/表栏范围声明，展开后参与对账",
        ))
    return items

class DrawingCatalogReconciler:
    """图纸目录与实际提取覆盖核对器。"""

    def __init__(self, catalog_items: list[CatalogItem] | None = None):
        self.catalog_items = list(catalog_items or [])
        # 绑定实例上的 reconcile 方法，支持 reconciler.reconcile(boxes, source_name)
        self.reconcile = lambda extracted_boxes, source_name="CAD图纸目录/PDF清单": DrawingCatalogReconciler.reconcile(
            self.catalog_items, extracted_boxes, source_name
        )

    def __iter__(self):
        return iter(self.catalog_items)

    def __len__(self):
        return len(self.catalog_items)

    def __getitem__(self, idx):
        return self.catalog_items[idx]

    @classmethod
    def parse_catalog_line(cls, line: str) -> Optional[CatalogItem]:
        """从图纸目录单行文本中解析图号、图名与所辖配电箱。"""
        if not line or not line.strip():
            return None
        text = line.strip()
        if _is_noise_catalog_text(text):
            return None

        # 过滤纯表头
        if any(h in text for h in ("图纸目录", "序号", "图名", "图幅", "版本", "设计阶段", "比例")):
            if not re.search(r"\d", text):
                return None

        # 尝试匹配图号与图名：
        # 常见形态："01B-03 动力配电箱系统图(三) JX1~JX21" 或 "D-02 低压配电系统图(一)"
        sheet_no = ""
        sheet_title = text

        m_no = re.search(r"([A-Za-z0-9_\-]{2,12})\s+(.+)", text)
        if m_no:
            sheet_no = m_no.group(1).strip()
            sheet_title = m_no.group(2).strip()

        # 提取所声明的配电箱范围。孤立代号不是目录漏项。
        declared = expand_panel_range(sheet_title if sheet_no else text)
        if not declared:
            declared = expand_panel_range(text)
        if (not declared and sheet_no and _accept_panel_code(sheet_no)
                and _is_schedule_line(text, [normalize_code(sheet_no)])):
            declared = [normalize_code(sheet_no)]
        if declared and not _is_schedule_line(text, declared):
            declared = []
        if not declared:
            return None
        # 图幅标题「00AL01道路照明系统图」不是箱号目录声明
        if _is_bare_sheet_title(text, declared):
            return None

        # 带图号/系统图字样 → 图纸目录；纯范围表达式 → 平面/表栏范围声明
        if sheet_no or any(m in sheet_title for m in ("系统图", "图纸", "目录", "一览")):
            kind = "sheet_catalog"
        elif re.search(r"[~～至到]", text) and len(declared) >= 2:
            kind = "range_annotation"
        else:
            kind = "caption"
        # 平面/表栏图名即使带了图号，也按范围声明对待（避免升 ERROR）
        if kind == "sheet_catalog" and _looks_like_plan_or_schedule(sheet_no, sheet_title, kind):
            # 仍保留 sheet_catalog 语义，但 note 标明平面；deficit 在 reconcile 时分类
            note = "平面/表栏目录行，缺失不视为系统图漏切"
        elif kind == "range_annotation":
            note = "平面/表栏范围声明，展开后参与对账"
        else:
            note = ""
        return CatalogItem(
            sheet_no=sheet_no,
            sheet_title=sheet_title,
            declared_panels=declared,
            matched_panels=[],
            missing_panels=list(declared),
            status="COVERED" if not declared else "MISSING",
            declaration_kind=kind,
            note=note,
        )

    @classmethod
    def from_records(cls, records: list[dict[str, Any]]) -> "DrawingCatalogReconciler":
        """从字典列表快捷构建 DrawingCatalogReconciler 实例。支持 sheet_no, sheet_title, declared_panels 等键。"""
        items: list[CatalogItem] = []
        for r in records:
            sheet_no = str(r.get("sheet_no", "")).strip()
            sheet_title = str(r.get("sheet_title", "")).strip()
            declared = r.get("declared_panels")
            if declared is None:
                declared = expand_panel_range(sheet_title)
            kind = str(r.get("declaration_kind") or "").strip()
            note = str(r.get("note") or "").strip()
            if not kind:
                if re.search(r"[~～至到]", sheet_title) and len(list(declared)) >= 2:
                    kind = "range_annotation"
                    note = note or "平面/表栏范围声明，展开后参与对账"
                else:
                    kind = "sheet_catalog"
            items.append(CatalogItem(
                sheet_no=sheet_no,
                sheet_title=sheet_title,
                declared_panels=list(declared),
                missing_panels=list(declared),
                status="COVERED" if not declared else "MISSING",
                declaration_kind=kind,
                note=note,
            ))
        return cls(items)

    @classmethod
    def reconcile(
        cls,
        catalog_items: list[CatalogItem] | DrawingCatalogReconciler,
        extracted_boxes: list[Box] | list[str],
        source_name: str = "CAD图纸目录/PDF清单",
    ) -> CatalogReconciliation:
        """执行图纸目录与提取结果的对账。支持 extracted_boxes 传入 Box 或 str 列表。"""
        if isinstance(catalog_items, DrawingCatalogReconciler):
            catalog_items = catalog_items.catalog_items

        extracted_codes: set[str] = set()
        for b in extracted_boxes:
            if isinstance(b, Box):
                if b.code and b.code.strip():
                    extracted_codes.add(normalize_code(b.code))
            elif isinstance(b, str) and b.strip():
                extracted_codes.add(normalize_code(b))

        total_declared_set: set[str] = set()
        covered_set: set[str] = set()
        missing_set: set[str] = set()

        processed_items: list[CatalogItem] = []

        for item in catalog_items:
            declared = item.declared_panels
            if not declared:
                processed_items.append(item)
                continue

            item_matched = []
            item_missing = []
            for code in declared:
                norm_c = normalize_code(code)
                total_declared_set.add(norm_c)
                if norm_c in extracted_codes:
                    item_matched.append(code)
                    covered_set.add(norm_c)
                else:
                    item_missing.append(code)
                    missing_set.add(norm_c)

            # 更新 item 状态
            if not item_missing:
                item_status = "COVERED"
            elif not item_matched:
                item_status = "MISSING"
            else:
                item_status = "PARTIAL"

            note = item.note or ""
            kind = getattr(item, "declaration_kind", "") or ""
            deficit = ""
            if item_missing:
                deficit = classify_deficit_kind(kind, item.sheet_no, item.sheet_title)
                if deficit == "plan_or_schedule_declared":
                    note = "目录声明、图面未切出单元"
                elif not note:
                    note = "目录声明箱号在本次提取中未命中"
            updated_item = CatalogItem(
                sheet_no=item.sheet_no,
                sheet_title=item.sheet_title,
                declared_panels=declared,
                matched_panels=item_matched,
                missing_panels=item_missing,
                status=item_status,
                declaration_kind=kind,
                deficit_kind=deficit,
                note=note,
            )
            processed_items.append(updated_item)

        total_count = len(total_declared_set)
        covered_count = len(covered_set)
        missing_count = len(missing_set)
        coverage_rate = (covered_count / total_count) if total_count > 0 else 1.0

        system_missing: set[str] = set()
        plan_missing: set[str] = set()
        for it in processed_items:
            for code in it.missing_panels or []:
                nc = normalize_code(code)
                if not nc:
                    continue
                if (it.deficit_kind or "") == "plan_or_schedule_declared":
                    plan_missing.add(nc)
                elif (it.deficit_kind or "") == "system_unit_missing":
                    system_missing.add(nc)
        # 同一箱号若同时被系统图与平面声明：优先算系统图缺失
        plan_missing -= system_missing

        return CatalogReconciliation(
            has_catalog=bool(catalog_items),
            catalog_source=source_name,
            total_declared_panels=total_count,
            covered_count=covered_count,
            missing_count=missing_count,
            coverage_rate=round(coverage_rate, 4),
            items=processed_items,
            missing_box_codes=sorted(list(missing_set)),
            system_unit_missing_count=len(system_missing),
            plan_or_schedule_declared_count=len(plan_missing),
        )

    @classmethod
    def generate_reconciliation_issues(
        cls,
        reconciliation: CatalogReconciliation,
    ) -> list[Uncertainty]:
        """根据对账结果生成待核对项。

        - system_unit_missing → ERROR（整幅）/ WARNING（部分）
        - plan_or_schedule_declared → WARNING（整幅）/ INFO（部分），绝不升 ERROR
        """
        issues: list[Uncertainty] = []
        if not reconciliation.has_catalog or reconciliation.missing_count == 0:
            return issues

        for item in reconciliation.items:
            if not item.missing_panels or item.status not in ("MISSING", "PARTIAL"):
                continue
            sample = ", ".join(item.missing_panels[:5])
            count = len(item.missing_panels)
            suffix = f" 等共 {count} 台" if count > 5 else f" (共 {count} 台)"
            deficit = getattr(item, "deficit_kind", "") or classify_deficit_kind(
                getattr(item, "declaration_kind", "") or "",
                item.sheet_no,
                item.sheet_title,
            )
            if deficit == "plan_or_schedule_declared":
                if item.status == "MISSING":
                    detail = (
                        f"【plan_or_schedule_declared】【目录声明、图面未切出单元】"
                        f"平面/表栏范围声明《{item.sheet_title}》展开为配电箱 [{sample}{suffix}]，"
                        f"但未切出对应单元块（多为平面标注/监控表栏，并非独立系统图）。"
                        f"请人工确认是否另有系统图，勿当作已扒量。"
                    )
                    sev = "WARNING"
                else:
                    detail = (
                        f"【plan_or_schedule_declared】【目录声明、图面未切出单元】"
                        f"范围声明《{item.sheet_title}》中 [{sample}{suffix}] 未切出单元块，"
                        f"请人工核对（勿当作已扒量）"
                    )
                    sev = "INFO"
            else:
                # system_unit_missing
                if item.status == "MISSING":
                    detail = (
                        f"【system_unit_missing】【范围缺失】图纸目录声明图幅 "
                        f"{item.sheet_no or '(未标图号)'} "
                        f"《{item.sheet_title}》包含配电箱 [{sample}{suffix}]，"
                        f"但在本次提取中全未找到！疑似系统图图幅漏传或未纳入切片范围，请核对补传！"
                    )
                    sev = "ERROR"
                else:
                    detail = (
                        f"【system_unit_missing】【部分缺失】图纸目录声明图幅 "
                        f"{item.sheet_no or ''} "
                        f"《{item.sheet_title}》中部分配电箱 [{sample}{suffix}] 未提取到，请核对图纸局部"
                    )
                    sev = "WARNING"
            issues.append(
                Uncertainty(
                    location="图纸目录对账",
                    detail=detail,
                    severity=sev,
                    source="program",
                )
            )

        return issues
