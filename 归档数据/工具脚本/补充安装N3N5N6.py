#!/usr/bin/env python3
"""为安装T3物料补充N3技术参数 + N5规范映射 + N6校验规则。只对有国标代号的物料生成。"""
import csv, json, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

N3_PATH = ROOT / "标准知识库/源数据/03_国家规范库/N3_材料技术参数定义.csv"
N5_PATH = ROOT / "标准知识库/源数据/03_国家规范库/N5_材料规范映射.csv"
N6_PATH = ROOT / "标准知识库/源数据/03_国家规范库/N6_规范校验规则.csv"
T3_PATH = ROOT / "标准知识库/T3_安装_标准物料库.csv"

N3_FIELDS = ['material_id', 'param_code', 'param_name', 'param_type', 'required_level', 'procurement_visible', 'boq_extractable', 'default_value', 'unit', 'standard_id', 'clause_id', 'notes']
N5_FIELDS = ['material_id', 'standard_id', 'relation_type', 'relevance', 'use_scene', 'covered_params', 'clause_refs', 'active_flag', 'notes']
N6_FIELDS = ['rule_id', 'material_id', 'rule_type', 'input_params', 'condition', 'check_target', 'allowed_values', 'error_level', 'error_message', 'standard_id', 'clause_id', 'constraint_level']


def load_existing(path, fields):
    if not path.exists():
        return [], set()
    with open(path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    ids = set(r.get('material_id', '') for r in rows)
    return rows, ids


def main():
    # Load T3 install
    with open(T3_PATH, encoding="utf-8-sig") as f:
        t3_rows = list(csv.DictReader(f))

    # Load existing N3/N5/N6
    n3_rows, n3_ids = load_existing(N3_PATH, N3_FIELDS)
    n5_rows, n5_ids = load_existing(N5_PATH, N5_FIELDS)
    n6_rows, n6_ids = load_existing(N6_PATH, N6_FIELDS)

    new_n3 = []
    new_n5 = []
    new_n6 = []
    rule_counter = len(n6_rows) + 1

    for r in t3_rows:
        mid = r.get('物料ID', '').strip()
        name = r.get('标准名称', '').strip()
        std_code = r.get('标准代号', '').strip()
        if not mid or not std_code:
            continue

        spec_str = r.get('规格模式JSON', '').strip()
        if not spec_str:
            continue
        try:
            specs = json.loads(spec_str)
        except:
            continue

        # --- N3: 从规格模式JSON提取参数 ---
        if mid not in n3_ids:
            for s in specs:
                param = s.get('param', '')
                ptype = s.get('type', '')
                required = 'required' if s.get('required') else 'optional'
                new_n3.append({
                    'material_id': mid,
                    'param_code': param.lower().replace(' ', '_').replace('(', '').replace(')', '').replace('/', '_'),
                    'param_name': param,
                    'param_type': ptype,
                    'required_level': required,
                    'procurement_visible': '1' if s.get('required') else '0',
                    'boq_extractable': '1' if s.get('required') else '0',
                    'default_value': s.get('default', ''),
                    'unit': s.get('unit', ''),
                    'standard_id': s.get('source_standard', std_code),
                    'clause_id': s.get('source_clause', ''),
                    'notes': f'{name} 核心参数',
                })

        # --- N5: 材料→规范映射 ---
        if mid not in n5_ids:
            # Collect all params
            param_names = [s.get('param', '') for s in specs]
            new_n5.append({
                'material_id': mid,
                'standard_id': std_code,
                'relation_type': 'product_standard',
                'relevance': 'primary',
                'use_scene': '规格定义/质量验收/采购招标',
                'covered_params': json.dumps(param_names, ensure_ascii=False),
                'clause_refs': json.dumps([s.get('source_clause', '') for s in specs if s.get('source_clause')], ensure_ascii=False),
                'active_flag': '1',
                'notes': f'安装工程-{name}',
            })

        # --- N6: 对enum类型必填参数生成校验规则 ---
        for s in specs:
            if s.get('required') and s.get('type') == 'enum' and s.get('values'):
                rule_id = f"CHECK-{mid}-{rule_counter:03d}"
                # Check if similar rule already exists
                existing_targets = {r.get('check_target', '') for r in n6_rows if r.get('material_id') == mid}
                if s.get('param', '') not in existing_targets:
                    new_n6.append({
                        'rule_id': rule_id,
                        'material_id': mid,
                        'rule_type': 'enum_check',
                        'input_params': json.dumps([s.get('param', '').lower().replace(' ', '_')], ensure_ascii=False),
                        'condition': '',
                        'check_target': s.get('param', ''),
                        'allowed_values': json.dumps(s.get('values', []), ensure_ascii=False),
                        'error_level': 'block',
                        'error_message': f'{s.get("param","")}不在{std_code}允许范围内',
                        'standard_id': std_code,
                        'clause_id': s.get('source_clause', ''),
                        'constraint_level': 'mandatory',
                    })
                    rule_counter += 1

    print(f"新增 N3参数: {len(new_n3)} 条")
    print(f"新增 N5映射: {len(new_n5)} 条")
    print(f"新增 N6规则: {len(new_n6)} 条")

    if not any([new_n3, new_n5, new_n6]):
        print("所有条目已存在，无需补充。")
        return

    # Backup and append
    for path, rows, new_rows, fields in [
        (N3_PATH, n3_rows, new_n3, N3_FIELDS),
        (N5_PATH, n5_rows, new_n5, N5_FIELDS),
        (N6_PATH, n6_rows, new_n6, N6_FIELDS),
    ]:
        if not new_rows:
            continue
        backup = path.with_suffix(f".csv.bak_{time.strftime('%Y%m%d_%H%M%S')}")
        import shutil
        shutil.copy2(path, backup)

        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
            writer.writerows(new_rows)
        print(f"写入 {path.name}: {len(rows)} → {len(rows)+len(new_rows)}")

    print("\n✅ 安装N3/N5/N6补充完成")


if __name__ == "__main__":
    main()
