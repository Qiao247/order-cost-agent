"""作业成本法（ABC）成本动因库与订单动因分析。

依据修订版计划书 4.5 知识库要求新增：
- 作业成本动因库：识别各项成本的驱动动因（作业 → 成本动因 → 动因类型）
- 订单动因分析：把每一笔订单的实际成本按作业动因归集，
  实现"从业务作业层动态精准核算每一笔订单真实成本"。
"""
from typing import List, Dict
from .models import ProductionOrder


# 作业成本动因库：cost_category=成本大类，activity=作业，cost_driver=成本动因，
# driver_type=动因类型（交易动因/时间动因/强度动因），control_focus=管控要点
COST_DRIVER_LIBRARY = [
    {
        "cost_category": "直接材料",
        "cost_item": "原材料采购价格",
        "activity": "采购作业",
        "cost_driver": "采购批次、采购批量",
        "driver_type": "交易动因",
        "control_focus": "合并采购批量、比价寻源、减少紧急采购溢价",
    },
    {
        "cost_category": "直接材料",
        "cost_item": "原材料耗用数量",
        "activity": "领料/裁剪作业",
        "cost_driver": "领料数量（BOM标准用量）",
        "driver_type": "交易动因",
        "control_focus": "推行定额领料、控制工艺损耗与返工、提高套裁利用率",
    },
    {
        "cost_category": "直接人工",
        "cost_item": "工序加工人工",
        "activity": "缝制/组装等加工作业",
        "cost_driver": "标准工时（人工工时）",
        "driver_type": "时间动因",
        "control_focus": "复核工时定额、优化工序节拍、提升工人熟练度",
    },
    {
        "cost_category": "制造费用",
        "cost_item": "水电费",
        "activity": "设备运行作业",
        "cost_driver": "机器工时",
        "driver_type": "时间动因",
        "control_focus": "合理安排开机时长、错峰用电、淘汰高耗能设备",
    },
    {
        "cost_category": "制造费用",
        "cost_item": "机物料消耗",
        "activity": "设备维护作业",
        "cost_driver": "机器工时",
        "driver_type": "时间动因",
        "control_focus": "按设备台时定额领用、做好设备保养降低磨损",
    },
    {
        "cost_category": "制造费用",
        "cost_item": "辅料费",
        "activity": "辅助生产作业",
        "cost_driver": "产品产量",
        "driver_type": "交易动因",
        "control_focus": "按产量定额控制辅料投放、推行替代与回收利用",
    },
    {
        "cost_category": "制造费用",
        "cost_item": "设备折旧",
        "activity": "产能占用作业",
        "cost_driver": "机器工时",
        "driver_type": "时间动因",
        "control_focus": "提高设备利用率摊薄折旧、及时处置闲置产能",
    },
    {
        "cost_category": "制造费用",
        "cost_item": "车间管理人员工资",
        "activity": "车间管理作业",
        "cost_driver": "直接人工工时",
        "driver_type": "时间动因",
        "control_focus": "优化班组编制、控制加班与辅助用工",
    },
    {
        "cost_category": "制造费用",
        "cost_item": "厂房租金分摊",
        "activity": "场地占用作业",
        "cost_driver": "产品产量（或占用面积）",
        "driver_type": "强度动因",
        "control_focus": "提升单位面积产出、清理积压在制品释放场地",
    },
]

# 制造费用名称 → 成本动因（用于订单动因分析；覆盖示例库6项费用）
EXPENSE_DRIVER_MAP = {
    "水电费": ("设备运行作业", "机器工时", "时间动因"),
    "机物料消耗": ("设备维护作业", "机器工时", "时间动因"),
    "辅料费": ("辅助生产作业", "产品产量", "交易动因"),
    "设备折旧": ("产能占用作业", "机器工时", "时间动因"),
    "车间管理人员工资": ("车间管理作业", "直接人工工时", "时间动因"),
    "厂房租金分摊": ("场地占用作业", "产品产量（或占用面积）", "强度动因"),
}


def analyze_order_cost_drivers(order: ProductionOrder) -> List[Dict]:
    """把订单累计实际成本按作业成本动因逐项归集，输出ABC动因分析表。

    体现作业成本法思想：产品消耗作业、作业消耗资源，
    每一笔成本都能回答"由什么作业驱动、按什么动因分配、管控重点是什么"。
    """
    rows: List[Dict] = []
    total_cost = order.actual_cost.total_cost

    def add_row(category, item, amount, activity, driver, driver_type):
        share = round(amount / total_cost * 100, 2) if total_cost > 0 else 0.0
        lib = next((d for d in COST_DRIVER_LIBRARY
                    if d["cost_category"] == category and d["cost_item"] == item), None)
        rows.append({
            "成本大类": category,
            "成本项目": item,
            "对应作业": activity,
            "成本动因": driver,
            "动因类型": driver_type,
            "实际金额(元)": round(amount, 2),
            "占订单成本比重(%)": share,
            "管控要点": lib["control_focus"] if lib else "结合现场作业分析管控",
        })

    for m in order.actual_cost.materials:
        # 材料成本同时受"采购作业（价格）"与"领料作业（用量）"两个作业驱动
        add_row("直接材料", f"{m.material_name}（采购价格）", m.amount,
                "采购作业", "采购批次、采购批量", "交易动因")
    for l in order.actual_cost.labors:
        add_row("直接人工", f"{l.process_name}（{l.work_type}）", l.amount,
                "加工作业", "标准工时（人工工时）", "时间动因")
    for e in order.actual_cost.expenses:
        activity, driver, dtype = EXPENSE_DRIVER_MAP.get(
            e.expense_name, ("其他作业", "按费用项目确定", "交易动因"))
        add_row("制造费用", e.expense_name, e.allocated_amount or e.amount,
                activity, driver, dtype)

    rows.sort(key=lambda r: r["实际金额(元)"], reverse=True)
    return rows
