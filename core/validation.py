import math
from datetime import datetime
from typing import Iterable, List

from .models import AccountingStatus, OrderStatus, ProductionOrder


class OrderValidationError(ValueError):
    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__("；".join(self.errors))


def _finite_nonnegative(value, field_name: str, errors: List[str]) -> None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        errors.append(f"{field_name}必须是数字")
        return
    if not math.isfinite(numeric):
        errors.append(f"{field_name}必须是有限数字")
    elif numeric < 0:
        errors.append(f"{field_name}不能为负数")


def _validate_dates(order: ProductionOrder, errors: List[str]) -> None:
    parsed = {}
    for name, value in (
        ("开工日期", order.start_date),
        ("计划交付日期", order.plan_delivery_date),
        ("实际交付日期", order.actual_delivery_date),
    ):
        if not value:
            if name != "实际交付日期":
                errors.append(f"{name}不能为空")
            continue
        try:
            parsed[name] = datetime.strptime(value, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            errors.append(f"{name}必须采用YYYY-MM-DD格式")
    if parsed.get("开工日期") and parsed.get("计划交付日期"):
        if parsed["计划交付日期"] < parsed["开工日期"]:
            errors.append("计划交付日期不能早于开工日期")
    if order.status == OrderStatus.DELIVERED and not order.actual_delivery_date:
        errors.append("已交付订单必须填写实际交付日期")


def _validate_cost_lines(order: ProductionOrder, errors: List[str]) -> None:
    for label, cost in (("标准成本", order.standard_cost), ("实际成本", order.actual_cost)):
        material_codes = [m.material_code for m in cost.materials if m.material_code]
        duplicates = sorted({code for code in material_codes if material_codes.count(code) > 1})
        if duplicates:
            errors.append(f"{label}存在重复物料编码：{','.join(duplicates)}")

        labor_names = [item.process_name for item in cost.labors if item.process_name]
        duplicates = sorted({name for name in labor_names if labor_names.count(name) > 1})
        if duplicates:
            errors.append(f"{label}存在重复工序：{','.join(duplicates)}")

        expense_names = [item.expense_name for item in cost.expenses if item.expense_name]
        duplicates = sorted({name for name in expense_names if expense_names.count(name) > 1})
        if duplicates:
            errors.append(f"{label}存在重复制造费用项目：{','.join(duplicates)}")

        for index, item in enumerate(cost.materials, 1):
            prefix = f"{label}材料第{index}行"
            _finite_nonnegative(item.qty, f"{prefix}数量", errors)
            _finite_nonnegative(item.unit_price, f"{prefix}单价", errors)
            _finite_nonnegative(item.amount, f"{prefix}金额", errors)
            expected = round(item.qty * item.unit_price, 2)
            if abs(item.amount - expected) > max(0.02, abs(expected) * 0.001):
                errors.append(f"{prefix}金额与数量×单价不一致")

        for index, item in enumerate(cost.labors, 1):
            prefix = f"{label}人工第{index}行"
            _finite_nonnegative(item.hours, f"{prefix}工时", errors)
            _finite_nonnegative(item.hourly_rate, f"{prefix}工资率", errors)
            _finite_nonnegative(item.amount, f"{prefix}金额", errors)
            expected = round(item.hours * item.hourly_rate, 2)
            if abs(item.amount - expected) > max(0.02, abs(expected) * 0.001):
                errors.append(f"{prefix}金额与工时×工资率不一致")

        for index, item in enumerate(cost.expenses, 1):
            prefix = f"{label}制造费用第{index}行"
            _finite_nonnegative(item.amount, f"{prefix}金额", errors)
            _finite_nonnegative(item.allocated_amount, f"{prefix}已分配金额", errors)


def validate_order(order: ProductionOrder) -> None:
    errors: List[str] = []
    if not order.order_no or not order.order_no.strip():
        errors.append("订单号不能为空")
    if not order.product_name or not order.product_name.strip():
        errors.append("产品名称不能为空")

    _finite_nonnegative(order.order_qty, "订单数量", errors)
    if isinstance(order.order_qty, (int, float)) and order.order_qty <= 0:
        errors.append("订单数量必须大于0")
    _finite_nonnegative(order.sales_price, "销售单价", errors)
    try:
        completion_rate = float(order.completion_rate)
        if not math.isfinite(completion_rate) or not 0 <= completion_rate <= 1:
            errors.append("完工率必须在0到1之间")
    except (TypeError, ValueError):
        completion_rate = None
        errors.append("完工率必须是0到1之间的数字")

    if completion_rate is not None and order.status == OrderStatus.NOT_STARTED and completion_rate != 0:
        errors.append("未开工订单的完工率必须为0")
    elif completion_rate is not None and order.status == OrderStatus.IN_PROGRESS and not 0 < completion_rate < 1:
        errors.append("生产中订单的完工率必须大于0且小于1")
    elif completion_rate is not None and order.status in (OrderStatus.COMPLETED, OrderStatus.DELIVERED) and completion_rate != 1:
        errors.append("已完工或已交付订单的完工率必须为100%")

    if order.accounting_status in (
        AccountingStatus.ACCOUNTING_APPROVED,
        AccountingStatus.FORMAL_OUTPUT,
    ) and not (
        order.status in (OrderStatus.COMPLETED, OrderStatus.DELIVERED)
        and completion_rate == 1
    ):
        errors.append("只有100%完工的订单才能进入会计已审批或正式输出状态")

    if order.source_mode not in ("示例/仿真", "实际业务"):
        errors.append("数据来源必须为“示例/仿真”或“实际业务”")

    if order.standard_cost.total_cost <= 0:
        errors.append("制造订单的标准总成本必须大于0")
    if order.status == OrderStatus.NOT_STARTED and order.actual_cost.total_cost > 0:
        errors.append("未开工订单不能存在累计实际成本")

    _validate_dates(order, errors)
    _validate_cost_lines(order, errors)
    if errors:
        raise OrderValidationError(errors)
