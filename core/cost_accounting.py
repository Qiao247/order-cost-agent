from typing import Dict, Optional
from .models import (
    ProductionOrder, StandardCost, ActualCost,
    MaterialItem, LaborItem, ExpenseItem, CostVariance
)


class CostAccountingEngine:
    @staticmethod
    def calculate_standard_cost(order: ProductionOrder) -> StandardCost:
        return order.standard_cost

    @staticmethod
    def standard_cost_to_date(order: ProductionOrder) -> StandardCost:
        """按当前完工进度生成可与累计实际成本比较的同期标准成本。"""
        cr = max(0.0, min(1.0, order.completion_rate))
        current = StandardCost()
        for m in order.standard_cost.materials:
            current.materials.append(MaterialItem(
                material_code=m.material_code,
                material_name=m.material_name,
                spec=m.spec,
                unit=m.unit,
                qty=round(m.qty * cr, 4),
                unit_price=m.unit_price,
                amount=round(m.amount * cr, 2),
            ))
        for labor in order.standard_cost.labors:
            current.labors.append(LaborItem(
                process_name=labor.process_name,
                work_type=labor.work_type,
                hours=round(labor.hours * cr, 4),
                hourly_rate=labor.hourly_rate,
                amount=round(labor.amount * cr, 2),
            ))
        for expense in order.standard_cost.expenses:
            amount = round(expense.amount * cr, 2)
            current.expenses.append(ExpenseItem(
                expense_type=expense.expense_type,
                expense_name=expense.expense_name,
                allocation_base=expense.allocation_base,
                amount=amount,
                allocated_amount=amount,
            ))
        return current

    @staticmethod
    def project_final_cost(actual_cost: ActualCost, completion_rate: float) -> ActualCost:
        if completion_rate <= 0:
            return actual_cost
        cr = max(0.01, min(1.0, completion_rate))
        projected = ActualCost()
        for m in actual_cost.materials:
            proj_qty = m.qty / cr
            proj_amt = m.amount / cr
            projected.materials.append(MaterialItem(
                material_code=m.material_code,
                material_name=m.material_name,
                spec=m.spec,
                unit=m.unit,
                qty=round(proj_qty, 4),
                unit_price=m.unit_price,
                amount=round(proj_amt, 2)
            ))
        for l in actual_cost.labors:
            proj_hours = l.hours / cr
            proj_amt = l.amount / cr
            projected.labors.append(LaborItem(
                process_name=l.process_name,
                work_type=l.work_type,
                hours=round(proj_hours, 4),
                hourly_rate=l.hourly_rate,
                amount=round(proj_amt, 2)
            ))
        for e in actual_cost.expenses:
            proj_amt = e.amount / cr
            projected.expenses.append(ExpenseItem(
                expense_type=e.expense_type,
                expense_name=e.expense_name,
                allocation_base=e.allocation_base,
                amount=round(proj_amt, 2),
                allocated_amount=round(proj_amt, 2)
            ))
        return projected

    @staticmethod
    def expected_profit(order: ProductionOrder) -> Optional[float]:
        """返回管理预测利润；完工率为0且已有异常成本时不强行外推。"""
        if order.actual_cost.total_cost <= 0:
            return order.standard_profit
        if order.completion_rate >= 1.0:
            return round(order.sales_amount - order.actual_cost.total_cost, 2)
        if order.completion_rate <= 0:
            return None
        projected = CostAccountingEngine.project_final_cost(
            order.actual_cost, order.completion_rate
        )
        return round(order.sales_amount - projected.total_cost, 2)

    @staticmethod
    def unit_cost_analysis(order: ProductionOrder) -> Dict:
        qty = max(order.order_qty, 1)
        # 未完工订单的实际成本只是累计发生额，先按完工率折算为完工总成本口径，避免毛利率虚高
        act = order.actual_cost
        if act.total_cost <= 0:
            act = order.standard_cost
        elif order.completion_rate < 1.0 and act.total_cost > 0:
            act = CostAccountingEngine.project_final_cost(act, order.completion_rate)
        expected_profit = CostAccountingEngine.expected_profit(order)
        expected_margin = (
            round(expected_profit / order.sales_amount * 100, 2)
            if expected_profit is not None and order.sales_amount > 0
            else None
        )
        projected_margin = (
            round((order.sales_price - act.total_cost / qty) / order.sales_price * 100, 2)
            if order.sales_price > 0 else None
        )
        return {
            "standard_unit_cost": round(order.standard_cost.total_cost / qty, 4),
            "actual_unit_cost": round(act.total_cost / qty, 4),
            "cumulative_actual_unit_cost": round(order.actual_cost.total_cost / qty, 4),
            "standard_material_unit": round(order.standard_cost.total_material_cost / qty, 4),
            "actual_material_unit": round(act.total_material_cost / qty, 4),
            "standard_labor_unit": round(order.standard_cost.total_labor_cost / qty, 4),
            "actual_labor_unit": round(act.total_labor_cost / qty, 4),
            "standard_expense_unit": round(order.standard_cost.total_expense_cost / qty, 4),
            "actual_expense_unit": round(act.total_expense_cost / qty, 4),
            "gross_margin_rate_standard": round(
                (order.sales_price - order.standard_cost.total_cost / qty) / order.sales_price * 100, 2
            ) if order.sales_price > 0 else 0,
            "gross_margin_rate_actual": projected_margin if order.is_actual_profit_available else None,
            "gross_margin_rate_projected": projected_margin,
            "expected_profit": expected_profit,
            "expected_margin_rate": expected_margin,
            "actual_profit": order.actual_profit,
            "cost_basis": (
                "正式实际"
                if order.is_actual_profit_available
                else "预计完工"
                if 0 < order.completion_rate < 1 and order.actual_cost.total_cost > 0
                else "完工待审批"
                if order.completion_rate >= 1 and order.actual_cost.total_cost > 0
                else "标准预算"
                if order.actual_cost.total_cost <= 0
                else "累计发生"
            ),
        }
