# 逆向分析：CAD快速看图（Mac 5.2.4）—— 图纸兼容性、字体兼容性、读取能力

> 样本：`CADReaderMac.dmg` → `CAD快速看图.app`，Bundle ID `com.glodonyuntu.cadreadermac`（广联达），版本 5.2.4.16，universal（x86_64 + arm64）
> 方法：只读挂载 DMG，静态分析 arm64 主程序（未运行）。REA 打开成功，但其 Mach-O 解析（dyld_info）在该文件上报“load command #14 越界”，Ghidra 对 50MB 主程序首次分析超时；改用 `otool` 反汇编 + `nm`/`c++filt` 符号（主程序**未去符号**，67,691 个）+ UTF-32 字符串提取完成。
> 目的：找出它在“预览不丢线不丢字、字体兼容、图纸解析”上是怎么做的，对照我们扒图/拆量的能力缺口。

---

## 0. 结论摘要

1. **引擎是 ODA（Open Design Alliance）Teigha / Drawings SDK，静态编译进主程序**，广联达自己维护了一个分支（编译路径 `Teigha-yuntu`）。`OdDb* / OdGe* / OdGi* / OdGs*` 符号 2.6 万+；`adinit.dat` 头部为 “OpenDWG Toolkit init data”。
2. **DWG 版本全覆盖**：AC1001 ~ AC1032（R1.x 到 AutoCAD 2018 格式，2018–2025 都是 AC1032），外加 DXF。
3. **天正是它最大的护城河**：自己实现了 **101 个天正自定义对象类（TDb*）+ 75 个天正解析器**，覆盖建筑 / 电气 / 给排水 / 暖通 / 符号标注 / 文字表格，不依赖天正插件就能读出天正对象的数据和文字；还内置天正文字解密（`TgDataEncrypt::TDUnEncrypt`）和“天正转 T3”命令。
4. **浩辰、中望等没有专门代码**：它们的图按标准 DWG 读；其中的自定义对象走 ODA 的代理图形（`oddbExplodeProxyGraphics` + `OdDbProxyEntity_Parser`）——图里保存了代理图形就能显示，没保存就是空的。
5. **字体：它并不保留原图字体。** 看图界面上所有文字都由自己的 `CTextItem` 用内置单线字库 `xfont.jff`（`CXFont::getGlyph`）重画；ODA 的 SHX/TTF 绘字只在 ODA 默认绘制管线里用。好处是永远不缺字、不出问号、速度快；代价是字形与原图不同。**这一点不满足我们“原有字体不能变”的要求，不能照抄。**
6. **读取能力的核心是“解析器层”**：112 个 `*_Parser`（37 个标准对象 + 75 个天正对象）把 DWG 对象转成自己的场景对象（线集、弧集、填充、文字项…），在此之上做了：表格提取（`GTable`→Excel）、设备识别计数（`CIdentifyDeviceHandler`→Excel）、文字提取与查找（→Excel）、测量、图纸比对、批量导 PDF、批量分图。
7. **我们项目已有两处移植，核查结果**：
   - `extractor/gtable.py`（表格提取）——思路与原程序方法名一一对应，本次未逐行核对。
   - `extractor/tangent.py`（天正文字解密）——**有实质性错误**：异或方式错、缺少版本 0x11/0x13/0x14 及分段密钥表、缺少版本≥0x11 的后处理。天正中文会解成乱码。详见第 6 节。

---

## 1. 程序结构

```
CAD快速看图.app
├── MacOS/CAD快速看图          105MB universal，ODA Teigha + 广联达解析/渲染/业务全部静态链接
├── Frameworks/Qt*.framework   Qt 5.15.2（界面）
├── Helpers/CADReader-Editor.app  编辑器子程序（同样带 adinit.dat、xfont.jff）
└── Resources/
    ├── adinit.dat             ODA/OpenDWG 初始化数据（代码页、默认表）
    ├── xfont.jff              广联达自有单线字库（2.2MB，坐标笔画数据）
    └── xfont_intl.jff         国际版字库
```

