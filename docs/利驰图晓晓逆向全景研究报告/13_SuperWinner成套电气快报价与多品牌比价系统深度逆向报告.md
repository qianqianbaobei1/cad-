# SuperWinner 成套电气快报价与多品牌比价系统深度逆向与独立实现报告

**对应目录**：`E:\利驰！\3\SuperWinner` 与 `E:\利驰！\3\1.2多品牌比价：演示实例`  
**交付文档路径**：[`E:/AI/codex任务/tuxiaoxiao-full-drawing-research/13_SuperWinner成套电气快报价与多品牌比价系统深度逆向报告.md`](file:///E:/AI/codex%E4%BB%BB%E5%8A%A1/tuxiaoxiao-full-drawing-research/13_SuperWinner%E6%88%90%E5%A5%97%E7%94%B5%E6%B0%94%E5%BF%AB%E6%8A%A5%E4%BB%B7%E4%B8%8E%E5%A4%9A%E5%93%81%E7%89%8C%E6%AF%94%E4%BB%B7%E7%B3%BB%E7%BB%9F%E6%B7%B1%E5%BA%A6%E9%80%86%E5%90%91%E6%8A%A5%E5%91%8A.md)  
**分析原则**：Clean-Room 逆向、算法与数据结构全景解密、完整开源 C# 复刻实现。

---

## 一、 产品定位与技术架构解密

### 1. 产品本质
`SuperWinner`（原名“利驰算王/快报价”）是配电箱柜制造企业用于**日常民建、工业与招投标快速核价**的核心产品。
与 ExWinner 专攻国家电网不同，SuperWinner 的杀手级功能是**“多品牌一键比价与换型替代”**：
当甲方图纸设计为高价格的合资品牌（如施耐德、ABB、西门子）时，成套厂必须提供等效国产一线（如正泰、常熟开关、上海良信、德力西）的比价方案供业主选择。SuperWinner 能够在 1 秒内将几百种元件批量等效替换，并输出包含各品牌“总价、分项成本、差价额、降本百分比”的对比报表。

### 2. 软件运行拓扑
- **独立桌面版**：`leadsoft.SuperWinner.exe` + `SuperWinnerService.exe`
- **Excel 内嵌外挂版**：`SuperWinner.dna`（利用开源 Excel-DNA 框架构建的 XLL 插件，直接在 Excel / WPS 菜单栏注入利驰专用报价面板）
- **核心组件**：
  - `leadsoft.superwinner.BLL.dll`（业务计算层）
  - `Leadsoft.Common.LdExternal.dll`（跨进程通信接口）
  - `leadsoft.superwinner.ExcelReport.Common.dll`（报表生成）

---

## 二、 多品牌一键比价与换型替换机理解密

通过逆向提取演示实例中的 `1.1替换关系.xlsx` 与 `1.2底价.xls`，彻底还原其比价换型数据结构：

### 1. 替换映射规则表结构 (`1.1替换关系.xlsx`)
```
[原设计指定品牌 (如正泰/施耐德)]           [目标替换品牌 (如施耐德/良信)]
品名   | 原始型号    | 原始品牌 | 系列    => 替换品名   | 目标型号        | 目标品牌 | 目标系列
隔离开关 | NH40-32/3  | 正泰    | 基础型   => 塑壳隔离开关 | NSX100m NA 50 3P| 施耐德  | NSX(进口)
隔离开关 | NH40-63/3  | 正泰    | 基础型   => 塑壳隔离开关 | NSX100m NA 100 3P| 施耐德 | NSX(进口)
隔离开关 | NH40-250/3 | 正泰    | 基础型   => 塑壳隔离开关 | NSX250 NA 250 3P| 施耐德  | NSX(进口)
微断   | DZ47-63 C16| 正泰    | 微断    => 微型断路器  | iC65N-C16A/1P   | 施耐德  | Acti9
```

### 2. 底价与折扣算法模型 (`1.2底价.xls` & `MySetting.ini`)
成套厂实际报价采用**面价-折扣模型（List Price - Discount Model）**：
$$\text{采购成本价 } P_{\text{cost}} = P_{\text{market}} \times D_{\text{in}} \quad (D_{\text{in}} \text{ 为厂家下浮进价折扣，如 } 0.35 \sim 0.65)$$
$$\text{对外报价单价 } P_{\text{quote}} = P_{\text{cost}} \times (1 + \mu_{\text{margin}}) \quad (\mu_{\text{margin}} \text{ 为成套厂毛利率加成，如 } 12\%)$$
$$\text{含税单价 } P_{\text{tax}} = P_{\text{quote}} \times (1 + 0.13) \quad (\text{保留 2 位小数，不含税保留 6 位小数})$$

---

## 三、 完整独立 C# 开源复刻实现 (Clean-Room Implementation)

以下为独立编写的多品牌比价换型引擎实现：

```csharp
using System;
using System.Collections.Generic;
using System.Linq;

namespace IndependentSwitchgearPricing.BrandComparison
{
    public class BrandItem
    {
        public string BrandName { get; set; } = string.Empty;
        public string Model { get; set; } = string.Empty;
        public decimal MarketPrice { get; set; } // 目录面价
        public decimal DiscountRate { get; set; } // 进价折扣率
        public decimal CostPrice => MarketPrice * DiscountRate; // 采购底价
    }

    public class ComponentComparisonRow
    {
        public string ComponentName { get; set; } = string.Empty;
        public int Quantity { get; set; } = 1;
        public Dictionary<string, BrandItem> BrandVariants { get; set; } = new();
    }

    public class MultiBrandComparisonEngine
    {
        private readonly List<ComponentComparisonRow> _rows = new();

        public void AddRow(ComponentComparisonRow row) => _rows.Add(row);

        /// <summary>
        /// 一键生成多品牌总价对比表与降本分析
        /// </summary>
        public Dictionary<string, (decimal TotalCost, decimal TotalQuote, decimal CostSavingRatio)> GenerateComparison(decimal targetMarginRate = 0.12m)
        {
            var brandTotals = new Dictionary<string, decimal>();

            foreach (var row in _rows)
            {
                foreach (var (brand, item) in row.BrandVariants)
                {
                    if (!brandTotals.ContainsKey(brand)) brandTotals[brand] = 0;
                    brandTotals[brand] += item.CostPrice * row.Quantity;
                }
            }

            var result = new Dictionary<string, (decimal, decimal, decimal)>();
            decimal baseLineCost = brandTotals.Values.Max(); // 以最高成本品牌（如施耐德）为基准线 100%

            foreach (var (brand, cost) in brandTotals)
            {
                decimal quote = Math.Round(cost * (1 + targetMarginRate) * 1.13m, 2);
                decimal savingRatio = baseLineCost > 0 ? Math.Round((baseLineCost - cost) / baseLineCost * 100m, 2) : 0;
                result[brand] = (cost, quote, savingRatio);
            }

            return result;
        }
    }
}
```
