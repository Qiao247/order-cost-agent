"""运筹学 - 订单投产优先级子模块（经营决策智能体内部子模块）。

依据修订版计划书核心修订原则：
- 不新增独立排产Agent，作为经营决策智能体内部子模块；
- 一期原型仅输出"订单投产优先级结构化参考表"，供经营决策参考，
  不下发生产工单、不直接控制车间设备；
- 完整运筹约束求解（设备/人员/物料约束）属于远期迭代。

评分模型：多因子加权评分（0-100），因子构成——
  预计毛利率 30%：订单盈利能力，产出效率优先；
  交期紧迫度 25%：距计划交付剩余天数与剩余完工量；
  成本控制  20%：同期成本差异率越低得分越高；
  进度合理性 15%：时间进度与实际完工率的偏离；
  投入产出比 10%：订单收入 / 已投入成本。
"""
from datetime import datetime
from typing import List, Dict, Optional

from .models import ProductionOrder, OrderStatus
from .cost_accounting import CostAccountingEngine
from .variance_analysis import VarianceAnalyzer

WEIGHTS = {
    "margin": 0.30,        # 预计毛利率
    "urgency": 0.25,       # 交期紧迫度
    "cost_control": 0.20,  # 成本控制
    "progress": 0.15,      # 进度合理性
    "roi": 0.10,           # 投入产出比
}

FACTOR_LABELS = {
    "margin": "毛利率因子",
    "urgency": "交期因子",
    "cost_control": "成本控制因子",
    "progress": "进度因子",
    "roi": "投入产出因子",
}


def _scale(value: float, low: float, high: float, invert: bool = False) -> float:
    """把 value 线性映射到 [0,100]；low/high 为饱和区间端点。"""
    if high <= low:
        return 50.0
    ratio = (value - low) / (high - low)
    ratio = min(1.0, max(0.0, ratio))
    if invert:
        ratio = 1.0 - ratio
    return round(ratio * 100, 1)


def _urgency_score(order: ProductionOrder) -> float:
    """交期紧迫度：越接近交付且剩余完工量越大，分越高；逾期未完成=100。"""
    if order.status in (OrderStatus.COMPLETED, OrderStatus.DELIVERED):
        return 0.0
    if not order.plan_delivery_date:
        return 30.0
    try:
        today = datetime.now().date()
        plan = datetime.strptime(order.plan_delivery_date, "%Y-%m-%d").date()
        days_left = (plan - today).days
    except Exception:
        return 30.0
    remaining = max(0.0, 1.0 - order.completion_rate)
    if days_left < 0 and remaining > 0:
        return 100.0  # 已逾期未完成，最优先
    if remaining <= 0:
        return 0.0
    # 每天需要完成的剩余量越大越紧迫（日完工需求率 0→0分，≥0.10→100分，
    # 即"10天内必须完工"视为最紧迫——贴合劳保企业 15-30 天的订单周期）
    daily_need = remaining / max(days_left, 1)
    return _scale(daily_need, 0.0, 0.10)


def _cost_control_score(order: ProductionOrder) -> float:
    """成本控制：同期总成本差异率，超支越多分越低。"""
    std = CostAccountingEngine.standard_cost_to_date(order).total_cost
    act = order.actual_cost.total_cost
    if std <= 0:
        return 60.0
    rate = (act - std) / std * 100
    return _scale(rate, -5.0, 15.0, invert=True)


def _progress_score(order: ProductionOrder) -> float:
    """进度合理性：时间进度 - 完工率 的落后幅度。"""
    if not order.start_date or order.status in (OrderStatus.COMPLETED, OrderStatus.DELIVERED):
        return 70.0
    try:
        today = datetime.now().date()
        start = datetime.strptime(order.start_date, "%Y-%m-%d").date()
        plan_end = datetime.strptime(order.plan_delivery_date, "%Y-%m-%d").date()
        total_days = max((plan_end - start).days, 1)
        elapsed = max((today - start).days, 0)
    except Exception:
        return 70.0
    gap = min(1.0, elapsed / total_days) - order.completion_rate
    return _scale(gap, -0.05, 0.25, invert=True)


def _roi(order: ProductionOrder) -> float:
    """整单预计投入产出比 = 订单收入 / 整单标准成本。

    用整单口径而非"已投入成本"：已投入额随完工率累积，
    早期订单会被人为放大 ROI，导致全场归一化失真。
    """
    invested = order.standard_cost.total_cost or order.actual_cost.total_cost
    if invested <= 0:
        return 0.0
    return float(order.sales_amount) / invested


