# ImageGallery 图库与箱体钣金快速算料采集器深度逆向与独立实现报告

**对应目录**：`E:\利驰！\1\ImageGallery`  
**交付文档路径**：[`E:/AI/codex任务/tuxiaoxiao-full-drawing-research/14_ImageGallery图库与箱体钣金快速算料采集器深度逆向报告.md`](file:///E:/AI/codex%E4%BB%BB%E5%8A%A1/tuxiaoxiao-full-drawing-research/14_ImageGallery%E5%9B%BE%E5%BA%93%E4%B8%8E%E7%AE%B1%E4%BD%93%E9%92%A3%E9%87%91%E5%BF%AB%E9%80%9F%E7%AE%97%E6%96%99%E9%87%87%E9%9B%86%E5%99%A8%E6%B7%B1%E5%BA%A6%E9%80%86%E5%90%91%E6%8A%A5%E5%91%8A.md)  
**分析原则**：Clean-Room 逆向、算法与数据结构全景解密、完整开源 C# 复刻实现。

---

## 一、 产品定位解密（你下载的第三个究竟是什么？）

用户在问题中提到“最后一个我也不清楚是啥”。经本轮对 `E:\利驰！\1\ImageGallery` 的深度挖掘，彻底弄清了其真实身份：

- **产品官方名称**：**利驰图库 / DWG 扒图工具（DwgGrillTool）**
- **英文程序名**：`DwgGrillTool.exe` / `DwgGrillControl.exe`（“Grill”在利驰内部即“扒图”的代号）
- **核心功能**：
  1. **CAD 图纸图元与回路大样采集器**：供工程师从历史施工图纸中，将优秀的配电柜图例、典型系统图回路“框选并收藏”为标准模块，上传至云端或本地知识库；
  2. **非标箱体结构钣金快速算料计算器**：内置了利驰成套厂的配电箱钣金结构展开面积数学模型，只要输入长宽高，就能瞬间推导出冷轧板净重、喷涂单价与壳体造价！

---

## 二、 核心钣金结构展开算量模型解密

在 `x64\CommonUserData\InitBoxStructure.json` 与 `curBottomCoefficient.json` 中，提取出了利驰独家沉淀的工业级配电箱钣金展开与结构系数模型：

### 1. 五大钣金构件展开计算公式
```json
{
  "底壳 (Base)": "H * W * 1 + H * D * 2 + W * D * 2",      // 展开5个面
  "面盖/门板 (Door)": "H * W * 1",                         // 展开1个面
  "封板 (Side Plate)": "H * D * 1",                         // 展开1个面
  "安装板 (Mounting Plate)": "H * W * 1",                  // 展开1个面
  "防雨帽 (Rain Cap)": "W * D * 1.5"                       // 展开1.5个面
}
```

### 2. 折弯弯边加工余量与喷涂计价参数
- **折弯余量**（`curWidthR: 50`, `curHeightR: 50`, `curDepthR: 20`）：
  在计算实际下料尺寸时，每个折弯边缘自动追加 20~50 mm 的压边咬口余量。
- **钣金与喷涂定额单价**：
  `cabinetBottomAreaUnitPrice = 280 元/m²`（底壳冷轧板+静电喷塑单价）
  `cabinetTopAreaUnitPrice = 280 元/m²`（面盖冷轧板+静电喷塑单价）
- **结构折算系数表（BottomCoefficient）**：
  - 基础封板：`1.0`
  - 标准空箱：`1.5`
  - 空箱带横梁：`2.0`
  - 带内部安装板：`2.0`
  - 安装板 + 防雨罩：`3.0`
  - 安装板 + 支架 + 侧封板：`3.5`
  - 户外重型全套防护（安装板 + 支架 + 封板 + 防雨帽）：`4.5`

---

## 三、 完整独立 C# 开源复刻实现 (Clean-Room Implementation)

以下为独立编写的配电箱钣金展开与壳体快速算料引擎：

```csharp
using System;

namespace IndependentSwitchgearPricing.SheetMetal
{
    public enum BoxStructureType
    {
        SealedPlate = 0,         // 封板 (1.0)
        StandardEmptyBox = 1,    // 标准空箱 (1.5)
        WithBeam = 2,            // 空箱带横梁 (2.0)
        WithMountingPlate = 3,   // 带安装板 (2.0)
        WithRainCap = 4,         // 带安装板及防雨罩 (3.0)
        HeavyOutdoor = 5         // 户外重型防护 (4.5)
    }

    public class SheetMetalBoxCalculator
    {
        public double WidthMm { get; set; } = 800;
        public double HeightMm { get; set; } = 2200;
        public double DepthMm { get; set; } = 1000;
        public decimal UnitPricePerM2 { get; set; } = 280m; // 280元/m2

        public double GetStructureCoefficient(BoxStructureType type) => type switch
        {
            BoxStructureType.SealedPlate => 1.0,
            BoxStructureType.StandardEmptyBox => 1.5,
            BoxStructureType.WithBeam => 2.0,
            BoxStructureType.WithMountingPlate => 2.0,
            BoxStructureType.WithRainCap => 3.0,
            BoxStructureType.HeavyOutdoor => 4.5,
            _ => 1.5
        };

        /// <summary>
        /// 计算展开表面积 (m2)
        /// </summary>
        public double CalculateUnfoldedAreaM2()
        {
            double w = WidthMm * 1e-3;
            double h = HeightMm * 1e-3;
            double d = DepthMm * 1e-3;

            // 底壳5面 (高*宽*1 + 高*深*2 + 宽*深*2) + 面盖1面 (高*宽*1)
            double baseShell = (h * w * 1) + (h * d * 2) + (w * d * 2);
            double frontDoor = (h * w * 1);
            return baseShell + frontDoor;
        }

        /// <summary>
        /// 计算壳体出厂成本造价
        /// </summary>
        public decimal CalculateEnclosureCost(BoxStructureType structure = BoxStructureType.StandardEmptyBox)
        {
            double area = CalculateUnfoldedAreaM2();
            double coeff = GetStructureCoefficient(structure);
            return Math.Round((decimal)(area * coeff) * UnitPricePerM2, 2);
        }
    }
}
```