链接的系统库：Metal / OpenGL（渲染）、PDFKit（导出 PDF）、StoreKit（内购）。第三方静态库：OpenSSL 1.1.0f、FreeType（ODA 内置 `_oda_ft_*`）、LibRaw / FreeImage / WebP / OpenEXR（图片）、QXlsx（导出 Excel）、ACIS/OdModeler（三维实体）。

数据流：

```
DWG/DXF ──ODA 读入──▶ OdDb 对象树
                         │
                         ├─ 标准对象 ─▶ OdDb*_Parser (37 个)  ─┐
                         ├─ 天正对象 ─▶ TDb*::dwgInFields 解密读字段 ─▶ TDb*_Parser (75 个) ─┤
                         └─ 未知自定义对象 ─▶ 代理图形炸开 ─▶ OdDbProxyEntity_Parser ─┘
                                                                                   ▼
                                    自有场景对象：CLineSetItem / CArcSetItem / CEllipseSetItem /
                                    CSolidHatchSetItem / CLineHatchSetItem / CThickPolylineSetItem /
                                    CPointSetItem / CTextItem / COleItem …
                                                                                   ▼
                                    自有渲染（Metal/OpenGL）+ 业务功能（测量/表格/设备/文字/比对）
```

---

## 2. 图纸兼容性

### 2.1 DWG / DXF 版本
字符串中完整存在版本标识 `AC1001 … AC1015, AC1018, AC1021, AC1024, AC1027, AC1032`，即 R2.x 至 AutoCAD 2018 文件格式（2018–2025 均为 AC1032）。另有 DXF 读入（`OdDbDxfFiler`），代码页支持 `DWGCODEPAGE`、gb2312、big5。

### 2.2 天正（核心优势）
101 个 `TDb*` 类，每个都实现了 `dwgInFields`（读 DWG 二进制字段），58 个实现 `dxfInFields`；配套 75 个 `TDb*_Parser` 转成可显示/可搜索的对象。按专业分组：

| 专业 | 类 |
|---|---|
| **电气（拆量最相关）** | `TDbWireDimNew`（导线标注）、`TDbEquipDimNew`（设备标注）、`TDbCableDim`（电缆标注）、`TDbEEquipment`（电气设备）、`TDbEFitting`（电气配件）、`TDbECabletry`（电缆桥架）、`TDbCableTrench`（电缆沟） |
| 文字 / 表格 | `TDbText`、`TDbMText`、`TDbMTextNew`、`TDbSheet`（天正表格）、`TDbDrawingName`（图名）、`TDbSymbWithText`、`TDbSymbComposing`（组合符号文字） |
| 建筑 | `TDbWall`、`TDbWallPatch`、`TDbColumn`、`TDbOpening`、`TDbGroupOpening`、`TDbBandWin`、`TDbCornerWin`、`TDbCurtainWall`、`TDbSlab`、`TDbSpace`、`TDbBalCony`、`TDbApron`、`TDbHandRail`、各类楼梯（`TDbLineStair/ArcStair/RectStair/MultiStair/AutoStair/CornerStair/ScissorsStair…`） |
| 给排水 | `TDbPipe`、`TDbFloorPipe`、`TDbDoubleLinePipe`、`TDbPipeFitting`、`TDbPipeValve`、`TDbPipeText`、`TDbPipeLabel`、`TDbPipeMultDim`、`TDbVPipeDim`、`TDbPump`、`TDbWaterTank`、`TDbWell`、`TDbPool`、`TDbFireExtinguisher`、`TDbFlangeFitting` |
| 暖通 | `TDbHvacDuct`、`TDbHvacEquipment`、`TDbHvacFitting`、`TDbHvacDimension`、`TDbRadiator` |
| 符号 / 标注 | `TDbDimension(2)`、`TDbRadiusDim`、`TDbAxisLabelSet`、`TDbSymbElevation`、`TDbSymbIndexPointer`、`TDbSymbSection`、`TDbSymbCut`、`TDbSymbNorthThumb`、`TDbSymbArrow`、`TDbSymbCoord`、`TDbSymbMultiLeader`、`TDbSymbRupture`、`TDbSymbSymmetry`、`TDbSymbDrawingIndex` |