def score_order(order: ProductionOrder,
                margin_range: Optional[tuple] = None,
                roi_range: Optional[tuple] = None) -> Dict:
    """计算单个订单的投产优先级评分。margin_range/roi_range 用于全场归一化。"""
    unit_ana = CostAccountingEngine.unit_cost_analysis(order)
    margin = unit_ana.get("expected_margin_rate")
    if margin is None:
        margin = unit_ana["gross_margin_rate_standard"]

    if margin_range and margin_range[1] > margin_range[0]:
        m_score = _scale(margin, margin_range[0], margin_range[1])
    else:
        m_score = _scale(margin, 0.0, 30.0)

    r = _roi(order)
    if roi_range and roi_range[1] > roi_range[0]:
        r_score = _scale(r, roi_range[0], roi_range[1])
    else:
        r_score = _scale(r, 1.0, 1.6)

    factors = {
        "margin": m_score,
        "urgency": _urgency_score(order),
        "cost_control": _cost_control_score(order),
        "progress": _progress_score(order),
        "roi": r_score,
    }
    total = round(sum(WEIGHTS[k] * factors[k] for k in WEIGHTS), 1)
    return {
        "margin_rate": margin,
        "roi": round(r, 2),
        "factors": factors,
        "total": total,
    }


def rank_orders(orders: List[ProductionOrder]) -> List[Dict]:
    """生成订单投产优先级结构化参考表（按总分降序）。

    已完工/已交付订单标记"无需投产"不参与排序竞争；
    未开工/生产中订单给出投产建议批次。
    """
    pending = [o for o in orders if o.status not in (OrderStatus.COMPLETED, OrderStatus.DELIVERED)]
    finished = [o for o in orders if o.status in (OrderStatus.COMPLETED, OrderStatus.DELIVERED)]

    margins = []
    rois = []
    for o in pending:
        s = score_order(o)
        margins.append(s["margin_rate"])
        rois.append(s["roi"])
    margin_range = (min(margins), max(margins)) if margins else None
    roi_range = (min(rois), max(rois)) if rois else None

    # 先统一用同一套全场归一化区间打分，再按总分降序排序，保证排序与展示一致
    scored = [(o, score_order(o, margin_range, roi_range)) for o in pending]
    scored.sort(key=lambda pair: pair[1]["total"], reverse=True)

    rows = []
    rank = 0
    for o, s in scored:
        rank += 1
        total = s["total"]
        if total >= 70:
            advice = "优先投产"
        elif total >= 52:
            advice = "正常排产"
        else:
            advice = "暂缓投产（先复盘）"
        days_left = ""
        try:
            dl = (datetime.strptime(o.plan_delivery_date, "%Y-%m-%d").date() - datetime.now().date()).days
            days_left = f"{dl}天" if dl >= 0 else f"逾期{-dl}天"
        except Exception:
            pass
        rows.append({
            "优先级": str(rank),
            "订单号": o.order_no,
            "产品名称": o.product_name,
            "客户": o.customer_name,
            "订单状态": o.status.value,
            "完工率": f"{o.completion_rate*100:.0f}%",
            "预计毛利率(%)": s["margin_rate"],
            "交期剩余": days_left,
            "综合评分": str(total),
            "毛利率因子": str(s["factors"]["margin"]),
            "交期因子": str(s["factors"]["urgency"]),
            "成本控制因子": str(s["factors"]["cost_control"]),
            "进度因子": str(s["factors"]["progress"]),
            "投入产出因子": str(s["factors"]["roi"]),
            "投产建议": advice,
            "备注": "",
        })
    for o in finished:
        rows.append({
            "优先级": "—",
            "订单号": o.order_no,
            "产品名称": o.product_name,
            "客户": o.customer_name,
            "订单状态": o.status.value,
            "完工率": f"{o.completion_rate*100:.0f}%",
            "预计毛利率(%)": CostAccountingEngine.unit_cost_analysis(o).get("expected_margin_rate"),
            "交期剩余": "—",
            "综合评分": "—",
            "毛利率因子": "—",
            "交期因子": "—",
            "成本控制因子": "—",
            "进度因子": "—",
            "投入产出因子": "—",
            "投产建议": "无需投产",
            "备注": "已完工/已交付",
        })
    return rows
