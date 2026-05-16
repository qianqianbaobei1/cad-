#!/usr/bin/env python3
"""
为T3安装物料库补充规格模式JSON。
基于物料类型、国标代号、行业知识生成结构化的规格参数定义。
"""
from __future__ import annotations
import csv, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_CSV = ROOT / "标准知识库" / "T3_安装_标准物料库.csv"
DST_CSV = ROOT / "标准知识库" / "T3_安装_标准物料库.csv"
BACKUP_CSV = ROOT / "标准知识库" / "T3_安装_标准物料库_backup.csv"

# ═══════════════════════════════════════
# 规格模式定义 — 按材料类型分组
# 每组的 key 是标准名称关键词匹配，value 是规格参数列表
# ═══════════════════════════════════════

def make_spec(param, ptype="enum", required=True, standard="", clause="", default="", unit="", values=None, linked_params=None):
    """构建单个规格参数"""
    s = {
        "param": param,
        "type": ptype,
        "required": required,
        "source_standard": standard,
        "source_clause": clause,
    }
    if default:
        s["default"] = default
    if unit:
        s["unit"] = unit
    if values:
        s["values"] = values
    if linked_params:
        s["linked_params"] = linked_params
    return s

# ═══════════════════════════
# 管材管件 (11条)
# ═══════════════════════════
PIPE_SPECS = {
    "无缝钢管 20#": [
        make_spec("外径", "enum", True, "GB/T 8163-2018", "GB8163-表1", default="DN100", unit="mm", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200","DN250","DN300","DN350","DN400"]),
        make_spec("壁厚", "enum", True, "GB/T 8163-2018", "GB8163-表1", default="4.0", unit="mm", values=["2.5","3.0","3.5","4.0","4.5","5.0","5.5","6.0","6.5","7.0","8.0","10.0","12.0"]),
        make_spec("材质", "enum", True, "GB/T 8163-2018", "GB8163-4.1", default="20#", values=["10","20","Q345B","Q345C","Q345D","Q345E","16Mn","20#"]),
        make_spec("制造工艺", "enum", False, "GB/T 8163-2018", "GB8163-5.1", values=["热轧","冷拔","热扩"]),
        make_spec("连接方式", "enum", False, "", "", values=["焊接","法兰连接","螺纹连接","沟槽连接"]),
        make_spec("压力等级", "text", False, "", "", unit="MPa", default="PN16"),
    ],
    "镀锌钢管 DN15~DN150": [
        make_spec("公称直径", "enum", True, "GB/T 3091-2025", "GB3091-4.1", default="DN50", unit="mm", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200"]),
        make_spec("壁厚", "enum", True, "GB/T 3091-2025", "GB3091-表1", unit="mm", default="3.8", values=["2.8","3.2","3.5","3.8","4.0","4.5","5.0","6.0"]),
        make_spec("镀锌方式", "enum", True, "GB/T 3091-2025", "GB3091-5.2", default="热浸镀锌", values=["热浸镀锌","冷镀锌","预镀锌"]),
        make_spec("连接方式", "enum", True, "", "", values=["螺纹连接","沟槽连接","法兰连接","焊接"]),
        make_spec("锌层重量", "range", False, "GB/T 3091-2025", "GB3091-5.2.1", unit="g/m²", default="300"),
    ],
    "塑料管 PPR/UPVC": [
        make_spec("管材类型", "enum", True, "", "", default="PPR", values=["PPR","UPVC","PE-RT","PE-X","PE","PB","CPVC","ABS"]),
        make_spec("公称外径", "enum", True, "GB/T 18742.2-2017", "", unit="mm", default="dn25", values=["dn16","dn20","dn25","dn32","dn40","dn50","dn63","dn75","dn90","dn110","dn160"]),
        make_spec("公称压力", "enum", True, "GB/T 18742.2-2017", "", unit="MPa", default="PN1.6", values=["PN1.0","PN1.25","PN1.6","PN2.0","PN2.5"]),
        make_spec("壁厚系列", "enum", False, "GB/T 18742.2-2017", "", default="S4", values=["S2.5","S3.2","S4","S5","S6.3"]),
        make_spec("连接方式", "enum", True, "", "", default="热熔连接", values=["热熔连接","电熔连接","承插连接","法兰连接","螺纹连接"]),
        make_spec("适用范围", "enum", False, "", "", values=["给水","排水","冷热水","化工"]),
    ],
    "铸铁排水管": [
        make_spec("公称直径", "enum", True, "", "", unit="mm", default="DN100", values=["DN50","DN75","DN100","DN150","DN200","DN250"]),
        make_spec("接口形式", "enum", True, "", "", default="柔性接口", values=["柔性接口","刚性接口","法兰接口","承插接口"]),
        make_spec("管壁厚度", "text", False, "", "", unit="mm", default="按国标"),
        make_spec("防腐处理", "enum", False, "", "", default="环氧涂层", values=["环氧涂层","煤焦油","镀锌","无"]),
    ],
    "不锈钢管": [
        make_spec("材质牌号", "enum", True, "GB/T 12771-2019", "GB12771-4.1", default="304(06Cr19Ni10)", values=["304(06Cr19Ni10)","304L","316(06Cr17Ni12Mo2)","316L","321","310S","2205","904L"]),
        make_spec("外径", "enum", True, "GB/T 12771-2019", "GB12771-表1", unit="mm", default="DN50", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100"]),
        make_spec("壁厚", "enum", True, "GB/T 12771-2019", "GB12771-表1", unit="mm", default="2.0", values=["1.0","1.5","2.0","2.5","3.0","3.5","4.0","5.0"]),
        make_spec("连接方式", "enum", True, "", "", default="焊接", values=["焊接","法兰连接","卡压式连接","沟槽连接"]),
        make_spec("表面处理", "enum", False, "", "", default="酸洗钝化", values=["酸洗钝化","抛光","拉丝","光亮退火"]),
    ],
    "铜管": [
        make_spec("材质", "enum", True, "", "", default="紫铜", values=["紫铜(TP2)","黄铜(H62)","黄铜(H65)","黄铜(H68)"]),
        make_spec("外径", "enum", True, "", "", unit="mm", default="15.88", values=["6.35","9.52","12.7","15.88","19.05","22.23","25.4","28.58","31.75","38.1","50.8"]),
        make_spec("壁厚", "number", True, "", "", unit="mm", default="1.0"),
        make_spec("状态", "enum", False, "", "", default="硬(Y)", values=["硬(Y)","半硬(Y2)","软(M)"]),
        make_spec("连接方式", "enum", True, "", "", values=["钎焊","卡套连接","扩口连接","法兰连接"]),
    ],
    "碳钢弯头(冲压/无缝)": [
        make_spec("公称直径", "enum", True, "GB/T 12459-2017", "GB12459-表1", default="DN100", unit="mm", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200","DN250","DN300"]),
        make_spec("角度", "enum", True, "", "", default="90°", values=["45°","90°","180°"]),
        make_spec("弯曲半径", "enum", False, "GB/T 12459-2017", "", default="1.5D", values=["1.0D","1.5D","3.0D"]),
        make_spec("壁厚系列", "enum", True, "", "", default="SCH40", values=["SCH20","SCH40","SCH80","SCH160","XS","XXS"]),
        make_spec("材质", "enum", True, "", "", default="20#", values=["20#","Q235B","Q345B","16Mn","304","316L","15CrMo"]),
        make_spec("制造工艺", "enum", False, "", "", default="无缝", values=["无缝","直缝焊接","冲压"]),
        make_spec("连接方式", "enum", False, "", "", values=["对焊","承插焊","螺纹"]),
    ],
    "沟槽管件(卡箍/弯头/三通)": [
        make_spec("类型", "enum", True, "", "", default="卡箍", values=["卡箍","弯头","三通","四通","异径管","法兰接头","堵头","机械三通"]),
        make_spec("公称直径", "enum", True, "", "", unit="mm", default="DN100", values=["DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200","DN250","DN300"]),
        make_spec("材质", "enum", True, "", "", default="球墨铸铁", values=["球墨铸铁","碳钢","不锈钢304","不锈钢316"]),
        make_spec("密封圈材质", "enum", False, "", "", default="EPDM", values=["EPDM(三元乙丙)","丁腈橡胶(NBR)","硅橡胶","氟橡胶"]),
        make_spec("表面处理", "enum", False, "", "", default="热镀锌", values=["热镀锌","环氧涂层","镀锌+涂层"]),
    ],
    "碳钢法兰(平焊/对焊)": [
        make_spec("公称直径", "enum", True, "GB/T 9115-2010", "", unit="mm", default="DN100", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200","DN250","DN300"]),
        make_spec("公称压力", "enum", True, "GB/T 9115-2010", "", unit="MPa", default="PN16", values=["PN6","PN10","PN16","PN25","PN40","PN63","PN100","PN160"]),
        make_spec("型式", "enum", True, "", "", default="平焊", values=["平焊(SO)","对焊(WN)","松套","螺纹","盲板(BL)"]),
        make_spec("密封面", "enum", True, "", "", default="突面(RF)", values=["突面(RF)","全平面(FF)","凹凸面(MFM)","榫槽面(TG)","环连接面(RJ)"]),
        make_spec("材质", "enum", True, "", "", default="20#", values=["20#","Q235B","16Mn","304","316L","321"]),
        make_spec("标准系列", "enum", False, "GB/T 9115-2010", "", default="GB/T", values=["GB/T","HG/T","JB/T","SH","ASME B16.5"]),
    ],
    "管道支架(型钢制)": [
        make_spec("支架类型", "enum", True, "", "", default="固定支架", values=["固定支架","滑动支架","导向支架","吊架","弹簧支架","管托"]),
        make_spec("材质", "enum", True, "", "", default="Q235B", values=["Q235B","Q345B","304","20#"]),
        make_spec("型钢规格", "enum", True, "", "", values=["角钢L50x5","角钢L63x6","角钢L75x7","槽钢[10","槽钢[12","槽钢[14","工字钢I10","工字钢I14"]),
        make_spec("表面处理", "enum", False, "", "", default="热镀锌", values=["热镀锌","刷漆","不锈钢"]),
        make_spec("荷载等级", "text", False, "", ""),
    ],
    "补偿器(波纹管/套筒)": [
        make_spec("类型", "enum", True, "", "", default="波纹管补偿器", values=["波纹管补偿器(轴向)","波纹管补偿器(横向)","波纹管补偿器(角向)","套筒补偿器","球形补偿器","非金属补偿器(织物)"]),
        make_spec("公称直径", "enum", True, "", "", unit="mm", default="DN100", values=["DN25","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200","DN250","DN300","DN350","DN400","DN500","DN600"]),
        make_spec("公称压力", "enum", True, "", "", unit="MPa", default="PN16", values=["PN6","PN10","PN16","PN25","PN40"]),
        make_spec("波数/补偿量", "text", True, "", "", unit="mm"),
        make_spec("材质", "enum", False, "", "", default="304", values=["304","316L","321","254SMO","Inconel625"]),
        make_spec("连接方式", "enum", False, "", "", values=["法兰连接","焊接"]),
    ],
}

# ═══════════════════════════
# 阀门 (8条)
# ═══════════════════════════
VALVE_SPECS = {
    "闸阀(铸铁/铸钢)": [
        make_spec("公称直径", "enum", True, "GB/T 12232-2025", "", unit="mm", default="DN100", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200","DN250","DN300","DN350","DN400","DN500","DN600"]),
        make_spec("公称压力", "enum", True, "GB/T 12232-2025", "", unit="MPa", default="PN16", values=["PN10","PN16","PN25","PN40","PN63","PN100","PN160"]),
        make_spec("阀体材质", "enum", True, "", "", default="铸铁", values=["铸铁","球墨铸铁","铸钢","不锈钢304","不锈钢316"]),
        make_spec("连接方式", "enum", True, "", "", default="法兰连接", values=["法兰连接","螺纹连接","焊接"]),
        make_spec("驱动方式", "enum", False, "", "", default="手动", values=["手动","电动","气动","液动","齿轮传动"]),
        make_spec("密封面材质", "enum", False, "", "", default="铜合金", values=["铜合金","不锈钢","橡胶","聚四氟乙烯","硬质合金"]),
    ],
    "蝶阀(对夹/法兰)": [
        make_spec("公称直径", "enum", True, "GB/T 12238-2008", "", unit="mm", default="DN100", values=["DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200","DN250","DN300","DN350","DN400","DN500","DN600","DN800","DN1000"]),
        make_spec("公称压力", "enum", True, "GB/T 12238-2008", "", unit="MPa", default="PN16", values=["PN10","PN16","PN25","PN40"]),
        make_spec("阀体材质", "enum", True, "", "", default="球墨铸铁", values=["球墨铸铁","铸钢","不锈钢304","不锈钢316","双相不锈钢"]),
        make_spec("连接方式", "enum", True, "", "", default="对夹", values=["对夹","法兰","凸耳"]),
        make_spec("驱动方式", "enum", False, "", "", default="手动(手柄)", values=["手动(手柄)","手动(蜗轮)","电动","气动"]),
        make_spec("阀板材质", "enum", False, "", "", default="304", values=["304","316L","2507双相钢","尼龙包覆","铸铁镀镍"]),
        make_spec("密封材质", "enum", False, "", "", default="EPDM", values=["EPDM","丁腈橡胶","聚四氟乙烯(PTFE)","金属硬密封"]),
    ],
    "截止阀": [
        make_spec("公称直径", "enum", True, "GB/T 12235-2025", "", unit="mm", default="DN50", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200"]),
        make_spec("公称压力", "enum", True, "GB/T 12235-2025", "", unit="MPa", default="PN16", values=["PN16","PN25","PN40","PN63","PN100","PN160"]),
        make_spec("阀体材质", "enum", True, "", "", default="铸钢", values=["铸铁","球墨铸铁","铸钢","锻钢","不锈钢304","不锈钢316"]),
        make_spec("连接方式", "enum", False, "", "", default="法兰连接", values=["法兰连接","螺纹连接","焊接"]),
        make_spec("密封面材质", "enum", False, "", "", default="不锈钢", values=["铜合金","不锈钢","司太立合金","聚四氟乙烯"]),
        make_spec("结构形式", "enum", False, "", "", default="直通式", values=["直通式","角式","Y型","三通"]),
    ],
    "止回阀": [
        make_spec("公称直径", "enum", True, "GB/T 12236-2025", "", unit="mm", default="DN100", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200","DN250","DN300"]),
        make_spec("公称压力", "enum", True, "GB/T 12236-2025", "", unit="MPa", default="PN16", values=["PN10","PN16","PN25","PN40","PN63","PN100"]),
        make_spec("结构形式", "enum", True, "", "", default="旋启式", values=["升降式","旋启式","双瓣式","轴流式","缓闭式","球形"]),
        make_spec("阀体材质", "enum", True, "", "", default="铸铁", values=["铸铁","球墨铸铁","铸钢","不锈钢304","不锈钢316"]),
        make_spec("连接方式", "enum", False, "", "", default="法兰连接", values=["法兰连接","螺纹连接","对夹","焊接"]),
    ],
    "球阀": [
        make_spec("公称直径", "enum", True, "GB/T 12237-2021", "", unit="mm", default="DN50", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200"]),
        make_spec("公称压力", "enum", True, "GB/T 12237-2021", "", unit="MPa", default="PN16", values=["PN10","PN16","PN25","PN40","PN63","PN100"]),
        make_spec("阀体材质", "enum", True, "", "", default="不锈钢304", values=["碳钢","不锈钢304","不锈钢316L","黄铜","UPVC"]),
        make_spec("连接方式", "enum", False, "", "", default="法兰连接", values=["法兰连接","螺纹连接","焊接","卡套"]),
        make_spec("结构形式", "enum", False, "", "", default="浮动球", values=["浮动球","固定球","V型"]),
        make_spec("密封材质", "enum", False, "", "", default="PTFE", values=["聚四氟乙烯(PTFE)","增强PTFE","PEEK","金属硬密封"]),
        make_spec("驱动方式", "enum", False, "", "", default="手动(手柄)", values=["手动(手柄)","蜗轮","电动","气动"]),
    ],
    "安全阀": [
        make_spec("公称直径", "enum", True, "", "", unit="mm", default="DN50", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN150","DN200"]),
        make_spec("公称压力", "enum", True, "", "", unit="MPa", default="PN16", values=["PN16","PN25","PN40","PN63","PN100","PN160","PN250"]),
        make_spec("整定压力", "text", True, "", "", unit="MPa", default="按设计要求"),
        make_spec("结构形式", "enum", True, "", "", default="弹簧式", values=["弹簧式","先导式","杠杆重锤式","波纹管式"]),
        make_spec("阀体材质", "enum", True, "", "", default="铸钢", values=["铸铁","球墨铸铁","铸钢","不锈钢304","不锈钢316"]),
        make_spec("连接方式", "enum", False, "", "", default="法兰连接", values=["法兰连接","螺纹连接","焊接"]),
        make_spec("密封面材质", "enum", False, "", "", default="不锈钢", values=["铜合金","不锈钢","司太立合金","聚四氟乙烯"]),
    ],
    "调节阀": [
        make_spec("公称直径", "enum", True, "", "", unit="mm", default="DN50", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200"]),
        make_spec("公称压力", "enum", True, "", "", unit="MPa", default="PN16", values=["PN10","PN16","PN25","PN40","PN63","PN100"]),
        make_spec("驱动方式", "enum", True, "", "", default="电动", values=["电动","气动","液动","自力式"]),
        make_spec("阀体材质", "enum", True, "", "", default="铸钢", values=["铸铁","铸钢","不锈钢304","不锈钢316L"]),
        make_spec("流量特性", "enum", False, "", "", default="等百分比", values=["线性","等百分比","快开","抛物线"]),
        make_spec("输入信号", "enum", False, "", "", default="4-20mA", values=["4-20mA","0-10V","开关量","HART协议","PROFIBUS","FF"]),
        make_spec("阀芯材质", "enum", False, "", "", default="304", values=["304","316L","440C(硬化)","司太立合金","陶瓷"]),
        make_spec("连接方式", "enum", False, "", "", default="法兰连接", values=["法兰连接","螺纹连接","焊接"]),
    ],
    "减压阀": [
        make_spec("公称直径", "enum", True, "GB/T 12244-2025", "", unit="mm", default="DN50", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200"]),
        make_spec("公称压力", "enum", True, "GB/T 12244-2025", "", unit="MPa", default="PN16", values=["PN10","PN16","PN25","PN40"]),
        make_spec("结构形式", "enum", True, "", "", default="先导活塞式", values=["膜片式","先导活塞式","波纹管式","比例式"]),
        make_spec("阀体材质", "enum", True, "", "", default="铸铁", values=["铸铁","球墨铸铁","铸钢","不锈钢304","铜"]),
        make_spec("进口压力范围", "text", True, "", "", unit="MPa", default="按设计"),
        make_spec("出口压力范围", "text", True, "", "", unit="MPa", default="按设计"),
        make_spec("连接方式", "enum", False, "", "", default="法兰连接", values=["法兰连接","螺纹连接"]),
    ],
}

# ═══════════════════════════
# 电缆电线 (9条)
# ═══════════════════════════
CABLE_SPECS = {
    "电力电缆 YJV": [
        make_spec("额定电压", "enum", True, "GB/T 12706.3-2020", "GB12706-表1", default="0.6/1kV", values=["0.6/1kV","3.6/6kV","6/10kV","8.7/15kV","12/20kV","18/30kV","21/35kV","26/35kV"]),
        make_spec("截面", "enum", True, "GB/T 12706.3-2020", "", unit="mm²", default="70", values=["1.5","2.5","4","6","10","16","25","35","50","70","95","120","150","185","240","300","400","500","630","800","1000"]),
        make_spec("芯数", "enum", True, "", "", default="4", values=["1","2","3","4","5","3+1","3+2","4+1"]),
        make_spec("导体材质", "enum", True, "", "", default="铜芯", values=["铜芯(T)","铝芯(L)"]),
        make_spec("绝缘材料", "enum", False, "", "", default="交联聚乙烯(XLPE)", values=["交联聚乙烯(XLPE)","乙丙橡胶(EPR)"]),
        make_spec("护套材质", "enum", False, "", "", default="聚氯乙烯(PVC)", values=["聚氯乙烯(PVC)","聚乙烯(PE)","低烟无卤(LSZH)"]),
        make_spec("铠装形式", "enum", False, "", "", default="无铠装", values=["无铠装","钢带铠装(22)","钢丝铠装(32)","非磁性铠装"]),
    ],
    "电力电缆 YJV22(铠装)": [
        make_spec("额定电压", "enum", True, "GB/T 12706.3-2020", "", default="0.6/1kV", values=["0.6/1kV","3.6/6kV","6/10kV","8.7/15kV","12/20kV"]),
        make_spec("截面", "enum", True, "GB/T 12706.3-2020", "", unit="mm²", default="95", values=["4","6","10","16","25","35","50","70","95","120","150","185","240","300","400"]),
        make_spec("芯数", "enum", True, "", "", default="4", values=["2","3","4","5","3+1","3+2","4+1"]),
        make_spec("导体材质", "enum", True, "", "", default="铜芯", values=["铜芯(T)","铝芯(L)"]),
        make_spec("铠装层", "enum", False, "", "", default="双层钢带间隙铠装", values=["双层钢带间隙铠装","细钢丝铠装","粗钢丝铠装"]),
        make_spec("外护套", "enum", False, "", "", default="聚氯乙烯", values=["聚氯乙烯(PVC)","聚乙烯(PE)","低烟无卤(LSZH)"]),
    ],
    "控制电缆 KVV": [
        make_spec("额定电压", "enum", True, "GB/T 9330-2020", "", default="450/750V", values=["300/500V","450/750V"]),
        make_spec("截面", "enum", True, "", "", unit="mm²", default="1.5", values=["0.75","1.0","1.5","2.5","4","6","10"]),
        make_spec("芯数", "enum", True, "", "", default="7", values=["2","3","4","5","7","8","10","12","14","16","19","24","30","37"]),
        make_spec("屏蔽形式", "enum", False, "", "", default="无屏蔽", values=["无屏蔽","铜带屏蔽","铜丝编织屏蔽","铝塑复合带屏蔽"]),
        make_spec("护套材质", "enum", False, "", "", default="聚氯乙烯(PVC)", values=["聚氯乙烯(PVC)","聚乙烯(PE)","低烟无卤(LSZH)"]),
    ],
    "电线 BV": [
        make_spec("额定电压", "enum", True, "GB/T 5023-2008", "", default="450/750V", values=["300/500V","450/750V"]),
        make_spec("截面", "enum", True, "GB/T 5023-2008", "", unit="mm²", default="2.5", values=["1.0","1.5","2.5","4","6","10","16","25","35","50","70","95","120","150","185","240"]),
        make_spec("导体结构", "enum", True, "", "", default="单芯硬导体", values=["单芯硬导体(BV)","单芯软导体(BVR)"]),
        make_spec("导体材质", "enum", False, "", "", default="铜", values=["铜","铝(BLV)"]),
        make_spec("颜色", "enum", False, "", "", values=["红","黄","绿","蓝","黄绿双色","黑","白"]),
    ],
    "电线 NH-BV(耐火)": [
        make_spec("额定电压", "enum", True, "GB/T 5023-2008", "", default="450/750V", values=["300/500V","450/750V"]),
        make_spec("截面", "enum", True, "", "", unit="mm²", default="2.5", values=["1.0","1.5","2.5","4","6","10","16","25","35","50"]),
        make_spec("耐火等级", "enum", True, "GB/T 19666-2019", "", default="NH-BV", values=["NH-BV(B类)","NH-A-BV(A类)","NH-C-BV(C类)"]),
        make_spec("耐火温度", "enum", False, "", "", default="750-800℃", values=["750-800℃","950-1000℃"]),
        make_spec("导体材质", "enum", False, "", "", default="铜", values=["铜","铝"]),
    ],
    "封闭母线槽": [
        make_spec("额定电流", "enum", True, "", "", unit="A", default="1250", values=["100","160","200","250","315","400","500","630","800","1000","1250","1600","2000","2500","3150","4000","5000","6300"]),
        make_spec("额定电压", "enum", True, "", "", default="0.4kV", values=["0.4kV","0.69kV","1kV以下"]),
        make_spec("导体材质", "enum", True, "", "", default="铜排", values=["铜排","铝排"]),
        make_spec("结构形式", "enum", False, "", "", default="密集绝缘型", values=["密集绝缘型","空气绝缘型","浇注型"]),
        make_spec("防护等级", "enum", False, "", "", default="IP54", values=["IP40","IP54","IP65","IP66"]),
        make_spec("相数", "enum", True, "", "", default="3P+N+PE", values=["3P+N","3P+N+PE","4P","5P"]),
    ],
    "阻燃电缆 ZR-YJV": [
        make_spec("额定电压", "enum", True, "GB/T 19666-2019", "", default="0.6/1kV", values=["0.6/1kV","3.6/6kV"]),
        make_spec("截面", "enum", True, "", "", unit="mm²", default="70", values=["1.5","2.5","4","6","10","16","25","35","50","70","95","120","150","185","240","300"]),
        make_spec("阻燃等级", "enum", True, "GB/T 19666-2019", "", default="ZR-C", values=["ZR-A(A类)","ZR-B(B类)","ZR-C(C类)","ZR-D(D类)"]),
        make_spec("芯数", "enum", False, "", "", default="4", values=["2","3","4","5"]),
        make_spec("外护套", "enum", False, "", "", default="聚氯乙烯", values=["聚氯乙烯(PVC)","低烟无卤(LSZH)"]),
    ],
    "耐火电缆 NH-YJV": [
        make_spec("额定电压", "enum", True, "GB/T 19666-2019", "", default="0.6/1kV", values=["0.6/1kV","3.6/6kV"]),
        make_spec("截面", "enum", True, "", "", unit="mm²", default="70", values=["1.5","2.5","4","6","10","16","25","35","50","70","95","120","150","185","240"]),
        make_spec("耐火等级", "enum", True, "GB/T 19666-2019", "", default="N(B类)", values=["N(A类)","N(B类)","N(C类)"]),
        make_spec("芯数", "enum", False, "", "", default="4", values=["2","3","4","5"]),
        make_spec("耐火温度", "text", False, "", "", default="750-800℃/90min"),
    ],
    "通信光缆/网线": [
        make_spec("类型", "enum", True, "YD/T 901-2018", "", default="单模光缆", values=["单模光缆","多模光缆","超五类网线","六类网线","超六类网线","七类网线","同轴电缆"]),
        make_spec("芯数/对数", "enum", False, "", "", default="4对(网线)", values=["2芯","4芯","6芯","8芯","12芯","24芯","48芯","4对(网线)"]),
        make_spec("铠装形式", "enum", False, "", "", values=["无铠装","钢带铠装","钢丝铠装","不锈钢管"]),
        make_spec("护套材质", "enum", False, "", "", default="LSZH", values=["PE","PVC","LSZH(低烟无卤)","阻燃PVC"]),
        make_spec("光缆结构", "enum", False, "", "", default="层绞式", values=["层绞式","中心束管式","骨架式","ADSS(自承式)"]),
        make_spec("屏蔽形式", "enum", False, "", "", values=["非屏蔽(UTP)","屏蔽(FTP)","双屏蔽(STP)"]),
    ],
}

# ═══════════════════════════
# 电气设备 (6条)
# ═══════════════════════════
ELEC_SPECS = {
    "配电箱/柜(照明/动力)": [
        make_spec("类型", "enum", True, "", "", default="照明配电箱", values=["照明配电箱(AL)","动力配电箱(AP)","双电源切换箱(AT)","消防配电箱(ALE)","应急配电箱"]),
        make_spec("箱体材质", "enum", True, "", "", default="冷轧钢板", values=["冷轧钢板","不锈钢304","玻璃钢(户外)"]),
        make_spec("安装方式", "enum", True, "", "", default="嵌墙暗装", values=["嵌墙暗装","挂墙明装","落地安装","户外杆上"]),
        make_spec("防护等级", "enum", False, "", "", default="IP30(户内)", values=["IP30(户内)","IP54(户外)","IP65(防水)","IP66(防尘+防水)"]),
        make_spec("额定电压", "enum", True, "", "", default="380/220V", values=["380/220V","220V","660V"]),
        make_spec("额定电流(主母线)", "enum", False, "", "", unit="A", default="100", values=["63","100","160","200","250","315","400","630","800","1000","1250","1600","2000"]),
    ],
    "控制箱/柜": [
        make_spec("类型", "enum", True, "", "", default="PLC控制柜", values=["PLC控制柜","DCS控制柜","MCC(马达控制中心)","变频控制柜","软启动柜","仪表控制箱"]),
        make_spec("额定电压", "enum", True, "", "", default="380/220V", values=["220V","380V","660V"]),
        make_spec("箱体材质", "enum", True, "", "", default="冷轧钢板", values=["冷轧钢板","不锈钢304"]),
        make_spec("安装方式", "enum", False, "", "", default="落地", values=["落地","挂墙","操作台"]),
        make_spec("防护等级", "enum", True, "", "", default="IP44", values=["IP30","IP44","IP54","IP65"]),
        make_spec("控制方式", "enum", False, "", "", values=["就地控制","远程控制","就地+远程","自动"]),
    ],
    "开关柜(高压/低压)": [
        make_spec("类型", "enum", True, "", "", default="低压抽出式开关柜", values=["高压铠装移开式(KYN)","高压环网柜(HXGN)","高压固定式(XGN)","低压抽出式(GCS/MNS)","低压固定分隔式(GGD)"]),
        make_spec("额定电压", "enum", True, "", "", default="0.4kV", values=["0.4kV","0.69kV","6kV","10kV","20kV","35kV"]),
        make_spec("额定电流", "enum", True, "", "", unit="A", default="1250", values=["630","800","1000","1250","1600","2000","2500","3150","4000","5000","6300"]),
        make_spec("短路耐受电流", "enum", False, "", "", unit="kA", default="50kA", values=["25kA","31.5kA","40kA","50kA","63kA"]),
        make_spec("母线形式", "enum", False, "", "", default="单母线", values=["单母线","单母线分段","双母线"]),
        make_spec("防护等级", "enum", False, "", "", default="IP30", values=["IP30","IP40","IP42"]),
    ],
    "变压器(干式/油浸)": [
        make_spec("类型", "enum", True, "", "", default="干式变压器", values=["干式变压器(SCB)","油浸式变压器(S13)","非晶合金变压器(SH15)","有载调压变压器"]),
        make_spec("额定容量", "enum", True, "", "", unit="kVA", default="800", values=["100","160","200","250","315","400","500","630","800","1000","1250","1600","2000","2500","3150"]),
        make_spec("额定电压比", "enum", True, "", "", default="10/0.4kV", values=["35/0.4kV","20/0.4kV","10/0.4kV","6/0.4kV"]),
        make_spec("联结组别", "enum", True, "", "", default="Dyn11", values=["Dyn11","Yyn0"]),
        make_spec("冷却方式", "enum", False, "", "", default="AN(自冷)", values=["AN(自冷)","AF(风冷)","ONAN(油浸自冷)","ONAF(油浸风冷)"]),
        make_spec("绝缘等级", "enum", False, "", "", default="F级", values=["F级(155℃)","H级(180℃)","C级(220℃)"]),
        make_spec("阻抗电压", "enum", False, "", "", unit="%", default="6", values=["4","4.5","6","8","10"]),
    ],
    "仪表(温度/压力/流量/液位)": [
        make_spec("仪表类型", "enum", True, "", "", default="压力变送器", values=["温度计(双金属)","热电阻/热电偶","温度变送器","压力表","压力变送器","差压变送器","电磁流量计","涡街流量计","孔板流量计","超声波流量计","液位计(磁翻板)","液位变送器","雷达液位计","超声波液位计"]),
        make_spec("输出信号", "enum", True, "", "", default="4-20mA", values=["4-20mA","0-10V","HART协议","RS-485(MODBUS)","FF现场总线","PROFIBUS"]),
        make_spec("供电方式", "enum", False, "", "", default="24VDC", values=["24VDC","220VAC","电池供电","回路供电"]),
        make_spec("连接规格", "text", True, "", "", default="DN15"),
        make_spec("防爆等级", "enum", False, "", "", default="无", values=["无","Ex d(隔爆)","Ex i(本安)","Ex e(增安)","Ex n(无火花)"]),
        make_spec("防护等级", "enum", False, "", "", default="IP65", values=["IP54","IP65","IP66","IP67"]),
        make_spec("精度等级", "enum", False, "", "", default="0.5级", values=["0.1级","0.2级","0.5级","1.0级","1.5级"]),
    ],
    "执行器/电动头": [
        make_spec("驱动方式", "enum", True, "", "", default="电动", values=["电动","气动","液动","电液联动"]),
        make_spec("输出力矩/推力", "text", True, "", "", unit="Nm或N", default="按阀门选型"),
        make_spec("输入信号", "enum", True, "", "", default="4-20mA", values=["4-20mA","0-10V","开关量","PROFIBUS","HART"]),
        make_spec("供电", "enum", True, "", "", default="380VAC", values=["220VAC","380VAC","24VDC","48VDC"]),
        make_spec("行程类型", "enum", False, "", "", default="角行程", values=["角行程(90°)","直行程","多回转"]),
        make_spec("防爆等级", "enum", False, "", "", default="无", values=["无","Ex dⅡBT4","Ex dⅡCT6","Ex iaⅡCT6"]),
        make_spec("防护等级", "enum", False, "", "", default="IP67", values=["IP65","IP66","IP67","IP68"]),
        make_spec("环境温度", "text", False, "", "", default="-20℃~+60℃"),
    ],
}

# ═══════════════════════════
# 暖通设备 (6条)
# ═══════════════════════════
HVAC_SPECS = {
    "风机(轴流/离心/混流)": [
        make_spec("风机类型", "enum", True, "", "", default="离心风机", values=["轴流风机","离心风机(前向)","离心风机(后向)","混流风机","屋顶风机","消防排烟风机"]),
        make_spec("风量", "text", True, "", "", unit="m³/h", default="按设计值"),
        make_spec("风压", "text", True, "", "", unit="Pa", default="按设计值"),
        make_spec("功率", "text", True, "", "", unit="kW", default="按设计值"),
        make_spec("电源", "enum", True, "", "", default="380V/50Hz", values=["220V/50Hz","380V/50Hz","660V/50Hz"]),
        make_spec("传动方式", "enum", False, "", "", default="直联", values=["直联","皮带传动","联轴器传动"]),
        make_spec("安装方式", "enum", False, "", "", values=["落地安装","吊装","墙装","屋顶安装"]),
        make_spec("防爆/防腐等级", "enum", False, "", "", default="不防爆", values=["不防爆","Ex dⅡBT4","Ex dⅡCT4"]),
        make_spec("噪声限值", "text", False, "", "", unit="dB(A)", default="≤85"),
    ],
    "镀锌钢板风管": [
        make_spec("形状", "enum", True, "", "", default="矩形", values=["矩形","圆形","扁圆形"]),
        make_spec("板材厚度", "enum", True, "", "", unit="mm", default="0.75", values=["0.5","0.6","0.75","0.8","1.0","1.2","1.5","2.0"]),
        make_spec("镀锌层重量", "enum", False, "", "", unit="g/m²", default="Z275", values=["Z120","Z150","Z180","Z275"]),
        make_spec("连接方式", "enum", False, "", "", default="法兰连接", values=["共板法兰(TDF)","角钢法兰","承插连接","咬口连接"]),
        make_spec("保温要求", "enum", False, "", "", default="无保温", values=["无保温","离心玻璃棉保温","橡塑海绵保温","岩棉保温"]),
        make_spec("压力等级", "enum", False, "", "", default="低压(≤500Pa)", values=["低压(≤500Pa)","中压(500-1500Pa)","高压(>1500Pa)"]),
        make_spec("防火等级", "enum", False, "", "", default="不燃A级", values=["不燃A级"]),
    ],
    "风口(散流器/百叶)": [
        make_spec("风口类型", "enum", True, "", "", default="方形散流器", values=["方形散流器","圆形散流器","单层百叶","双层百叶","条缝风口","旋流风口","喷口","格栅风口"]),
        make_spec("材质", "enum", True, "", "", default="铝合金", values=["铝合金","不锈钢","碳钢喷塑"]),
        make_spec("风口尺寸", "text", True, "", "", unit="mm", default="按设计"),
        make_spec("带调节阀", "enum", False, "", "", default="否", values=["是","否"]),
        make_spec("表面处理", "enum", False, "", "", default="氧化", values=["氧化","喷涂","抛光"]),
    ],
    "风阀(调节阀/止回阀/防火阀)": [
        make_spec("阀类型", "enum", True, "", "", default="手动调节阀", values=["手动调节阀","电动调节阀","止回阀","防火阀(70℃)","排烟防火阀(280℃)","防烟防火阀","定风量阀","变风量阀"]),
        make_spec("阀体尺寸", "text", True, "", "", unit="mm", default="按风管尺寸"),
        make_spec("材质", "enum", True, "", "", default="镀锌钢板", values=["镀锌钢板","碳钢","不锈钢304"]),
        make_spec("控制信号", "enum", False, "", "", values=["无(手动)","4-20mA","0-10V","开关量","总线控制"]),
        make_spec("漏风率", "text", False, "", "", default="按国标(≤2%)"),
    ],
    "空调机组(新风/组合式)": [
        make_spec("类型", "enum", True, "", "", default="组合式空调机组", values=["组合式空调机组(AHU)","新风机组(PAU)","风机盘管(FCU)","吊顶式空调","精密空调"]),
        make_spec("风量", "text", True, "", "", unit="m³/h", default="按设计值"),
        make_spec("冷量", "text", False, "", "", unit="kW", default="按设计值"),
        make_spec("热量", "text", False, "", "", unit="kW", default="按设计值"),
        make_spec("机外余压", "text", False, "", "", unit="Pa", default="按设计值"),
        make_spec("电源", "enum", True, "", "", default="380V/3P/50Hz", values=["220V/50Hz","380V/3P/50Hz"]),
        make_spec("风机段数", "text", False, "", "", default="回风机+送风机段"),
        make_spec("功能段", "enum", False, "", "", default="过滤+表冷+加热+加湿+风机", values=["过滤+表冷+风机","过滤+表冷+加热+风机","过滤+表冷+加热+加湿+风机","过滤+表冷+加热+加湿+热回收+风机"]),
        make_spec("盘管排数", "enum", False, "", "", default="4排", values=["2排","4排","6排","8排"]),
        make_spec("过滤等级", "enum", False, "", "", default="G4", values=["G3","G4","F5","F6","F7","F8","F9(HEPA)"]),
    ],
    "消声器/静压箱": [
        make_spec("类型", "enum", True, "", "", default="阻性片式消声器", values=["阻性片式消声器","阻性折板消声器","微穿孔板消声器","消声弯头","静压箱","阻抗复合消声器"]),
        make_spec("接口尺寸", "text", True, "", "", unit="mm", default="按风管尺寸"),
        make_spec("有效长度", "text", True, "", "", unit="mm", default="1000"),
        make_spec("消声量", "text", False, "", "", unit="dB(A)", default="≥15"),
        make_spec("消声频带", "text", False, "", "", default="中高频"),
        make_spec("外壳材质", "enum", False, "", "", default="镀锌钢板", values=["镀锌钢板","碳钢","不锈钢"]),
        make_spec("填充材料", "enum", False, "", "", default="离心玻璃棉", values=["离心玻璃棉","岩棉","矿棉"]),
    ],
}

# ═══════════════════════════
# 其他类型
# ═══════════════════════════

FIRE_SPECS = {
    "消火栓箱(室内/室外)": [
        make_spec("类型", "enum", True, "", "", default="室内消火栓箱", values=["室内消火栓箱(甲型)","室内消火栓箱(乙型)","室内消火栓箱(丙型)","室外地上消火栓","室外地下消火栓","试验消火栓","减压稳压消火栓"]),
        make_spec("公称直径", "enum", True, "", "", default="DN65", values=["DN50","DN65","DN80"]),
        make_spec("箱体材质", "enum", False, "", "", default="冷轧钢板", values=["冷轧钢板","不锈钢","玻璃钢","铝合金"]),
        make_spec("安装方式", "enum", True, "", "", default="嵌墙暗装", values=["嵌墙暗装","挂墙明装","落地"]),
        make_spec("箱体尺寸", "text", False, "", "", unit="mm"),
    ],
    "自动喷淋喷头": [
        make_spec("喷头类型", "enum", True, "", "", default="闭式玻璃球喷头", values=["闭式玻璃球(标准响应)","闭式玻璃球(快速响应)","开式洒水喷头","水幕喷头","水雾喷头","早期抑制快速响应(ESFR)","扩大覆盖面积(EC)"]),
        make_spec("公称动作温度", "enum", True, "", "", default="68℃", values=["57℃","68℃","79℃","93℃","141℃","182℃"]),
        make_spec("K系数", "enum", True, "", "", default="80", values=["57","80","115","161","202","242","363"]),
        make_spec("连接螺纹", "enum", False, "", "", default="DN15", values=["DN15","DN20","DN25"]),
        make_spec("安装方式", "enum", True, "", "", default="下垂型", values=["下垂型","直立型","边墙型","水平边墙型","吊顶暗装(装饰盖)"]),
        make_spec("响应类别", "enum", False, "", "", default="标准响应", values=["标准响应(SRT)","快速响应(QR)","特殊响应"]),
    ],
    "感烟/感温探测器": [
        make_spec("探测器类型", "enum", True, "", "", default="点型光电感烟", values=["点型光电感烟","点型离子感烟","点型感温(定温)","点型感温(差定温)","线型感烟(红外光束)","线型感温电缆","吸气式感烟","复合型(烟+温)"]),
        make_spec("探测方式", "enum", True, "", "", default="点型", values=["点型","线型","吸气式"]),
        make_spec("输出方式", "enum", True, "", "", default="总线制(无源触点)", values=["总线制(无源触点)","多线制","继电器输出"]),
        make_spec("报警温度(感温)", "enum", False, "", "", default="58℃(定温)", values=["54℃","58℃(定温)","68℃","78℃","88℃"]),
        make_spec("防护等级", "enum", False, "", "", default="IP30(户内)", values=["IP30(户内)","IP66(户外/水雾)","防爆型(Ex)"]),
        make_spec("带底座", "boolean", False, "", "", default="是"),
    ],
    "手动报警按钮/声光报警器": [
        make_spec("类型", "enum", True, "", "", default="手动报警按钮", values=["手动报警按钮(不带电话)","手动报警按钮(带电话插孔)","消火栓按钮","声光报警器","手报+声光一体"]),
        make_spec("输出方式", "enum", True, "", "", default="总线制", values=["总线制(无源触点)","多线制"]),
        make_spec("外壳材质", "enum", False, "", "", default="ABS/PC", values=["ABS/PC","金属"]),
        make_spec("防护等级", "enum", False, "", "", default="IP30(户内)", values=["IP30(户内)","IP66(户外)","防爆型(Ex)"]),
        make_spec("安装方式", "enum", False, "", "", default="壁挂", values=["壁挂","立柱安装"]),
    ],
    "火灾报警控制器/模块": [
        make_spec("设备类型", "enum", True, "", "", default="火灾报警控制器(联动型)", values=["火灾报警控制器(联动型)","区域报警控制器","电气火灾监控设备","消防设备电源监控","防火门监控器","输入模块","输出模块","输入/输出模块","总线隔离器","楼层显示器"]),
        make_spec("回路数/容量", "text", True, "", "", default="按设计"),
        make_spec("总线制式", "enum", True, "", "", default="二总线", values=["二总线","四总线","CAN总线","RS-485","以太网"]),
        make_spec("安装方式", "enum", True, "", "", default="壁挂(小型)", values=["壁挂(小型)","立柜式(大型)","琴台"]),
        make_spec("备用电源", "enum", False, "", "", default="内置蓄电池(24V)", values=["无","内置蓄电池(12V)","内置蓄电池(24V)"]),
        make_spec("协议", "enum", False, "", "", default="国标GB4717", values=["国标GB4717","专用厂家协议"]),
        make_spec("联网接口", "enum", False, "", "", values=["无","RS-485","CAN","以太网","光纤","电话线"]),
    ],
    "气体灭火装置(柜式/管网)": [
        make_spec("灭火剂类型", "enum", True, "", "", default="七氟丙烷", values=["七氟丙烷(HFC-227ea)","IG541(混合气体)","IG100(氮气)","IG55(氩氮混合)","二氧化碳(CO2)","热气溶胶"]),
        make_spec("装置型式", "enum", True, "", "", default="柜式(预制)", values=["柜式(预制)","管网式","悬挂式","探火管式"]),
        make_spec("灭火剂充装量", "text", True, "", "", unit="kg", default="按设计"),
        make_spec("贮存压力", "enum", False, "", "", default="2.5MPa(20℃)", values=["1.6MPa","2.5MPa(20℃)","4.2MPa","5.6MPa","15MPa(IG541)"]),
        make_spec("保护区体积", "text", False, "", "", unit="m³", default="按设计"),
        make_spec("启动方式", "enum", False, "", "", default="自动+手动+机械应急", values=["自动","手动","自动+手动","自动+手动+机械应急"]),
    ],
    "灭火器(干粉/CO₂)": [
        make_spec("灭火器类型", "enum", True, "", "", default="手提式干粉灭火器", values=["手提式干粉(ABC)","手提式干粉(BC)","手提式CO2","推车式干粉","推车式CO2","手提式水基","手提式泡沫","悬挂式干粉"]),
        make_spec("充装量/规格", "enum", True, "", "", default="MFZ/ABC4(4kg)", values=["MFZ/ABC1(1kg)","MFZ/ABC2(2kg)","MFZ/ABC3(3kg)","MFZ/ABC4(4kg)","MFZ/ABC5(5kg)","MFZ/ABC8(8kg)","MFT/ABC35(35kg推车)","MT/3(3kg CO2)","MT/5(5kg CO2)","MT/7(7kg CO2)"]),
        make_spec("灭火级别", "enum", True, "", "", default="2A 55B(ABC4)", values=["1A 21B","2A 34B","2A 55B","3A 89B","4A 144B","34B(CO2)","55B(CO2)"]),
        make_spec("使用温度范围", "text", False, "", "", default="-20℃~+55℃"),
    ],
    "消防水带/水枪/接口": [
        make_spec("水带口径", "enum", True, "", "", default="DN65", values=["DN50","DN65","DN80"]),
        make_spec("水带长度", "enum", True, "", "", default="25m", values=["20m","25m","30m"]),
        make_spec("水带材质", "enum", True, "", "", default="有衬里(聚氨酯)", values=["有衬里(橡胶)","有衬里(聚氨酯)","无衬里(麻质)","橡塑"]),
        make_spec("水枪类型", "enum", False, "", "", default="直流开关水枪", values=["直流开关水枪","开花水枪","喷雾水枪","多功能水枪","无后坐力水枪"]),
        make_spec("接口型式", "enum", False, "", "", default="内扣式接口", values=["内扣式接口","卡式接口","螺纹接口"]),
    ],
}

PLUMBING_SPECS = {
    "陶瓷洗脸盆(立柱/台上)": [
        make_spec("安装方式", "enum", True, "", "", default="台下盆", values=["台上盆","台下盆","立柱盆","壁挂盆","半嵌盆"]),
        make_spec("材质", "enum", True, "", "", default="陶瓷", values=["陶瓷","人造石","天然大理石","不锈钢"]),
        make_spec("尺寸(长x宽)", "text", True, "", "", unit="mm", default="600x400"),
        make_spec("水龙头孔数", "enum", False, "", "", default="单孔", values=["单孔","三孔","无孔(墙出水)"]),
        make_spec("溢水孔", "enum", False, "", "", default="有", values=["有","无"]),
        make_spec("颜色", "enum", False, "", "", default="白色", values=["白色","骨色","黑金"]),
        make_spec("带配件", "enum", False, "", "", default="含下水器+排水管", values=["含下水器+排水管","仅盆体"]),
    ],
    "坐便器/蹲便器": [
        make_spec("类型", "enum", True, "", "", default="坐便器(连体)", values=["坐便器(连体)","坐便器(分体)","蹲便器(带存水弯)","蹲便器(不带存水弯)","壁挂坐便器(墙排)","智能坐便器"]),
        make_spec("冲水方式", "enum", True, "", "", default="虹吸式", values=["冲落式","虹吸式","漩涡虹吸式","虹吸喷射式"]),
        make_spec("排水方式", "enum", True, "", "", default="地排(下排水)", values=["地排(下排水)","墙排(后排)"]),
        make_spec("坑距", "enum", True, "", "", default="305mm", values=["305mm","400mm"]),
        make_spec("用水量", "enum", False, "", "", default="≤6L", values=["≤4L","≤6L","≤8L","双冲3/6L"]),
    ],
    "小便器(挂式/落地)": [
        make_spec("安装方式", "enum", True, "", "", default="挂式", values=["挂式(壁挂)","落地式"]),
        make_spec("冲水方式", "enum", False, "", "", default="感应式", values=["手动","感应式","延时自闭"]),
        make_spec("材质", "enum", False, "", "", default="陶瓷", values=["陶瓷","不锈钢"]),
        make_spec("带存水弯", "boolean", False, "", "", default="是"),
    ],
    "淋浴器(手持/花洒)": [
        make_spec("类型", "enum", True, "", "", default="手持花洒套装", values=["单功能手持花洒","多功能手持花洒","顶喷+手持套装","恒温淋浴柱","暗装淋浴器"]),
        make_spec("材质", "enum", False, "", "", default="ABS+不锈钢", values=["ABS塑料","不锈钢","铜镀铬"]),
        make_spec("管路连接", "enum", False, "", "", default="DN15螺纹", values=["DN15螺纹","DN20螺纹","暗装预埋件"]),
        make_spec("表面处理", "enum", False, "", "", default="镀铬", values=["镀铬","拉丝镍","烤漆白","烤漆黑"]),
    ],
    "地漏/存水弯": [
        make_spec("地漏类型", "enum", True, "", "", default="普通地漏", values=["普通地漏","防臭地漏(水封)","防臭地漏(机械)","洗衣机专用地漏","侧排地漏","线型地漏"]),
        make_spec("公称直径", "enum", True, "", "", default="DN50", values=["DN50","DN75","DN100","DN150"]),
        make_spec("材质", "enum", True, "", "", default="不锈钢", values=["不锈钢304","黄铜","铸铁","UPVC"]),
        make_spec("水封高度", "enum", False, "", "", default="≥50mm", values=["≥30mm","≥50mm","无(机械密封)"]),
    ],
    "水龙头/角阀": [
        make_spec("类型", "enum", True, "", "", default="陶瓷阀芯水龙头", values=["陶瓷阀芯水龙头","感应式水龙头","延时自闭水龙头","混合调温水龙头","角阀(冷水)","角阀(热水)"]),
        make_spec("安装方式", "enum", True, "", "", default="台面安装", values=["台面安装","墙面安装"]),
        make_spec("连接规格", "enum", False, "", "", default="DN15", values=["DN15","DN20"]),
        make_spec("材质", "enum", False, "", "", default="黄铜镀铬", values=["黄铜镀铬","不锈钢","锌合金"]),
    ],
    "卫生洁具配件(下水/软管)": [
        make_spec("配件类型", "enum", True, "", "", default="下水器", values=["下水器(弹跳)","下水器(翻盖)","排水管(不锈钢波纹)","排水管(PVC)","编织软管","角阀+软管套件"]),
        make_spec("连接规格", "enum", False, "", "", default="DN32", values=["DN32","DN40","DN15(软管)"]),
        make_spec("材质", "enum", False, "", "", default="不锈钢+黄铜", values=["不锈钢","黄铜","ABS工程塑料","纯铜"]),
    ],
}

STEEL_SPECS = {
    "等边角钢 Q235B": [
        make_spec("规格", "enum", True, "", "", default="L50x50x5", values=["L30x3","L40x4","L50x5","L63x6","L63x8","L75x6","L75x8","L80x8","L90x8","L100x8","L100x10","L125x10","L125x12","L140x12","L160x14","L180x16","L200x18","L200x20"]),
        make_spec("材质", "enum", True, "", "", default="Q235B", values=["Q235B","Q345B","Q345C","Q345D","Q420","304","316L","Q355B"]),
        make_spec("长度", "enum", False, "", "", default="6m", values=["6m","9m","12m"]),
        make_spec("表面处理", "enum", False, "", "", default="无(黑材)", values=["无(黑材)","热镀锌","刷漆","喷砂"]),
    ],
    "槽钢 Q235B": [
        make_spec("规格", "enum", True, "", "", default="[10", values=["[5","[6.3","[8","[10","[12","[12.6","[14a","[14b","[16a","[16b","[18a","[18b","[20a","[20b","[22a","[22b","[25a","[25b","[28a","[28b","[32a","[32b","[36a","[36b","[40a","[40b"]),
        make_spec("材质", "enum", True, "", "", default="Q235B", values=["Q235B","Q345B","Q355B"]),
        make_spec("长度", "enum", False, "", "", default="6m", values=["6m","9m","12m"]),
    ],
    "工字钢 Q235B": [
        make_spec("规格", "enum", True, "", "", default="I14", values=["I10","I12.6","I14","I16","I18","I20a","I20b","I22a","I22b","I25a","I25b","I28a","I28b","I32a","I32b","I36a","I36b","I40a","I40b","I45a","I45b"]),
        make_spec("材质", "enum", True, "", "", default="Q235B", values=["Q235B","Q345B","Q355B"]),
        make_spec("长度", "enum", False, "", "", default="6m", values=["6m","9m","12m"]),
    ],
    "热镀锌钢板/扁钢": [
        make_spec("类型", "enum", True, "", "", default="镀锌钢板(平板)", values=["镀锌钢板(平板)","镀锌扁钢","镀锌花纹钢板","镀锌卷板"]),
        make_spec("厚度", "enum", True, "", "", unit="mm", default="3.0", values=["0.5","0.8","1.0","1.2","1.5","2.0","2.5","3.0","4.0","5.0","6.0","8.0","10.0","12.0","16.0","20.0"]),
        make_spec("镀锌层", "enum", False, "", "", default="Z275", values=["Z120","Z180","Z275","Z350"]),
        make_spec("扁钢宽度", "enum", False, "", "", unit="mm", default="40", values=["25","30","40","50","60","80","100"]),
    ],
    "电缆桥架(钢制/镀锌)": [
        make_spec("桥架类型", "enum", True, "", "", default="槽式桥架", values=["槽式桥架","梯式桥架","托盘式桥架","大跨距桥架","防火桥架","玻璃钢桥架","不锈钢桥架","铝合金桥架"]),
        make_spec("规格(宽x高)", "enum", True, "", "", unit="mm", default="200x100", values=["50x50","100x50","100x75","150x75","200x100","200x150","300x100","300x150","400x100","400x150","400x200","500x150","500x200","600x150","600x200","800x150","800x200"]),
        make_spec("壁厚", "enum", False, "", "", unit="mm", default="1.5", values=["1.0","1.2","1.5","2.0","2.5","3.0"]),
        make_spec("表面处理", "enum", False, "", "", default="热镀锌", values=["热镀锌","电镀锌","喷塑","防火涂料","热浸锌","不锈钢"]),
        make_spec("带盖板", "boolean", False, "", "", default="是"),
        make_spec("连接方式", "enum", False, "", "", default="连接片+螺栓", values=["连接片+螺栓","卡扣式"]),
    ],
    "通丝吊杆/吊架": [
        make_spec("类型", "enum", True, "", "", default="通丝吊杆", values=["通丝吊杆","C型钢吊架","方钢吊架","抗震支吊架","成品吊架"]),
        make_spec("螺纹规格", "enum", True, "", "", default="M12", values=["M8","M10","M12","M14","M16","M18","M20","M22","M24"]),
        make_spec("长度", "text", True, "", "", unit="mm", default="按现场确定"),
        make_spec("材质", "enum", False, "", "", default="Q235B", values=["Q235B","304","热镀锌处理"]),
    ],
}

INSULATION_SPECS = {
    "岩棉管壳/板": [
        make_spec("形式", "enum", True, "", "", default="管壳", values=["管壳","板","毡","带"]),
        make_spec("容重", "enum", True, "GB/T 11835-2016", "", unit="kg/m³", default="80", values=["40","50","60","80","100","120","140","160"]),
        make_spec("厚度", "enum", True, "", "", unit="mm", default="50", values=["30","40","50","60","80","100","120","150"]),
        make_spec("导热系数", "text", False, "GB/T 11835-2016", "", unit="W/(m·K)", default="≤0.040"),
        make_spec("使用温度", "text", False, "", "", default="≤650℃"),
        make_spec("憎水率", "enum", False, "", "", default="≥98%(憎水型)", values=["≥98%(憎水型)","普通型"]),
        make_spec("燃烧性能", "enum", False, "", "", default="A1级(不燃)", values=["A1级(不燃)","A2级"]),
    ],
    "橡塑海绵管/板": [
        make_spec("形式", "enum", True, "", "", default="管", values=["管","板","胶带"]),
        make_spec("厚度", "enum", True, "", "", unit="mm", default="20", values=["9","13","16","19","20","25","30","32","40","50"]),
        make_spec("湿阻因子", "text", False, "", "", default="≥5000"),
        make_spec("导热系数", "text", False, "", "", unit="W/(m·K)", default="≤0.034(0℃)"),
        make_spec("氧指数", "text", False, "", "", default="≥32%"),
        make_spec("燃烧性能", "enum", False, "", "", default="B1级(难燃)", values=["B1级(难燃)","B2级(可燃)"]),
    ],
    "玻璃棉管壳/板": [
        make_spec("形式", "enum", True, "", "", default="管壳", values=["管壳","板","毡"]),
        make_spec("容重", "enum", True, "GB/T 11835-2016", "", unit="kg/m³", default="48", values=["24","32","48","64","80","96"]),
        make_spec("厚度", "enum", True, "", "", unit="mm", default="50", values=["25","30","40","50","60","80","100"]),
        make_spec("导热系数", "text", False, "", "", unit="W/(m·K)", default="≤0.037(25℃)"),
        make_spec("憎水性", "enum", False, "", "", default="有", values=["有(憎水型)","无"]),
        make_spec("燃烧性能", "enum", False, "", "", default="A1级(不燃)", values=["A1级(不燃)"]),
        make_spec("使用温度", "text", False, "", "", default="≤400℃"),
    ],
    "保温钉/铝箔胶带/粘结剂": [
        make_spec("材料类型", "enum", True, "", "", default="铝箔胶带", values=["铝箔胶带","保温钉(钢)","保温钉(塑料)","保温钉锁片","胶粘剂(水溶性)","胶粘剂(溶剂型)","玻璃纤维布","铝箔布"]),
        make_spec("铝箔厚度", "enum", False, "", "", unit="μm", default="30", values=["18","25","30","50","70"]),
        make_spec("保温钉长度", "enum", False, "", "", unit="mm", default="按保温厚度+10", values=["50","80","100","120","150","200"]),
        make_spec("胶带宽度", "enum", False, "", "", unit="mm", default="50", values=["25","50","75","100","150"]),
        make_spec("耐温等级", "text", False, "", "", default="-30℃~+120℃"),
    ],
}

AUXILIARY_SPECS = {
    "高强无收缩灌浆料": [
        make_spec("强度等级", "enum", True, "GB/T 50448-2015", "", default="C60", values=["C40","C50","C60","C70","C80","C90","C100","C120"]),
        make_spec("流动度", "enum", False, "", "", unit="mm", default="≥290(初始)", values=["≥290(初始)","≥270(初始)"]),
        make_spec("膨胀率", "text", False, "", "", default="≥0.02%(24h)" ),
        make_spec("骨料类型", "enum", False, "", "", default="细骨料", values=["细骨料(≤4.75mm)","粗骨料(≤16mm)","超细骨料(≤1.25mm)"]),
        make_spec("使用温度", "text", False, "", "", default="-5℃~+40℃"),
    ],
    "地脚螺栓(普通/锚固)": [
        make_spec("类型", "enum", True, "", "", default="直钩地脚螺栓", values=["直钩地脚螺栓","弯钩地脚螺栓","锚板地脚螺栓","双头螺栓","化学锚栓","膨胀锚栓","后扩底锚栓"]),
        make_spec("螺纹规格", "enum", True, "GB/T 799-2020", "", default="M24", values=["M12","M14","M16","M18","M20","M22","M24","M27","M30","M33","M36","M42","M48","M56","M64"]),
        make_spec("材质/性能等级", "enum", True, "", "", default="Q235B/4.8级", values=["Q235B/4.8级","Q345B/5.6级","35#/6.8级","45#/8.8级","40Cr/10.9级","35CrMo/12.9级","304(A2-70)","316(A4-70)"]),
        make_spec("表面处理", "enum", False, "", "", default="发黑", values=["发黑","镀锌","热浸锌","达克罗","无"]),
        make_spec("总长", "text", True, "", "", unit="mm", default="按设计"),
    ],
    "普通螺栓/螺母/垫圈": [
        make_spec("螺栓类型", "enum", True, "", "", default="六角头螺栓", values=["六角头螺栓(全螺纹)","六角头螺栓(半螺纹)","双头螺柱","内六角螺栓","沉头螺栓","马车螺栓"]),
        make_spec("螺纹规格", "enum", True, "", "", default="M12", values=["M6","M8","M10","M12","M14","M16","M18","M20","M22","M24","M27","M30","M36","M42","M48"]),
        make_spec("性能等级", "enum", True, "", "", default="4.8级", values=["4.6级","4.8级","5.6级","5.8级","6.8级","8.8级","10.9级","12.9级"]),
        make_spec("材质", "enum", False, "", "", default="碳钢", values=["碳钢","不锈钢304","不锈钢316","合金钢","黄铜"]),
        make_spec("表面处理", "enum", False, "", "", default="发黑", values=["发黑","镀锌","热浸锌","达克罗","无"]),
    ],
    "电焊条(J422/J507)": [
        make_spec("焊条牌号", "enum", True, "GB/T 5117-2012", "", default="J422", values=["J422","J502","J506","J507","J427","J507RH","E308(A102)","E309(A302)","E316(A202)","E308L(A002)","E316L(A022)"]),
        make_spec("直径", "enum", True, "", "", unit="mm", default="3.2", values=["2.0","2.5","3.2","4.0","5.0"]),
        make_spec("药皮类型", "enum", False, "", "", default="钛钙型", values=["钛钙型","低氢型","纤维素型"]),
        make_spec("适用母材", "enum", False, "", "", default="碳钢", values=["碳钢","低合金钢","耐热钢","不锈钢","铸铁","堆焊"]),
        make_spec("电流类型", "enum", False, "", "", default="AC/DC", values=["AC","DC(+)","DC(-)","AC/DC"]),
    ],
    "密封胶/垫片/填料": [
        make_spec("密封类型", "enum", True, "", "", default="硅酮密封胶", values=["硅酮密封胶(中性)","硅酮密封胶(酸性)","聚氨酯密封胶","聚硫密封胶","丁基密封胶","石棉垫片","金属缠绕垫片","四氟垫片(PTFE)","石墨垫片","O型圈(丁腈橡胶)","O型圈(氟橡胶)","O型圈(硅橡胶)","盘根(石墨)","盘根(四氟)"]),
        make_spec("适用介质", "enum", True, "", "", default="水/空气", values=["水/空气","热油","蒸汽","酸碱","溶剂","燃气"]),
        make_spec("使用温度范围", "text", False, "", "", default="-40℃~+150℃"),
        make_spec("颜色(密封胶)", "enum", False, "", "", values=["透明","白色","黑色","灰色","古铜色"]),
        make_spec("包装规格", "enum", False, "", "", default="300ml(胶)", values=["300ml(胶)","600ml(软包)","按重量(kg)"]),
    ],
    "穿墙套管(刚性/柔性)": [
        make_spec("套管类型", "enum", True, "", "", default="刚性防水套管", values=["刚性防水套管(A型)","刚性防水套管(B型)","柔性防水套管(A型)","柔性防水套管(B型)","穿墙钢套管","人防密闭套管","电气管道套管"]),
        make_spec("公称直径", "enum", True, "", "", default="DN150", values=["DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200","DN250","DN300","DN350","DN400","DN500"]),
        make_spec("长度", "text", True, "", "", unit="mm", default="按墙厚"),
        make_spec("材质", "enum", False, "", "", default="Q235B", values=["Q235B","304","316L"]),
    ],
    "防火封堵材料(防火泥/防火包)": [
        make_spec("封堵类型", "enum", True, "", "", default="防火泥", values=["防火泥(有机)","防火泥(无机)","防火包","防火板","防火密封胶","阻火圈","防火泡沫","防火涂料"]),
        make_spec("耐火极限", "enum", True, "", "", default="≥2h", values=["≥1h","≥2h","≥3h","≥4h"]),
        make_spec("使用部位", "enum", False, "", "", default="电缆穿墙/穿楼板", values=["电缆穿墙/穿楼板","穿墙套管","管道贯穿","电缆沟","缝隙密封"]),
        make_spec("有烟毒性", "enum", False, "", "", default="低烟无毒", values=["低烟无毒","微毒"]),
    ],
    "接地材料(镀锌扁钢/圆钢/铜排)": [
        make_spec("材料形式", "enum", True, "GB/T 50065-2011", "", default="镀锌扁钢", values=["镀锌扁钢(水平)","镀锌圆钢(垂直接地极)","铜排","铜包钢","锌包钢","铜绞线","铜棒"]),
        make_spec("规格(扁钢x厚/圆钢直径)", "enum", True, "", "", default="40x4(扁钢)", values=["25x4","30x4","40x4","50x5","50x6","63x6","Φ12(圆钢)","Φ16","Φ20","Φ25"]),
        make_spec("防腐方式", "enum", False, "", "", default="热镀锌", values=["热镀锌","铜包钢","导电防腐涂料","无(铜)"]),
        make_spec("电阻率要求", "text", False, "", "", unit="Ω·m", default="按设计"),
        make_spec("连接方式", "enum", False, "", "", default="焊接", values=["焊接","放热焊接(热熔焊)","螺栓连接","压接"]),
    ],
    "绝缘材料(绝缘垫/绝缘子/热缩管)": [
        make_spec("材料类型", "enum", True, "", "", default="热缩管", values=["绝缘垫(橡胶)","绝缘垫(环氧)","支柱绝缘子","穿墙套管(绝缘)","热缩管(低压)","热缩管(中压)","冷缩管(中压)","绝缘胶带(PVC)","绝缘胶带(自粘)"]),
        make_spec("电压等级", "enum", True, "", "", default="0.6/1kV", values=["0.6/1kV","6kV","10kV","20kV","35kV"]),
        make_spec("耐温等级", "enum", False, "", "", default="90℃", values=["70℃","90℃","105℃","125℃"]),
        make_spec("阻燃性", "enum", False, "", "", default="V-0", values=["V-0","V-1","V-2","HB"]),
    ],
    "型钢吊架/支架(成品)": [
        make_spec("支架类型", "enum", True, "", "", default="综合支吊架", values=["综合支吊架(成品)","抗震支吊架","管廊支架","重型支架","轻型支架","C型钢支架系统"]),
        make_spec("荷载等级", "enum", True, "", "", unit="kN", default="按设计", values=["轻载(≤2kN)","中载(2-8kN)","重载(>8kN)"]),
        make_spec("材质", "enum", True, "", "", default="Q235B热镀锌", values=["Q235B热镀锌","Q345B热镀锌","304不锈钢","铝合金"]),
        make_spec("防腐处理", "enum", False, "", "", default="热浸锌(≥45μm)", values=["电镀锌(8-12μm)","热浸锌(≥45μm)","热浸锌(≥85μm)","环氧喷涂"]),
        make_spec("抗震设防烈度", "enum", False, "", "", values=["6度","7度","8度","9度"]),
    ],
    "电缆终端头/中间接头": [
        make_spec("接头类型", "enum", True, "", "", default="热缩终端头", values=["热缩终端头(户内)","热缩终端头(户外)","冷缩终端头","硅橡胶预制终端头","热缩中间接头","冷缩中间接头","插拔式终端"]),
        make_spec("电压等级", "enum", True, "", "", default="0.6/1kV", values=["0.6/1kV","8.7/15kV","12/20kV","26/35kV"]),
        make_spec("电缆截面范围", "text", True, "", "", unit="mm²", default="按电缆截面"),
        make_spec("芯数", "enum", False, "", "", default="4芯", values=["单芯","3芯","4芯","5芯"]),
    ],
    "软接头/橡胶接头": [
        make_spec("接头类型", "enum", True, "GB/T 14905-2020", "", default="单球橡胶接头", values=["单球橡胶接头","双球橡胶接头","同心异径橡胶接头","偏心异径橡胶接头","金属软接头(不锈钢波纹)","PTFE软接头"]),
        make_spec("公称直径", "enum", True, "", "", default="DN65", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200","DN250","DN300"]),
        make_spec("公称压力", "enum", True, "", "", default="PN16", values=["PN6","PN10","PN16","PN25","PN40"]),
        make_spec("橡胶材质", "enum", False, "", "", default="EPDM", values=["EPDM(三元乙丙)","丁腈橡胶(NBR)","氯丁橡胶","氟橡胶(耐热耐油)"]),
        make_spec("连接方式", "enum", False, "", "", default="法兰连接", values=["法兰连接","螺纹连接","沟槽连接"]),
    ],
    "仪表接头/卡套/阀门": [
        make_spec("类型", "enum", True, "", "", default="卡套接头", values=["卡套接头(双卡套)","卡套接头(单卡套)","焊接式接头","扩口式接头","螺纹接头","三阀组","五阀组","平衡阀组"]),
        make_spec("公称直径/管径", "enum", True, "", "", default="Φ12", values=["Φ3","Φ6","Φ8","Φ10","Φ12","Φ14","Φ18","Φ25"]),
        make_spec("材质", "enum", True, "", "", default="316SS", values=["304","316SS","316L","蒙乃尔合金","哈氏合金","黄铜"]),
        make_spec("连接规格", "text", False, "", "", default="NPT 1/2或M20x1.5"),
    ],
    "减振垫/弹簧减振器": [
        make_spec("减振类型", "enum", True, "", "", default="弹簧减振器", values=["弹簧减振器","橡胶减振垫","弹簧+橡胶组合减振器","聚氨酯减振垫","气垫减振器"]),
        make_spec("荷载范围", "text", True, "", "", unit="kg", default="按设备重量"),
        make_spec("自振频率", "text", False, "", "", unit="Hz", default="2-5"),
        make_spec("阻尼比", "text", False, "", "", default="≥0.05(橡胶)/≥0.03(弹簧)"),
    ],
    "水位控制阀/浮球阀": [
        make_spec("类型", "enum", True, "", "", default="液压水位控制阀", values=["液压水位控制阀","浮球阀(不锈钢)","浮球阀(铜)","电动水位控制阀","远传液位控制阀"]),
        make_spec("公称直径", "enum", True, "", "", default="DN50", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200"]),
        make_spec("公称压力", "enum", False, "", "", default="PN10", values=["PN6","PN10","PN16","PN25"]),
        make_spec("阀体材质", "enum", False, "", "", default="铸铁", values=["铸铁","球墨铸铁","不锈钢","铜"]),
        make_spec("连接方式", "enum", False, "", "", default="法兰连接", values=["法兰连接","螺纹连接"]),
    ],
    "Y型过滤器": [
        make_spec("公称直径", "enum", True, "", "", default="DN65", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80","DN100","DN125","DN150","DN200","DN250","DN300","DN350","DN400"]),
        make_spec("公称压力", "enum", True, "", "", default="PN16", values=["PN10","PN16","PN25","PN40"]),
        make_spec("阀体材质", "enum", True, "", "", default="铸铁", values=["铸铁","球墨铸铁","铸钢","不锈钢304","不锈钢316L","黄铜","UPVC"]),
        make_spec("过滤精度/目数", "enum", True, "", "", default="60目", values=["18目(粗)","40目","60目","80目","100目","200目","300目(精)"]),
        make_spec("连接方式", "enum", False, "", "", default="法兰连接", values=["法兰连接","螺纹连接","焊接"]),
        make_spec("滤网材质", "enum", False, "", "", default="304SS", values=["304SS","316LSS","蒙乃尔合金"]),
    ],
    "橡胶垫块/木垫": [
        make_spec("类型", "enum", True, "", "", default="平垫铁", values=["平垫铁(碳钢)","斜垫铁(碳钢)","调整垫铁","橡胶垫块","木垫(松木)","木垫(硬木)","聚四氟乙烯滑动垫"]),
        make_spec("规格尺寸", "text", True, "", "", unit="mm", default="100x80x(厚度变量)"),
        make_spec("荷载等级", "text", False, "", "", default="按设备重量"),
    ],
    "石棉绳/布/板": [
        make_spec("材料形式", "enum", True, "", "", default="石棉绳", values=["石棉绳","石棉布","石棉板","石棉橡胶板","石棉水泥板","无石棉橡胶板","陶瓷纤维绳/布"]),
        make_spec("厚度/直径", "enum", True, "", "", unit="mm", default="3(绳)/5(板)", values=["1","1.5","2","3","4","5","6","8","10","12","15","20"]),
        make_spec("使用温度", "text", False, "", "", default="≤450℃(石棉)/≤1200℃(陶瓷)"),
        make_spec("含石棉", "enum", True, "", "", default="含石棉", values=["含石棉","无石棉"]),
    ],
    "配管(镀锌钢管/可挠金属管/阻燃PVC)": [
        make_spec("管材类型", "enum", True, "", "", default="KBG扣压式薄壁钢管", values=["JDG紧定式薄壁钢管","KBG扣压式薄壁钢管","SC焊接钢管","可挠金属管(普利卡)","阻燃PVC电工套管","金属软管(包塑)"]),
        make_spec("公称直径", "enum", True, "GB/T 3091-2015", "", unit="mm", default="DN20", values=["DN15","DN20","DN25","DN32","DN40","DN50","DN65","DN80"]),
        make_spec("壁厚", "enum", True, "", "", unit="mm", default="1.6", values=["1.0","1.2","1.5","1.6","2.0","2.5","3.0","3.5","4.0"]),
        make_spec("表面处理", "enum", False, "", "", default="热镀锌", values=["热镀锌","电镀锌","涂塑","无(PVC)"]),
        make_spec("连接方式", "enum", False, "", "", default="扣压/紧定", values=["扣压/紧定","螺纹","焊接","胶粘(PVC)"]),
        make_spec("阻燃等级(PVC)", "enum", False, "", "", default="V-0", values=["V-0","V-1"]),
    ],
    "零星/杂项材料(按项)": [
        make_spec("材料描述", "text", True, "", "", default="按设计图纸或建设单位要求"),
        make_spec("计量方式", "enum", True, "", "", default="按项", values=["按项","按批","按套","按面积(m²)","按重量(kg)","按长度(m)"]),
        make_spec("参考单项目估价", "text", False, "", "", unit="元"),
        make_spec("是否可分开列项", "boolean", False, "", "", default="否"),
    ],
}

PAINT_SPECS = {
    "环氧富锌底漆": [
        make_spec("漆类型", "enum", True, "HG/T 3668-2009", "", default="环氧富锌底漆(双组分)", values=["环氧富锌底漆(双组分)","环氧富锌底漆(单组分)","水性环氧富锌底漆"]),
        make_spec("干膜含锌量", "enum", True, "HG/T 3668-2009", "", default="≥80%", values=["≥80%","≥70%","≥60%"]),
        make_spec("干膜厚度/道", "enum", True, "", "", unit="μm", default="60-80", values=["40-50","60-80","80-100","100-125"]),
        make_spec("理论涂布率", "text", False, "", "", unit="m²/kg", default="按供应商数据"),
        make_spec("配比(主漆:固化剂)", "text", False, "", "", default="10:1"),
        make_spec("适用底材", "enum", False, "", "", default="钢材(喷砂Sa2.5)", values=["钢材(喷砂Sa2.5)","钢材(St3手工除锈)","镀锌钢(轻微拉毛)"]),
        make_spec("颜色", "enum", False, "", "", default="灰色", values=["灰色","棕灰色","红灰色"]),
        make_spec("施工方式", "enum", False, "", "", default="无气喷涂", values=["无气喷涂","空气喷涂","刷涂","滚涂"]),
        make_spec("VOC含量", "text", False, "HG/T 3668-2009", "", unit="g/L", default="按国标限量"),
    ],
    "环氧云铁中间漆": [
        make_spec("漆类型", "enum", True, "", "", default="环氧云铁中间漆(双组分)", values=["环氧云铁中间漆(双组分)","环氧云铁中间漆(厚浆型)","改性环氧云铁漆","水性环氧云铁漆"]),
        make_spec("干膜厚度/道", "enum", True, "", "", unit="μm", default="80-100", values=["50-80","80-100","100-150","150-200(厚浆)"]),
        make_spec("云母氧化铁含量", "text", False, "", "", default="≥30%"),
        make_spec("体积固含量", "text", False, "", "", unit="%", default="≥50%"),
        make_spec("适用底漆", "enum", False, "", "", default="环氧富锌底漆", values=["环氧富锌底漆","环氧底漆","无机富锌底漆"]),
        make_spec("颜色", "enum", False, "", "", default="灰色/红褐色", values=["灰色","红褐色","铁红色"]),
    ],
    "聚氨酯面漆/丙烯酸面漆": [
        make_spec("漆类型", "enum", True, "", "", default="脂肪族聚氨酯面漆", values=["脂肪族聚氨酯面漆(保光)","芳香族聚氨酯面漆","丙烯酸面漆(单组分)","丙烯酸聚氨酯面漆","聚硅氧烷面漆(超耐候)","氟碳面漆(极耐候)"]),
        make_spec("干膜厚度/道", "enum", True, "", "", unit="μm", default="40-50", values=["30-40","40-50","50-60","60-80"]),
        make_spec("光泽度", "enum", False, "", "", default="半光(40-60GU)", values=["高光(>80GU)","半光(40-60GU)","哑光(10-30GU)","无光(<10GU)"]),
        make_spec("耐候等级", "enum", False, "", "", default="C3/C4(中等腐蚀)", values=["C1/C2(低腐蚀/户内)","C3/C4(中等腐蚀/城市户外)","C5-I(高腐蚀/工业)","C5-M(海洋环境)"]),
        make_spec("适用中间漆", "enum", False, "", "", default="环氧云铁中间漆", values=["环氧云铁中间漆","环氧中间漆","环氧底漆(底面合一)"]),
        make_spec("颜色", "enum", False, "", "", default="按色卡", values=["各色可选/按色卡"]),
        make_spec("施工方式", "enum", False, "", "", default="无气喷涂", values=["无气喷涂","空气喷涂","刷涂"]),
    ],
    "防火涂料(薄型/厚型)": [
        make_spec("涂料类型", "enum", True, "", "", default="薄型(膨胀型)", values=["超薄型(≤3mm)","薄型(3-7mm,膨胀型)","厚型(8-50mm,隔热型)","室外型","室内型"]),
        make_spec("耐火极限", "enum", True, "", "", default="2.0h", values=["1.0h","1.5h","2.0h","2.5h","3.0h"]),
        make_spec("耐火极限对应的涂层厚度", "text", True, "", "", unit="mm", default="按厂家型式检验报告"),
        make_spec("粘结强度", "text", False, "", "", default="≥0.15MPa(厚型)/≥0.20MPa(薄型)"),
        make_spec("适用基材", "enum", False, "", "", default="结构钢(H型/箱型)", values=["结构钢(H型/箱型)","钢管","铸钢节点","压型钢板"]),
        make_spec("施工方式", "enum", False, "", "", default="喷涂", values=["喷涂","抹涂","滚涂","刷涂"]),
        make_spec("VOC含量", "text", False, "", "", default="按国标"),
    ],
    "耐酸砖/板衬里": [
        make_spec("衬里类型", "enum", True, "GB/T 8488-2008", "", default="耐酸砖衬里", values=["耐酸砖衬里","耐酸瓷板衬里","铸石板衬里","橡胶衬里(衬胶)","铅衬里(搪铅)","环氧树脂衬里","乙烯基酯树脂衬里","玻璃钢衬里(FRP)"]),
        make_spec("砖/板规格", "text", True, "", "", unit="mm", default="230x113x65(标砖)"),
        make_spec("胶泥类型", "enum", True, "", "", default="钾水玻璃胶泥", values=["钾水玻璃胶泥","钠水玻璃胶泥","环氧树脂胶泥","呋喃树脂胶泥","酚醛树脂胶泥","乙烯基酯树脂胶泥"]),
        make_spec("耐酸度", "text", False, "", "", default="≥99.8%(耐酸砖)"),
        make_spec("最高使用温度", "text", False, "", "", default="按胶泥类型确定"),
    ],
}

MISC_SPECS = {
    "仪表管(不锈钢/铜)": [
        make_spec("管材类型", "enum", True, "", "", default="不锈钢仪表管", values=["不锈钢仪表管(304)","不锈钢仪表管(316)","铜仪表管(紫铜)","尼龙管(PA)","PFA管"]),
        make_spec("外径x壁厚", "enum", True, "", "", default="Φ12x1.0", values=["Φ6x1.0","Φ8x1.0","Φ10x1.0","Φ12x1.0","Φ14x1.5","Φ18x1.5","Φ25x2.0"]),
        make_spec("长度", "enum", False, "", "", default="6m/根", values=["3m/根","6m/根","盘管(≥50m)"]),
        make_spec("承压等级", "enum", False, "", "", default="≤16MPa", values=["≤6.4MPa","≤16MPa","≤32MPa","≤42MPa"]),
        make_spec("表面状态", "enum", False, "", "", default="光亮退火", values=["酸洗","光亮退火","抛光"]),
    ],
    "仪表阀门/三阀组": [
        make_spec("阀门类型", "enum", True, "", "", default="针形截止阀", values=["针形截止阀","球阀(仪表用)","三阀组","五阀组","仪表阀组(一体式)","单向阀(仪表用)","过压保护阀"]),
        make_spec("公称直径", "enum", True, "", "", default="DN6", values=["DN3","DN6","DN10","DN15","DN20","DN25"]),
        make_spec("公称压力", "enum", True, "", "", default="PN16", values=["PN16","PN25","PN40","PN63","PN100","PN160","PN250","PN320","PN420"]),
        make_spec("材质", "enum", True, "", "", default="316SS", values=["304SS","316SS","316L","蒙乃尔","哈氏合金C276","黄铜"]),
        make_spec("连接方式", "enum", True, "", "", default="卡套连接", values=["卡套连接","螺纹连接(NPT)","焊接","法兰连接"]),
        make_spec("操作方式", "enum", False, "", "", default="手动", values=["手动","气动"]),
    ],
    "散热器(钢制/铸铝)": [
        make_spec("散热器类型", "enum", True, "", "", default="钢制柱式散热器", values=["钢制柱式散热器","钢制板式散热器","铸铝散热器","铜铝复合散热器","铸铁散热器","踢脚线散热器","钢制翅片管散热器"]),
        make_spec("散热量", "text", True, "", "", unit="W/片", default="按设计"),
        make_spec("中心距", "enum", False, "", "", unit="mm", default="600", values=["300","400","500","600","900","1200","1500","1800"]),
        make_spec("公称压力", "enum", False, "", "", default="1.0MPa", values=["0.6MPa","1.0MPa","1.6MPa","2.0MPa"]),
        make_spec("连接方式", "enum", False, "", "", default="DN15/20接口", values=["DN15接口","DN20接口","1/2\"接口","3/4\"接口"]),
        make_spec("表面处理", "enum", False, "", "", default="喷涂(白色)", values=["喷涂(白色)","喷涂(其他RAL色)","电泳"]),
        make_spec("带温控阀", "boolean", False, "", "", default="否"),
    ],
    "地暖管/集分水器": [
        make_spec("地暖管材质", "enum", True, "", "", default="PE-RT", values=["PE-RT(耐热聚乙烯)","PE-Xa(过氧化物交联)","PE-Xb(硅烷交联)","PE-Xc(辐照交联)","PB(聚丁烯)","铝塑复合管"]),
        make_spec("管径", "enum", True, "", "", default="dn16", values=["dn16","dn20","dn25"]),
        make_spec("壁厚", "enum", False, "", "", unit="mm", default="2.0", values=["1.8","2.0","2.3","2.8"]),
        make_spec("集分水器回路数", "enum", False, "", "", default="6回路", values=["2回路","3回路","4回路","5回路","6回路","8回路","10回路","12回路"]),
        make_spec("集分水器材质", "enum", False, "", "", default="黄铜锻造", values=["黄铜锻造","不锈钢","PPR"]),
        make_spec("带流量计", "boolean", False, "", "", default="是"),
    ],
    "LED灯具(平板/筒灯/吸顶)": [
        make_spec("灯具类型", "enum", True, "", "", default="LED平板灯", values=["LED平板灯(嵌入式)","LED平板灯(吸顶)","LED筒灯","LED吸顶灯","LED格栅灯","LED教室灯","LED面板灯(洁净)"]),
        make_spec("功率", "enum", True, "", "", unit="W", default="36", values=["6","9","12","15","18","24","30","36","48","60","72","100"]),
        make_spec("色温", "enum", True, "", "", default="4000K(中性白光)", values=["2700K(暖光)","3000K(暖白)","4000K(中性白光)","5000K(正白光)","5700K(冷白光)","6500K(日光)"]),
        make_spec("显色指数", "enum", False, "", "", default="Ra≥80", values=["Ra60-70","Ra≥80","Ra≥90(高显色)","Ra≥95(博物馆级)"]),
        make_spec("安装方式", "enum", False, "", "", default="嵌入式", values=["嵌入式","吸顶","吊装","管吊","壁装"]),
        make_spec("防护等级", "enum", False, "", "", default="IP20(户内)", values=["IP20(户内)","IP44(防溅)","IP65(户外/防水)","IP66(防尘+防水)"]),
        make_spec("防眩光", "enum", False, "", "", default="UGR<19(标准办公)", values=["UGR<16(无眩光)","UGR<19(标准办公)","UGR<22","无"]),
        make_spec("能效等级", "enum", False, "", "", default="1级", values=["1级","2级","3级"]),
    ],
    "应急照明灯具": [
        make_spec("灯具类型", "enum", True, "", "", default="疏散指示灯", values=["疏散指示灯(安全出口)","疏散指示灯(方向/箭头)","应急照明灯(双头灯)","应急吸顶灯","应急筒灯","楼层指示灯","应急壁灯","集中控制型(智能)"]),
        make_spec("应急时间", "enum", True, "", "", default="≥90min", values=["≥30min","≥60min","≥90min","≥120min","≥180min"]),
        make_spec("光源类型", "enum", False, "", "", default="LED", values=["LED","荧光灯管"]),
        make_spec("电池类型", "enum", False, "", "", default="锂电池(内置)", values=["镍镉电池(内置)","镍氢电池(内置)","锂电池(内置)","集中供电(无内置电池)"]),
        make_spec("安装方式", "enum", False, "", "", default="壁挂式", values=["壁挂式","嵌入式","管吊","吸顶"]),
        make_spec("防护等级", "enum", False, "", "", default="IP30(户内)", values=["IP30(户内)","IP65(户外/地下车库)","IP67(浸水保护)"]),
    ],
    "开关/插座": [
        make_spec("类型", "enum", True, "", "", default="单联单控开关", values=["单联单控开关","双联单控开关","三联单控开关","双控开关","中途开关","五孔插座(10A)","五孔插座(16A)","三孔插座(空调16A)","USB插座","地面插座","防水插座(带盖)"]),
        make_spec("额定电流", "enum", True, "", "", default="10A(插座)/10AX(开关)", values=["10A","16A","20A","25A","32A"]),
        make_spec("面板材质", "enum", False, "", "", default="PC(聚碳酸酯)", values=["PC(聚碳酸酯)","尿素树脂","金属拉丝","钢化玻璃"]),
        make_spec("颜色", "enum", False, "", "", default="白色", values=["白色","香槟金","钛金灰","黑色","银色"]),
        make_spec("安装方式", "enum", False, "", "", default="86底盒", values=["86型暗装","118型","120型","地面"]),
    ],
    "照明配电箱(户内)": [
        make_spec("型号", "text", True, "", "", default="PZ30"),
        make_spec("回路数", "enum", True, "", "", default="12回路", values=["2回路","4回路","6回路","8回路","10回路","12回路","16回路","18回路","20回路","24回路","36回路"]),
        make_spec("箱体材质", "enum", True, "", "", default="冷轧钢板喷塑", values=["冷轧钢板喷塑","ABS塑料","不锈钢"]),
        make_spec("安装方式", "enum", True, "", "", default="嵌墙暗装", values=["嵌墙暗装","挂墙明装"]),
        make_spec("防护等级", "enum", False, "", "", default="IP30", values=["IP30","IP65"]),
        make_spec("额定电压", "enum", False, "", "", default="220V", values=["220V","380/220V"]),
    ],
}

MISC2_SPECS = {
    "防雷装置（避雷针/避雷带/避雷网）": [
        make_spec("装置类型", "enum", True, "", "", default="避雷带", values=["避雷针(独立)","避雷针(屋顶)","避雷带(明装)","避雷带(暗装)","避雷网(屋面网格)","提前放电避雷针(ESE)","电涌保护器(SPD)"]),
        make_spec("材质", "enum", True, "", "", default="热镀锌圆钢", values=["热镀锌圆钢","热镀锌扁钢","不锈钢圆钢","铜带","铜包钢"]),
        make_spec("规格", "enum", True, "", "", default="Φ12(圆钢)", values=["Φ10","Φ12","Φ16","Φ20","40x4(扁钢)","50x6(扁钢)"]),
        make_spec("安装高度(避雷针)", "text", False, "", "", unit="m"),
        make_spec("网格尺寸(避雷网)", "enum", False, "", "", default="≤10m×10m", values=["≤5m×5m(二类)","≤10m×10m(三类)","≤20m×20m"]),
        make_spec("SPD等级(T1/T2/T3)", "enum", False, "", "", default="T2(二级)", values=["T1(一级,10/350μs)","T2(二级,8/20μs)","T3(三级)","T1+T2组合"]),
        make_spec("SPD最大放电电流", "text", False, "", "", unit="kA", default="Iimp≥12.5kA(T1)"),
    ],
    "接地装置（接地极/接地母线/接地网）": [
        make_spec("接地类型", "enum", True, "", "", default="人工接地装置", values=["人工接地装置(垂直接地极+水平连接)","自然接地体(基础钢筋)","铜排接地网","离子接地极","深井接地","化学降阻接地"]),
        make_spec("接地极材质", "enum", True, "", "", default="镀锌钢管", values=["镀锌钢管(DN50)","镀锌圆钢(Φ20)","铜包钢","纯铜棒","不锈钢","石墨"]),
        make_spec("接地极规格", "text", True, "", "", default="L=2500mm"),
        make_spec("接地母线材质/规格", "enum", False, "", "", default="40x4镀锌扁钢", values=["25x4镀锌扁钢","40x4镀锌扁钢","50x6镀锌扁钢","铜排30x3","铜绞线50mm²"]),
        make_spec("接地电阻要求", "enum", True, "", "", default="≤1Ω", values=["≤0.5Ω","≤1Ω","≤4Ω","≤10Ω","≤30Ω"]),
    ],
    "过程分析仪表": [
        make_spec("分析类型", "enum", True, "", "", default="在线pH计", values=["在线pH计","电导率仪","溶氧分析仪","浊度计","在线COD分析仪","在线氨氮分析仪","在线总磷/总氮分析仪","气体分析仪(O2)","气体分析仪(CO)","气体分析仪(SO2/NOx)","气相色谱仪(过程)","TOC分析仪","硅表/磷表","ORP计"]),
        make_spec("测量原理", "enum", True, "", "", default="按仪表类型", values=["电化学","光学","热导","顺磁","色谱","非分散红外(NDIR)","紫外荧光"]),
        make_spec("输出信号", "enum", True, "", "", default="4-20mA+HART", values=["4-20mA","4-20mA+HART","MODBUS RTU","PROFIBUS PA","FF","EtherNet/IP"]),
        make_spec("供电", "enum", False, "", "", default="24VDC", values=["24VDC","220VAC","回路供电"]),
        make_spec("防爆等级", "enum", False, "", "", default="无", values=["无","Ex d(隔爆)","Ex ia(本安)","Ex d ia"]),
        make_spec("防护等级", "enum", False, "", "", default="IP65", values=["IP54","IP65","IP66"]),
        make_spec("预处理系统", "boolean", False, "", "", default="是(气体/高温/高压)"),
    ],
    "工厂对讲系统": [
        make_spec("系统类型", "enum", True, "", "", default="全厂扩音对讲", values=["全厂扩音对讲系统","无主机对讲","有主机对讲(程控)","防爆对讲系统","广播扩音系统","一键呼叫系统"]),
        make_spec("覆盖区域", "text", True, "", "", default="全厂区"),
        make_spec("终端数量", "text", False, "", "", default="按设计"),
        make_spec("防爆要求", "enum", False, "", "", default="部分防爆(Ex d)", values=["无","部分防爆(Ex d)","全防爆(Ex d/Ex ia)"]),
        make_spec("控制方式", "enum", False, "", "", default="集中控制", values=["集中控制","分散控制","集中+分散"]),
        make_spec("与火灾报警联动", "boolean", False, "", "", default="否"),
    ],
    "工业计算机系统": [
        make_spec("系统类型", "enum", True, "", "", default="DCS控制系统", values=["DCS(集散控制系统)","PLC(可编程控制器)","SIS(安全仪表系统)","SCADA(监控与数据采集)","MES(制造执行系统)","工控机(IPC)","FGS(火灾/气体检测系统)","ESD(紧急停车系统)"]),
        make_spec("控制器型号/CPU", "text", True, "", "", default="按设计选型"),
        make_spec("I/O点数(AI/AO/DI/DO)", "text", True, "", "", default="按设计"),
        make_spec("冗余配置", "enum", False, "", "", default="控制器冗余", values=["无冗余","控制器冗余","控制器+电源冗余","全冗余(控制器+电源+通信)","三重冗余(TMR,SIS用)"]),
        make_spec("通信协议", "enum", False, "", "", default="EtherNet/IP+Modbus RTU", values=["Modbus RTU","Modbus TCP","EtherNet/IP","PROFIBUS-DP","PROFINET","FF-H1","HART","OPC UA"]),
        make_spec("操作站", "text", False, "", "", default="按设计(工程师站+操作员站)"),
        make_spec("SIS安全完整性等级", "enum", False, "", "", default="SIL2", values=["SIL1","SIL2","SIL3","SIL4"]),
        make_spec("供电要求", "enum", False, "", "", default="220VAC+UPS", values=["220VAC","24VDC","220VAC+UPS","24VDC+冗余"]),
    ],
    "气柜": [
        make_spec("气柜类型", "enum", True, "", "", default="干式气柜", values=["干式气柜(曼型)","干式气柜(威金斯型)","干式气柜(可隆型)","湿式气柜(单节)","湿式气柜(多节)","双膜气柜"]),
        make_spec("公称容积", "text", True, "", "", unit="m³", default="按设计"),
        make_spec("储存介质", "enum", True, "", "", default="焦炉煤气", values=["焦炉煤气","高炉煤气","转炉煤气","天然气","沼气","氢气","合成气","氧气"]),
        make_spec("工作压力", "text", False, "", "", unit="kPa", default="按设计(干式3-10kPa)"),
        make_spec("密封形式(干式)", "enum", False, "", "", default="稀油密封", values=["稀油密封","润滑脂密封","橡胶膜密封"]),
        make_spec("材质", "enum", False, "", "", default="Q345R(筒体)", values=["Q235B","Q345R","Q345qD","304"]),
        make_spec("设计温度", "text", False, "", "", default="-40℃~+60℃(按介质)"),
    ],
    "非金属化工设备": [
        make_spec("设备类型", "enum", True, "", "", default="玻璃钢储罐", values=["玻璃钢储罐(FRP)","搪玻璃反应釜","石墨换热器","塑料储罐(PP/HDPE)","玻璃钢管道","衬氟设备(PTFE/PFA)","衬胶设备","石墨吸收塔"]),
        make_spec("材质/衬里", "enum", True, "", "", default="FRP(玻璃钢)", values=["FRP(玻璃钢)","PP(聚丙烯)","HDPE","PVDF","搪玻璃","石墨(浸渍)","PTFE衬里","PFA衬里","衬胶(天然橡胶/氯丁橡胶)"]),
        make_spec("公称容积/规格", "text", True, "", "", default="按设计"),
        make_spec("工作压力", "text", False, "", "", unit="MPa", default="常压"),
        make_spec("工作温度", "text", False, "", "", default="按设计"),
        make_spec("适用介质", "text", True, "", "", default="按工艺介质"),
        make_spec("设计标准", "enum", False, "", "", default="HG/T 20696(FRP)", values=["HG/T 20696(FRP)","HG/T 2371(搪玻璃)","HG/T 2370(石墨)"]),
    ],
    "无损检验(服务)": [
        make_spec("检测方法", "enum", True, "", "", default="射线检测(RT)", values=["射线检测(RT)","超声波检测(UT)","渗透检测(PT)","磁粉检测(MT)","涡流检测(ET)","目视检测(VT)","泄漏检测(LT)","相控阵(PAUT)","TOFD","DR数字射线"]),
        make_spec("检测比例", "enum", True, "", "", default="100%", values=["5%","10%","20%","25%","50%","100%","局部抽查"]),
        make_spec("检测标准", "enum", True, "", "", default="NB/T 47013", values=["NB/T 47013(承压设备)","GB/T 3323(焊缝/RT)","GB/T 11345(焊缝/UT)","JB/T 4730","ASME BPVC Sec V"]),
        make_spec("检测部位", "enum", False, "", "", default="焊缝", values=["焊缝(对接)","焊缝(角接)","母材","铸件","锻件","堆焊层"]),
        make_spec("合格等级", "enum", False, "", "", default="Ⅱ级", values=["Ⅰ级(最严)","Ⅱ级","Ⅲ级"]),
        make_spec("出报告要求", "boolean", False, "", "", default="是"),
        make_spec("检测单位资质", "enum", False, "", "", default="特种设备无损检测机构", values=["自检(不需资质)","特种设备无损检测机构(B级)","特种设备无损检测机构(A级)"]),
    ],
    "电气调整试验(服务)": [
        make_spec("试验类型", "enum", True, "", "", default="交接试验", values=["交接试验(新装)","预防性试验(定期)","诊断性试验","保护装置校验","绝缘试验","耐压试验","继电保护整组试验"]),
        make_spec("设备范围", "enum", True, "", "", default="全站/全厂", values=["全站/全厂电力系统","仅变压器","仅开关柜","仅保护装置","发电机组(并网试验)","GIS/电缆线路"]),
        make_spec("试验标准", "enum", True, "", "", default="GB 50150-2016", values=["GB 50150-2016(电气装置安装工程交接试验)","DL/T 596-2021(电力设备预防性试验)","GB/T 7261(继电保护)"]),
        make_spec("电压等级", "enum", True, "", "", default="10kV", values=["0.4kV","6kV","10kV","20kV","35kV","110kV","220kV"]),
        make_spec("试验仪器要求", "enum", False, "", "", default="试验单位自备", values=["试验单位自备"]),
        make_spec("出试验报告", "boolean", False, "", "", default="是"),
    ],
    "采暖空调水系统调试(服务)": [
        make_spec("调试类型", "enum", True, "", "", default="水系统冲洗+试压+平衡", values=["管道冲洗","水压试验","气压试验","水力平衡调试","风平衡调试","单机试运转","系统联合试运转","空调系统综合效能测试"]),
        make_spec("系统规模", "text", True, "", "", default="按设计(总冷量kW/总风量m³/h)"),
        make_spec("调试标准", "enum", True, "", "", default="GB 50243-2016(通风与空调)", values=["GB 50243-2016(通风与空调工程施工质量验收)","GB 50242-2002(建筑给排水及采暖)","GB 50235-2010(工业金属管道)"]),
        make_spec("风平衡精度", "text", False, "", "", default="±10%(舒适性空调) / ±5%(工艺空调)"),
        make_spec("水系统试压倍率", "text", False, "", "", default="1.5倍工作压力≥0.6MPa"),
        make_spec("出调试报告", "boolean", False, "", "", default="是"),
    ],
    "锅炉系统调试与性能试验(服务)": [
        make_spec("调试/试验类型", "enum", True, "", "", default="分系统调试+整套启动", values=["分系统调试","整套启动调试","168h满负荷试运行","性能考核试验","燃烧调整试验","热效率试验","SCR脱硝性能试验","FGD脱硫性能试验"]),
        make_spec("锅炉类型/参数", "text", True, "", "", default="按设计(型号/蒸发量t/h/压力MPa/温度℃)"),
        make_spec("试验标准", "enum", True, "", "", default="GB/T 10184-2015", values=["GB/T 10184-2015(电站锅炉性能试验)","DL/T 5437(火电启动调试)","DL/T 611(锅炉燃烧调整)","ASME PTC 4(锅炉性能)"]),
        make_spec("燃料类型", "enum", True, "", "", default="煤(烟煤)", values=["煤(烟煤)","煤(无烟煤)","煤(褐煤)","天然气","高炉煤气","焦炉煤气","生物质","油","煤矸石"]),
        make_spec("出试验/调试报告", "boolean", False, "", "", default="是(需第三方认证)"),
        make_spec("试验单位资质", "enum", False, "", "", default="调试甲级资质", values=["调试甲级资质","调试乙级资质","锅炉制造厂","电科院"]),
    ],
}

HEATING_SPECS = {}
for r in ["电站锅炉成套设备","汽轮发电机组","汽轮机辅机成套设备","汽轮机附属设备","卸煤设备","煤场设备","碎煤设备","化学水预处理设备","锅炉补给水除盐设备","凝结水精处理设备","循环水处理设备","给水炉水校正处理设备","脱硫设备","脱硝设备","低压锅炉成套设备","低压锅炉辅机设备","燃机余热锅炉辅助设备","三联供锅炉辅助设备"]:
    HEATING_SPECS[r] = [
        make_spec("型号/规格", "text", True, "", "", default="按设计选型"),
        make_spec("技术参数", "text", True, "", "", default="按设计图纸及技术规范"),
        make_spec("材质要求", "text", False, "", "", default="按设计/GB/T 150(压力容器)或GB/T 16507(水管锅炉)/GB/T 16508(锅壳锅炉)"),
        make_spec("设计工况", "text", True, "", "", default="按设计文件"),
        make_spec("执行的制造/检验标准", "text", True, "", "", default="按设备类型对应的国标"),
        make_spec("供货范围", "text", True, "", "", default="按合同技术附件"),
    ]

# ═══════════════════════════
# 完整映射表
# ═══════════════════════════
ALL_SPECS = {}
ALL_SPECS.update(PIPE_SPECS)
ALL_SPECS.update(VALVE_SPECS)
ALL_SPECS.update(CABLE_SPECS)
ALL_SPECS.update(ELEC_SPECS)
ALL_SPECS.update(HVAC_SPECS)
ALL_SPECS.update(FIRE_SPECS)
ALL_SPECS.update(PLUMBING_SPECS)
ALL_SPECS.update(STEEL_SPECS)
ALL_SPECS.update(INSULATION_SPECS)
ALL_SPECS.update(AUXILIARY_SPECS)
ALL_SPECS.update(PAINT_SPECS)
ALL_SPECS.update(MISC_SPECS)
ALL_SPECS.update(MISC2_SPECS)
ALL_SPECS.update(HEATING_SPECS)


def main():
    # Read existing CSV
    with open(SRC_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    print(f"读取 {len(rows)} 条安装物料")

    # Backup
    import shutil
    shutil.copy2(SRC_CSV, BACKUP_CSV)
    print(f"备份 → {BACKUP_CSV}")

    updated = 0
    not_found = []

    for row in rows:
        name = row.get("标准名称", "").strip()
        # Try partial match if exact not found
        if name in ALL_SPECS:
            row["规格模式JSON"] = json.dumps(ALL_SPECS[name], ensure_ascii=False)
            updated += 1
        else:
            # Try fuzzy match
            matched = False
            for key in ALL_SPECS:
                if key in name or name in key:
                    row["规格模式JSON"] = json.dumps(ALL_SPECS[key], ensure_ascii=False)
                    updated += 1
                    matched = True
                    break
            if not matched:
                not_found.append(name)

    # Write back
    fieldnames = list(rows[0].keys())
    with open(DST_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n✅ 已更新 {updated}/{len(rows)} 条物料规格模式")
    if not_found:
        print(f"\n⚠ 未匹配 {len(not_found)} 条:")
        for n in not_found:
            print(f"  - {n}")

    print(f"\n输出 → {DST_CSV}")


if __name__ == "__main__":
    main()