配套能力：
- 天正字段加密读取：`TgDataEncrypt::TDReadInt8/16/32/Double/Bool/Point2d/Point3d/Vector3d/String` + `TDUnEncrypt`。
- 天正转普通 CAD：`CConvertTangentCommand`、`CSelectConvertTangantAreaCommand`（可框选区域转换）。

### 2.3 浩辰、中望及其他厂商
全量符号与字符串中**没有** GstarCAD / ZWCAD / 鸿业 / PKPM / 理正 的专门代码。这类图：
- 标准实体（线、文字、块、标注、填充…）由 ODA 正常读取；
- 它们的自定义对象按 ODA 代理对象处理：`oddbBeginProxyGraphics / oddbExplodeProxyGraphics` 把保存在图里的代理图形炸成普通几何，再由 `OdDbProxyEntity_Parser` 显示。
- **风险**：若原软件存图时没有保存代理图形（PROXYGRAPHICS=0），这些对象会显示为空。

### 2.4 其他
外部参照（`CExternalRefManageDialog`、云端外参）、光栅图片（`OdDbRasterImage_Parser`）、OLE（`OdDbOle2Frame_Parser`）、表格对象（`OdDbTable_Parser`）、多重引线、尺寸标注、形位公差（`OdDbFcf_Parser`）、三维实体/面域（ACIS）。

---

## 3. 字体兼容性

### 3.1 三层字体能力
| 层 | 实现 | 作用 |
|---|---|---|
| ODA SHX | `OdShxFont`、`OdShxBigFont`（含 `EOdShxBigFont` 异常） | 读 SHX 字体与中文大字体 |
| ODA TrueType | 内置 FreeType（`_oda_ft_*`），扫描 `/System/Library/Fonts` | 读 TTF/TTC |
| 字体映射 | `OdFontMapper::lookUp / lookUpNesting`、`OdFontTable::createShx / createTtf`、系统变量 `FONTALT` | 找不到字体时按映射替换；日志 “font %ls by font %ls”“by default font”“by default gdt font” |
| **广联达自有** | `CXFont`（`initial / load / loadCADSymbol / getGlyph / getTextWidth / getUnicodeCharWidth`）+ `CFontDictionary`（`addFont / getFontFilename`）+ `xfont.jff` | **看图界面实际使用的字体** |

内置的默认字体名只有 `txt.shx`、`simplex.shx`、`gdt.shx`、`@extfont2.shx`、`Arial`；**没有打包任何 SHX 大字体或中文 TTF**——中文全靠 `xfont.jff`。

### 3.2 看图界面的文字是怎么画出来的（调用链实测）
```
OdDbText_Parser / OdDbMText_Parser / OdDbAttributeDefinition_Parser /
OdDbBlockReference_Parser::ParseBlockAttribute / OdDbMInsertBlock_Parser::ParseBlockAttribute
        └─ CFontDictionary::addFont(样式字体文件名)        ← 只记录字体名
TDbText/TDbMText/TDbMTextNew/TDbSheet/TDbCableDim/TDbSymbComposing _Parser
        └─ CXFont::getTextWidth / getUnicodeCharWidth      ← 用自有字库量宽度、断行
CTextItem::getHTextLineArray / getVTextLineArray
        └─ CRenderString::getNextChar（处理 %%c %%d %%p 等特殊符号）
        └─ CXFont::getGlyph(unicode)                         ← 取单线字形，生成线段数组
CTextItemSet::painting                                       ← 作为线集一起绘制
```
`OdShxFont::drawCharacterImp` 只被 `OdGiDefaultContext::drawText` 调用（ODA 默认绘制管线，看图主界面不用）。

**结论**：快速看图把所有文字统一换成自己的单线字体绘制，原图的 SHX/TTF 字形、字宽比例**不保留**。
- 优点：永远不缺字、不出“?”、不受用户电脑字体影响；文字包围框由同一字库计算，框选、搜索、测量的位置一致。
- 缺点：字形与 AutoCAD 中看到的不同；宽度因子、特殊 SHX 符号（如 gdt、自定义形）可能不一致。

