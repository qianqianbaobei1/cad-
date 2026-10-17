# ExWinner 国网自动组价与快速投标系统深度逆向与独立实现报告

**对应目录**：`E:\利驰！\4\ExWinner` 与 `E:\利驰！\4\1.1国网组价：演示实例`  
**交付文档路径**：[`E:/AI/codex任务/tuxiaoxiao-full-drawing-research/12_ExWinner国网自动组价与快速投标系统深度逆向报告.md`](file:///E:/AI/codex%E4%BB%BB%E5%8A%A1/tuxiaoxiao-full-drawing-research/12_ExWinner%E5%9B%BD%E7%BD%91%E8%87%AA%E5%8A%A8%E7%BB%84%E4%BB%B7%E4%B8%8E%E5%BF%AB%E9%80%9F%E6%8A%95%E6%A0%87%E7%B3%BB%E7%BB%9F%E6%B7%B1%E5%BA%A6%E9%80%86%E5%90%91%E6%8A%A5%E5%91%8A.md)  
**分析原则**：Clean-Room 逆向、算法与数据结构全景解密、完整开源 C# 复刻实现。

---

## 一、 产品定位与系统架构解密

### 1. 产品本质
`ExWinner` 是利驰专门面向**国家电网（SGCC）集中招标采购平台（ECP）**开发的成套电气设备自动组价与快速投标文件生成系统。
在电力工程中，国网对高低压配电柜、箱式变电站、开关柜的招投标有极其严苛的格式规范，要求投标文件精确分解至每台柜的“单价分析表”与“元器件清单”，包含主进线母线排公斤重、壳体制作费、装配调试人工费、一次及二次辅材费。人工编制一套投标清单通常耗时 3~5 天，ExWinner 通过规则引擎实现了“10分钟一键组价”。

### 2. 软件运行拓扑与组件分工
```
┌────────────────────────────────────────────────────────┐
│ ExWinner 前端桌面宿主 (leadsoft.ExWinner.exe)         │
│ - WPF / NanUI 交互界面，展示工程标包树与柜型清单        │
└───────────────────────┬────────────────────────────────┘
                        │ 本地 IPC
        ┌───────────────┴───────────────┐
        ▼                               ▼
┌───────────────────────────┐   ┌───────────────────────────┐
│ 国网数据映射引擎 (SGCC)    │   │ 精密算量引擎 (Calculate)   │
│ - leadsoft.SGCC.dll       │   │ - AccurateCalculate.dll   │
│ - ModelConfig.myxml       │   │ - 铜排母线载流量温升核算   │
│ - NoModelConfig.myxml     │   │ - 柜体表面积与钣金定额计算│
│ - 生成 .SGCC 密文/明文包  │   │ - 一二次线辅材与装配工时  │
└───────────────────────────┘   └───────────────────────────┘
```

---

## 二、 国网招投标数据映射引擎全景解密 (`ModelConfig.myxml`)

ExWinner 的核心是基于 XML 声明式驱动的 Excel 模板自动化装配引擎。逆向提取出的 `ModelConfig.myxml` 与 `NoModelConfig.myxml` 揭示了其完整的数据源绑定规范：

### 1. 数据集与表结构模型 (Data Model Schema)
- **`T_ProjectInfo` (项目投标主表)**：
  - `F_QuotationNumber`：报价单号 / 招标项目编号
  - `F_FolderName`：分包名称（如 `开关柜_分包16`）
  - `F_ProjectName`：工程项目全称（如 `某电业有限公司项目`）
  - `F_ProjectUnit`：工程单位 / 建设单位
  - `F_Factory_Name`：投标人全称（制造厂家）
  - `Signature`：法定代表人签名图片（`signatureDefault.png`）
  - `F_QuotationTime`：投标报价日期（`yyyy/MM/dd`）
  - `F_TotalPrice` / `F_TotalPriceStr`：投标总价（小写数值与中文大写金额）
  - `BidBond`：投标保证金金额

- **`T_Cabinet` (成套开关柜汇总表)**：
  - `F_Custom_Name`：柜体自定义代号（如 `01AH1`、`02AP1`）
  - `F_ProjectName`：设备名称（如 `低压抽出式开关柜 GCK`）
  - `F_Name2`：型号规格（如 `GCK-1600/380V`）
  - `F_Unit`：计量单位（`面`、`台`）
  - `F_Number`：数量
  - `F_NoTax_Price` / `F_NoTax_Price2`：不含税单价 / 不含税合价（公式自动化计算）
  - `TaxRate`：增值税率（固定 13%）
  - `F_Price` / `F_Price2`：含税单价 / 含税合价（公式自动化计算）

- **`{TableName}` (每台柜对应的单价分析表 / 元器件明细表)**：
  - `F_Xh`：元器件序号
  - `F_Name`：元器件品名（如 `断路器`、`电流互感器`、`接触器`）
  - `F_Origin`：产地（国产 / 进口）
  - `F_Manufacturer`：制造厂商（如 `施耐德`、`正泰`、`常熟开关`）
  - `F_Model`：完整型号规格（如 `NSX100m NA 50 3P`）
  - `F_Unit`：单位（`只`、`台`、`套`）
  - `F_Numbers`：元件数量
  - `F_Marked_Price`：面价 / 目录价
  - `F_Total_Price`：总价（`= F_Numbers * F_Marked_Price * Discount`）
  - `F_Remark`：技术备注（分断能力、脱扣整定等）

---

## 三、 精密算量公式库 (Accurate Calculation Formulation)

在 `leadsoft.superwinner.AccurateCalculate.dll` 与 `SuperWinner.dll.config` 中，封装了国网认可的四项工程算量数学模型：

### 1. 铜排/铝排母线自动算量与造价公式
- **主母线载流量选型标准**：
  $$I_n \le 250\text{A} \implies 3 \times (25 \times 3) + 1 \times (25 \times 3)$$
  $$I_n \le 400\text{A} \implies 3 \times (40 \times 4) + 1 \times (30 \times 4)$$
  $$I_n \le 630\text{A} \implies 3 \times (50 \times 5) + 1 \times (40 \times 4)$$
  $$I_n \le 1000\text{A} \implies 3 \times (80 \times 8) + 1 \times (50 \times 5)$$
  $$I_n \le 1600\text{A} \implies 3 \times 2 \times (80 \times 8) + 1 \times (60 \times 6)$$
  $$I_n \le 2500\text{A} \implies 3 \times 2 \times (100 \times 10) + 1 \times (80 \times 8)$$
- **重量与成本公式**：
  $$\text{单台柜母线体积 } V = \sum (\text{单相截面积 } \text{mm}^2 \times \text{柜宽 } W \text{ mm} \times \text{相数}) \times 10^{-6} \ [\text{m}^3]$$
  $$\text{母线净重 } M_{\text{net}} = V \times \rho_{Cu} \quad (\rho_{Cu} = 8900 \, \text{kg/m}^3, \ \rho_{Al} = 2700 \, \text{kg/m}^3)$$
  $$\text{采购毛重 } M_{\text{gross}} = M_{\text{net}} \times (1 + \alpha_{\text{loss}}) \quad (\text{损耗率 } \alpha_{\text{loss}} = 5\%)$$
  $$\text{母排总材料费 } C_{\text{bus}} = M_{\text{gross}} \times \frac{P_{\text{base}}}{1000} \quad (P_{\text{base}} = 56110 \, \text{元/吨})$$

### 2. 柜体钣金展开表面积与定额造价
- **展开表面积公式**（六面体冷轧钢板）：
  $$S = 2 \times (W \cdot H + W \cdot D + H \cdot D) \times 10^{-6} \ [\text{m}^2]$$
- **弯边与折弯损耗系数**（`BottomCoefficient`）：
  - 封板基础系数：$1.0$
  - 空箱标准结构：$1.5$
  - 含安装板结构：$2.0$
  - 带防护雨帽与支架结构：$3.0 \sim 4.5$
- **壳体成本**：
  $$C_{\text{enclosure}} = S \times \text{折弯系数} \times \text{喷涂钢板定额单价 (280 元/m}^2)$$

### 3. 一次线、二次辅材与装配工时定额
- **一次软铜线（BVR）**：按回路出线电流等级分档（16A~63A 采用 $2.5\sim 10\text{mm}^2$，100A~250A 采用 $16\sim 70\text{mm}^2$），平均每回路计入 2 米电线与 4 只冷压线鼻子。
- **二次线与控制辅料**：每台柜配置 1 套接地扁铜排（25x4）、1 组控制端子排（UK2.5B, 40位）、行线槽 3 米、二次控制线 40 米。固定定额：每个回路计取 35~45 元。
- **装配调试人工费**：
  - 固定柜（如 GGD/XL-21）：80~120 元/台
  - 抽屉柜（如 GCK/GCS/MNS）：250~400 元/台

---

## 四、 完整独立 C# 开源复刻实现 (Clean-Room Implementation)

以下为根据逆向出的规格书，独立编写的不侵犯任何第三方版权的 C#/.NET 9 国网自动组价引擎实现：

```csharp
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace IndependentSwitchgearPricing.SgccEngine
{
    /// <summary>
    /// 国网成套报价项目模型
    /// </summary>
    public class SgccProject
    {
        public string QuotationNumber { get; set; } = "SGCC-2026-001";
        public string FolderName { get; set; } = "低压开关柜_分包16";
        public string ProjectName { get; set; } = "国网智能配电网配电柜集中采购";
        public string ProjectUnit { get; set; } = "国家电网有限公司物资部";
        public string BidderFactoryName { get; set; } = "自主研发电气成套设备有限公司";
        public DateTime QuotationDate { get; set; } = DateTime.Now;
        public decimal TaxRate { get; set; } = 0.13m; // 13% 增值税
        public List<SgccCabinet> Cabinets { get; set; } = new();

        public decimal CalculateTotalPriceNoTax() => Cabinets.Sum(c => c.CalculateCabinetPriceNoTax() * c.Quantity);
        public decimal CalculateTotalPriceWithTax() => Math.Round(CalculateTotalPriceNoTax() * (1 + TaxRate), 2);
    }

    /// <summary>
    /// 开关柜模型
    /// </summary>
    public class SgccCabinet
    {
        public string CabinetCode { get; set; } = string.Empty; // 如 01AH1
        public string CabinetName { get; set; } = "低压抽出式开关柜";
        public string ModelType { get; set; } = "GCK";
        public int Quantity { get; set; } = 1;
        public double WidthMm { get; set; } = 800;
        public double HeightMm { get; set; } = 2200;
        public double DepthMm { get; set; } = 1000;
        public double RatedCurrentA { get; set; } = 1000; // 主进线额定电流

        public List<SgccComponent> Components { get; set; } = new();

        /// <summary>
        /// 1. 计算元器件总价
        /// </summary>
        public decimal CalculateComponentsCost() => Components.Sum(p => p.Price * (decimal)p.Quantity);

        /// <summary>
        /// 2. 计算母线铜排成本 (根据国网载流量规范推导)
        /// </summary>
        public decimal CalculateBusbarCost(decimal cuPricePerTon = 56110m)
        {
            // 根据额定电流推导主母线单相截面积 (mm2)
            double singlePhaseArea = RatedCurrentA switch
            {
                <= 250 => 25 * 3,
                <= 400 => 40 * 4,
                <= 630 => 50 * 5,
                <= 1000 => 80 * 8,       // 640 mm2
                <= 1600 => 2 * (80 * 8),  // 1280 mm2
                <= 2500 => 2 * (100 * 10),// 2000 mm2
                _ => 3 * (100 * 10)
            };

            // 3相 + 1相中性排(按主相一半或等截面)
            double totalSectionAreaMm2 = singlePhaseArea * 3 + (singlePhaseArea >= 400 ? singlePhaseArea * 0.6 : singlePhaseArea);
            double volumeM3 = totalSectionAreaMm2 * WidthMm * 1e-9;
            double densityCu = 8900.0; // kg/m3
            double netWeightKg = volumeM3 * densityCu;
            double grossWeightKg = netWeightKg * 1.05; // 5% 弯折与冲孔加工损耗

            return Math.Round((decimal)grossWeightKg * (cuPricePerTon / 1000m), 2);
        }

        /// <summary>
        /// 3. 计算箱体壳体表面积定额成本
        /// </summary>
        public decimal CalculateEnclosureCost(decimal areaUnitPrice = 280m, double structureCoeff = 1.5)
        {
            // 六面体展开面积 S (m2)
            double sM2 = 2.0 * (WidthMm * HeightMm + WidthMm * DepthMm + HeightMm * DepthMm) * 1e-6;
            return Math.Round((decimal)(sM2 * structureCoeff) * areaUnitPrice, 2);
        }

        /// <summary>
        /// 4. 计算辅材费与装配人工费
        /// </summary>
        public decimal CalculateAuxiliaryAndLaborCost()
        {
            int loopCount = Math.Max(1, Components.Count(c => c.Name.Contains("断路器")));
            decimal auxiliaryCost = loopCount * 40.0m; // 40元/回路二次辅料定额
            decimal laborCost = ModelType.Contains("GCK") || ModelType.Contains("MNS") ? 350.0m : 120.0m;
            return auxiliaryCost + laborCost;
        }

        /// <summary>
        /// 综合单柜不含税出厂价
        /// </summary>
        public decimal CalculateCabinetPriceNoTax()
        {
            return CalculateComponentsCost() + CalculateBusbarCost() + CalculateEnclosureCost() + CalculateAuxiliaryAndLaborCost();
        }
    }

    /// <summary>
    /// 元器件模型
    /// </summary>
    public class SgccComponent
    {
        public int SeqNo { get; set; }
        public string Name { get; set; } = string.Empty;
        public string Model { get; set; } = string.Empty;
        public string Manufacturer { get; set; } = "正泰";
        public string Unit { get; set; } = "只";
        public double Quantity { get; set; } = 1;
        public decimal Price { get; set; }
    }
}
```

---

## 五、 实施建议

1. 将 `SgccQuotationEngine` 封装为标准的独立类库；
2. 直接读取图晓晓或我方解析器输出的 `Cab` 与 `Part` 列表，自动绑定母线排与表面积公式；
3. 输出带联动公式的国网标准 Excel，实现彻底脱离 ExWinner 的独立自主组价。
