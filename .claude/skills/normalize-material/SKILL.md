---
name: normalize-material
description: "材料名称+规格一体化标准化：输入混合文本，输出分离后的标准 name+spec+校验标记"
trigger: /normalize-material
---

# /normalize-material

输入一个混合的材料描述串（可能名称和规格混在一起），自动分离并输出标准化的 `material_name` + `spec`，同时附上名称校验和规格校验的完整标记。

这是 `/check-material-name` 和 `/check-spec-format` 的组合技能。

## 使用方式

```
/normalize-material <混合文本>                     # 单条标准化
/normalize-material <材料名> <规格>                 # 已分离的 name+spec
/normalize-material <json文件>                      # 批量标准化
/normalize-material --pipeline <流水线结果json>      # 处理流水线 Stage4 输出
```

## 处理流程

```
输入: "冷轧08薄钢板0.5mm 3000mm长"
         │
    ┌────▼────┐
    │ Step 1  │ 名称/规格分离（R0+S0规则）
    │ 识别并分离规格参数
    └────┬────┘
         │
    ┌────▼────┐
    │ Step 2  │ 材料名校验（/check-material-name R1-R9）
    │ 合法性、长度、后缀、动作词、占位词
    └────┬────┘
         │
    ┌────▼────┐
    │ Step 3  │ 规格格式校验（/check-spec-format S1-S5）
    │ 六种格式识别、参数排序、键名标准化
    └────┬────┘
         │
    ┌────▼────┐
    │ Step 4  │ 综合判定
    │ confidence + needs_review + fix_suggestion
    └────┬────┘
         │
    输出: {
      material_name: "薄钢板",
      spec: "冷轧08 0.5mm 长度3000mm",
      confidence: "high",
      needs_review: false
    }
```

## 标准化流程详解

### Step 1: 名称/规格分离

从混合文本中识别并分离规格参数：

1. **检测尺寸值**：`\d+×\d+mm`、`DN\d+`、`Φ\d+`、`\d+mm` → 移至 spec
2. **检测强度等级**：`C\d+`、`HRB\d+`、`Q\d+B` → 移至 spec
3. **检测型号编码**：大写字母+数字组合（如 JDG25、YJV-4×25）→ 移至 spec
4. **检测中建 KV 串**：第一个逗号后的 `key:value` 序列 → 解析为 spec
5. **检测单位后缀**：`mm`、`m`、`MPa`、`kV` 结尾的数值 → 移至 spec
6. **残留验证**：去掉以上内容后，剩余部分是否为有效材料名

### Step 2: 材料名校验

执行 `/check-material-name` 的全部 R0-R9 规则。名称不可通过时标记 `needs_review=true`。

### Step 3: 规格格式校验

执行 `/check-spec-format` 的全部 S0-S5 规则。自动做：
- 六种格式归类
- 参数排序（牌号→尺寸→附加参数）
- 中建 KV → 标准键名映射
- 删除引用词/投标用语

### Step 4: 综合判定

| 条件 | confidence | needs_review | 可自动通过 Stage6 |
|------|-----------|-------------|-------------------|
| 名称 R0-R9 全通过 + 规格 S0-S5 全通过 | high | false | ✅ |
| 名称通过但规格有格式问题 | medium | false | ✅（规格可后修） |
| 名称有非阻塞问题（R2/R3/R6/R7） | medium | true | ❌ |
| 名称有阻塞问题（R0/R1/R4/R5） | low | true | ❌ |
| 来源为 ai_inferred | low | true | ❌ |

---

## 输出格式

```json
{
  "original": "原始输入文本",
  "material_name": "薄钢板",
  "spec": "冷轧08 0.5mm",
  "spec_params": {
    "牌号": "冷轧08",
    "厚度": "0.5mm"
  },
  "name_level": "L1",
  "name_source": "both_verified",
  "spec_format": "F2/F6",
  "confidence": "high",
  "needs_review": false,
  "review_reason": null,
  "fix_applied": true,
  "fix_detail": "移除了材料名中的牌号'冷轧08'和厚度'0.5mm'",
  "name_checks": {
    "r0_pass": true, "r1_pass": true, "r2_pass": true,
    "r3_pass": true, "r4_pass": true, "r5_pass": true,
    "r6_level": "L1", "r7_pass": true, "r8_pass": true
  },
  "spec_checks": {
    "s0_pass": true, "s1_type": "F2",
    "s2_pass": true, "s3_applicable": false,
    "s4_pass": true, "s5_pass": true
  }
}
```

---

## 批量标准化汇总

```
=== 材料标准化报告 ===
总数: N 条
自动修正: A 条
需人工处理: M 条

标准化前后对比:
  名称/规格分离修正: X 条
  规格参数排序修正: Y 条
  中建KV解析修正: Z 条
  引用词清除: W 条

无法自动处理的条目（需人工介入）:
  [列出具体条目和原因]

来源置信度分布: both_verified=X, tj_verified=X, zj_verified=X, ai_inferred=X
```

---

## 执行步骤

1. 解析输入：判断是混合文本、已分离 name+spec、JSON文件还是流水线结果
2. 对每条材料执行 Step 1→4
3. 输出标准化结果 JSON
4. 批量时额外输出汇总报告
5. 标记哪些条目可以自动通过、哪些需要人工复核