### 3.3 对我们的启示
我们的目标是“原有字体不能变”，所以字体策略要**和它反过来**：
1. 主路径用真实字体：SHX（含大字体）+ TTF，按文字样式取字体文件；
2. 建一个“字体仓库”：常见 SHX 大字体（hztxt / gbcbig / tssdchn / hzfs 等）与常用中文 TTF；
3. 按“字体映射表 → 同类替换 → 兜底字体”的顺序找字，并且**只对缺失的字逐字兜底**（借鉴 `CXFont` 的思路，但只作为最后一层），同时记录“哪些字体被替换了”供用户查看；
4. 文字包围框用**实际所用字体的度量**计算，保证框选不漏字。

---

## 4. 读取能力

### 4.1 解析器清单（决定“读得到什么”）
- **标准对象 37 个**：`2dPolyline, 3dPolyline, 3dSolid, ArcAlignedText, Arc, AttributeDefinition, BlockReference(+属性), Body, Circle, Dimension, Ellipse, Entity, Face, Fcf, Hatch, Leader, Line, MInsertBlock(+属性), MLeader, MText, Mline, Ole2Frame, Point, PolyFaceMesh, PolygonMesh, Polyline, ProxyEntity, RasterImage, Ray, Region, Shape, Solid, Spline, Table, Text, Trace, Viewport`
- **天正对象 75 个**：见 2.2。

注意它对**块属性、MINSERT 阵列块属性、代理对象、表格对象、形（Shape）**都有单独解析——这些正是“框选后内容缺失”的常见来源。

### 4.2 业务功能（与扒图/拆量直接相关）
| 功能 | 类 / 方法 | 做法要点 |
|---|---|---|
| **表格提取** | `CExtractTableCommand` → `GTable::extractHVLines / mergeLines / removeFrameLines / buildRelation2DArray / buildMergeCellList / loadTexts / calcFrameData / saveToExcelFile` | 框选区域 → 取水平/垂直线 → 断线焊接与共线合并 → 去外框 → 求交得网格与合并单元格 → 文字按包围框落格 → 导 Excel（我们已移植为 `gtable.py`） |
| **设备识别计数** | `CIdentifyDeviceCommand`、`CIdentifyDeviceHandler::buildLineBoxTree / buildArcBoxTree / buildEllipseBoxTree / buildTextBoxTree / findAllDevices / findDevicesAllCircle / findDevicesNotAllCircle / isTwoDeviceLineEqual / isTwoArcEqual / isTwoEllipseEqual / getPointEpsilon / transformBox / findText / removeOverlapDevices`、`GDeviceManageDialog::doExportToExcel` | 用户框选一个样例设备 → 对线/弧/椭圆/文字分别建空间索引（BoxTree）→ 在全图按几何相似（带容差、可旋转变换）找同形状 → 匹配内部文字 → 去重叠 → 列表 + 导 Excel。**不依赖块名**，炸开的图例也能数 |
| 文字提取 | `CExtractTextsCommand`、`CExtractTextsResultDialog` | 框选区域提取全部文字 |
| 文字查找 | `TextFinderDialog::doSearch / changeSearchArea / setPolygonRange / doExportToExcel`、`CTextItem::findKeyText / getKeyTextPosition` | 关键字全图/区域/多边形范围查找，结果定位并导 Excel |
| 测量 | `CMeasure*Command`（长度、连续长度、面积、圆、弧长、角度、半径、点到线、填充边界面积）、`CMeasureCommentsSumCommand`（测量汇总导 Excel） | |
| 图纸比对 | `CCompareDrawingCommand`、`CCompareSetBasePtCommand` | 两版图对比 |
| 批量 | `CBatchExportPdfCommand`、`CBatchDrawingDivideCommand`（批量分图） | 多图框拆分 |
| 天正转换 | `CConvertTangentCommand` | 天正对象转标准 CAD |

---

## 5. 对照我们扒图/拆量的能力缺口

