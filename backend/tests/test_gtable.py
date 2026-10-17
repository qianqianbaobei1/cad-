# -*- coding: utf-8 -*-
"""测试 GTable 表格拓扑重构与天正解密模块 (基于 CAD快速看图 5.2.4 逆向内核)。"""

import math
from extractor.tangent import tangent_unencrypt_string, safe_decode_tangent_if_cipher
from extractor.gtable import (
    HVLine,
    extract_hv_lines,
    merge_lines,
    build_relation_grid,
    load_texts_into_cells,
    StructuredTable,
    parse_switchgear_table,
)


def test_tangent_unencrypt_string_basic():
    """验证天正 Base-26 字符逆向解密函数。"""
    # 短字符串原样返回
    assert tangent_unencrypt_string("A") == "A"
    assert tangent_unencrypt_string("") == ""

    # 测试 safe_decode_tangent_if_cipher 保护
    normal_text = "MCCB-250A/3P"
    assert safe_decode_tangent_if_cipher(normal_text) == normal_text


def test_gtable_hv_lines_extraction():
    """验证 extract_hv_lines 正交斜率容差提取。"""
    class MockLine:
        def __init__(self, start, end):
            self.dxf = type("dxf", (), {"start": type("pt", (), {"x": start[0], "y": start[1]}),
                                         "end": type("pt", (), {"x": end[0], "y": end[1]})})
        def dxftype(self):
            return "LINE"

    entities = [
        MockLine((0.0, 10.0), (100.0, 10.0)),   # 绝对水平线
        MockLine((10.0, 0.0), (10.0, 100.0)),   # 绝对垂直线
        MockLine((0.0, 0.0), (50.0, 50.0)),     # 45度斜线 (应过滤)
    ]
    h_lines, v_lines = extract_hv_lines(entities)
    assert len(h_lines) == 1
    assert len(v_lines) == 1
    assert math.isclose(h_lines[0].coord, 10.0)
    assert math.isclose(v_lines[0].coord, 10.0)


def test_gtable_merge_lines_snapping():
    """验证 merge_lines 断线焊接与自适应 minTextHeight 容差。"""
    # 构造两个同行、端点留有微小断缝 (1.5mm) 的水平线段
    min_text_height = 10.0  # 焊接容差为 10.0 * 0.2 = 2.0mm
    lines = [
        HVLine(start=0.0, end=50.0, coord=100.0, is_horizontal=True),
        HVLine(start=51.5, end=100.0, coord=100.0, is_horizontal=True),  # 相距 1.5mm < 2.0mm
    ]
    merged = merge_lines(lines, is_horizontal=True, min_text_height=min_text_height)
    assert len(merged) == 1
    assert math.isclose(merged[0].start, 0.0)
    assert math.isclose(merged[0].end, 100.0)


def test_gtable_build_relation_grid_and_load_texts():
    """验证 2x2 网格构建与文字空间几何中心点注入。"""
    h_lines = [
        HVLine(start=0.0, end=200.0, coord=100.0, is_horizontal=True),
        HVLine(start=0.0, end=200.0, coord=50.0, is_horizontal=True),
        HVLine(start=0.0, end=200.0, coord=0.0, is_horizontal=True),
    ]
    v_lines = [
        HVLine(start=0.0, end=100.0, coord=0.0, is_horizontal=False),
        HVLine(start=0.0, end=100.0, coord=100.0, is_horizontal=False),
        HVLine(start=0.0, end=100.0, coord=200.0, is_horizontal=False),
    ]
    grid = build_relation_grid(h_lines, v_lines, min_text_height=10.0)
    assert len(grid) == 2      # 2 行
    assert len(grid[0]) == 2   # 2 列

    # 投射文字
    texts = [
        {"text": "低压配电柜编号", "x": 10.0, "y": 70.0, "height": 10.0, "width": 50.0},  # 落入 (0, 0)
        {"text": "C01", "x": 120.0, "y": 70.0, "height": 10.0, "width": 20.0},           # 落入 (0, 1)
        {"text": "空气开关", "x": 10.0, "y": 20.0, "height": 10.0, "width": 40.0},        # 落入 (1, 0)
        {"text": "MCCB-250A/3P", "x": 110.0, "y": 20.0, "height": 10.0, "width": 60.0},   # 落入 (1, 1)
    ]
    load_texts_into_cells(grid, texts)

    assert grid[0][0].text_content == "低压配电柜编号"
    assert grid[0][1].text_content == "C01"
    assert grid[1][0].text_content == "空气开关"
    assert grid[1][1].text_content == "MCCB-250A/3P"

    table = StructuredTable(num_rows=2, num_cols=2, cells=grid, bbox=(0, 0, 200, 100))
    boxes, circuits, devices = parse_switchgear_table(table)
    assert len(boxes) == 1
    assert boxes[0].code == "C01"
    assert len(circuits) == 1
    assert circuits[0].box == "C01"
    assert "MCCB-250A/3P" in circuits[0].breaker