| 我们的需求 | 快速看图的做法 | 我们应补的能力 | 优先级 |
|---|---|---|---|
| 预览不丢线 | 112 个解析器全覆盖，含代理对象炸开、MINSERT、形、OLE、光栅 | 建“实体类型覆盖清单”，逐类对照；代理对象一律炸开代理图形；渲染前统计“未处理实体类型/数量”并在界面提示 | 高 |
| 预览不丢字 | 文字、多行文字、块属性、阵列块属性、天正文字/表格/标注全部单独解析 | 补齐块属性（含嵌套块、MINSERT）、天正文字类；表格对象展开为文字 | 高 |
| 原有字体不变 | **不做**（统一单线字库） | 走真实 SHX/大字体/TTF + 字体仓库 + 映射 + 逐字兜底 + “替换字体清单” | 高 |
| 框选后内容不缺 | 所有对象转成带包围框的场景项，空间索引（BoxTree）框选；文字框由同一字库计算 | 文字包围框按实际字体度量算；块按炸开后的子对象参与框选；天正对象按解析出的几何参与框选 | 高 |
| 天正图纸 | 101 类自研解析 + 解密 | **修 `tangent.py`**（第 6 节）；按电气优先补 `TDbWireDimNew / TDbEquipDimNew / TDbCableDim / TDbEEquipment / TDbSheet / TDbText / TDbMText(New)` 的字段读取 | 高 |
| 浩辰/中望等 | 标准读 + 代理图形 | 同左；对“代理对象无图形”给出明确提示，而不是静默空白 | 中 |
| 系统图表格结构化 | GTable 网格法 | 已移植 `gtable.py`，补回归样例（合并单元格、斜线表头、多行文字） | 中 |
| 数图例/设备 | 几何相似匹配，不依赖块名 | 新增“框一个样例 → 全图找同形”的计数能力，可用于平面图设备统计 | 中 |
| 关键字定位 | 文字查找 + 区域/多边形范围 | 拆量页“依据⌖”可复用：按型号/回路号全文查找定位 | 低 |

---

## 6. 天正文字解密：`backend/extractor/tangent.py`（已按反汇编修正）

反汇编 `TgDataEncrypt::TDUnEncrypt(int version, OdString const&)`（arm64 切片 `0x1005544fc`）。`this` 的首个 int 字段（下称 `n`，对象自带的加密参数）参与密钥计算；`TgDataEncrypt::TDReadInt32`（`0x100554b5c`）在 version ≥ 0x10 时才走解密。libc++ 调用经 `__la_symbol_ptr` 绑定表核对（otool 对桩名的标注是错的）：`wstring::append(1, ch)`、`wstring::erase(pos, n)`、`wstring::operator=`。

### 6.1 实际算法
1. **按版本和 n 取 (key, base)**（完整分段表；各版本第一段为有符号比较，其后为无符号比较）：

| 版本 | n 范围 | key | base |
|---|---|---|---|
| < 0x11 | — | 0x528 | 0x41（'A'） |
| 0x11 | n ≤ 0x63 | 0x9D2 + n | 0xDF |
|  | n > 0x63 | 0x9D2 | 0xC6 |
| 0x12 | n ≤ 4 | 10n + 0x72 | 8n + 0x76 |
|  | ≤ 9 | 11n + 0x93 | 8n + 0xDA |
|  | ≤ 0xE | 13n + 0x13A | 8n + 0x13E |
|  | ≤ 0x13 | 15n + 0x9D | 8n + 0x1A2 |
|  | > 0x13 | 18n + 0xD6 | 3n + 0x206 |
| 0x13 | n ≤ 0x13 | 10n + 0x68 | 5n + 0xDA |
|  | ≤ 0x27 | 11n + 0x89 | 6n + 0x26A |
|  | ≤ 0x36 | 13n + 0x144 | 7n + 0x76 |
|  | ≤ 0x59 | 15n + 0x9D | 8n + 0xDA |
|  | > 0x59 | 8n + 0xDF | 3n + 0x13E |
| 0x14 | n ≤ 0x13 | 10n + 0x72 | 5n + 0xD0 |
|  | ≤ 0x27 | 11n + 0x25 | 6n + 0xDA |
|  | ≤ 0x36 | 13n + 0xE0 | 7n + 0x6C |
|  | ≤ 0x59 | 15n + 0x101 | 8n + 0x13E |
|  | > 0x59 | 8n + 0x7B | 3n + 0x76 |
| > 0x14 | n ≤ 0x13 | 10n + 0x6E | 5n + 0xCB |
|  | ≤ 0x27 | 11n + 0x27 | 6n + 0xD0 |
|  | ≤ 0x36 | 13n + 0xCC | 7n + 0x76 |
|  | ≤ 0x59 | 15n + 0xE3 | 8n + 0x134 |
|  | > 0x59 | 8n + 0x71 | 3n + 0x80 |

2. **两两解码**：`v = ((c1 & 0xFF) − base) × 26 + (c2 & 0xFF) − base`（取 16 位），每对字母 append 一个宽字符。
3. **异或**：每个宽字符**只保留最低字节**，与 **`(key >> 8) & 0xFF`** 异或后整字写回（默认 key 0x528 时为 0x05）——每对字母产出 1 个字节。
4. **后处理（版本 ≥ 0x11 且长度 ≥ 3，`0x100554774` 起）**——不是“反转并去首尾”：
   - 0x11：不反转；`erase(0,1)`、`erase(1,1)`
   - 0x12 / 0x13 且长度 ≥ 4：不反转；`erase(0,1)` 两次、再 `erase(1,1)`
   - 0x12 / 0x13 且长度 = 3，或版本 ≥ 0x14：整串反转后 `erase(0,1)`、`erase(1,1)`
5. 得到的字节串按 GBK/GB18030 解码为中文。

### 6.2 落地情况
- `tangent.py`：`tianzheng_key` 实现上表全部分支；`tangent_decrypt_bytes` / `tangent_unencrypt_string` 实现第 2–5 步；单测 `tests/test_tangent.py`。
- `safe_decode_tangent_if_cipher` 只在密文结构（默认分支每对 v ≤ 0xFF）与明文编码（GB2312 双字节区、含汉字）都对得上时才采纳，WDZN、FIRE 等普通大写标注保持原样（随机大写串误判率约 0.7%）。
- 接入点：`native_takeoff.py` 对天正代理对象（类名 TDb… / TCH_ / TEL_）的代理图形文字、天正块（`$EQUIP$…` 等）的块内文字与属性值自动解密，经 `supplement_cad_texts` 进入 cad_texts；解密条数写入读取警告。
- 局限：代理图形里拿不到对象自带的版本与 n，统一按默认分支解；高版本加密字段要等读取 `dwgInFields` 原始数据后才能用。仍需真实天正电气图纸回归。

---

## 7. 合规提示
- 快速看图的 DWG 能力来自 ODA 商业 SDK，我们**不能**直接复用其二进制、`adinit.dat` 或 `xfont.jff`。
- 本报告提取的是“它支持什么、怎么做”的算法思路与文件格式知识（如天正字段解密），用于我们自己的实现与图纸互通；实现时用自有代码，DWG 读取继续用我们已集成的引擎（`integrations/acadrust`、`cadcraft`、ezdxf），或另行获得 ODA 授权。

---

## 8. 建议的下一步
1. `tangent.py` 已修正并接入 native_takeoff（见 6.2），剩余：配真实天正电气样图回归、读取 dwgInFields 拿到对象的版本与 n。
2. 字体：建字体仓库 + 映射 + 逐字兜底 + 替换清单；文字包围框按实际字体度量。
3. 实体覆盖清单：对照 4.1 的 37 类标准对象，统计我们渲染/提取管线未处理的类型；代理对象一律炸开代理图形。
4. 天正电气优先：`TDbWireDimNew / TDbEquipDimNew / TDbCableDim / TDbEEquipment / TDbSheet / TDbText / TDbMText(New)` 字段读取与文字解密。
5. 设备识别计数（几何相似匹配）作为拆量的新能力评估立项。
