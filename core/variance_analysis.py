from typing import List, Dict, Tuple
from .models import ProductionOrder, CostVariance, WarningLevel, WasteType
from .cost_accounting import CostAccountingEngine


# 每条差异原因附带浪费类型标签：工艺性浪费=生产作业过程产生；管理性浪费=管理控制缺陷产生
MATERIAL_CAUSES = {
    "price_high": (WasteType.MANAGEMENT, "材料采购价格高于标准（管理性浪费），可能原因：市场价格上涨、供应商议价能力弱、紧急采购溢价、采购批量偏小"),
    "price_low": (None, "材料采购价格低于标准，可能原因：市场价格下跌、批量采购优惠、更换低价供应商、材料等级降低"),
    "qty_high": (WasteType.PROCESS, "材料用量超标（工艺性浪费），可能原因：工艺损耗增加、次品返工、工人操作不熟练、材料质量差导致浪费"),
    "qty_low": (None, "材料用量节约，可能原因：工艺改进、工人熟练、边角料利用、实际用料比标准保守")
}

LABOR_CAUSES = {
    "rate_high": (WasteType.MANAGEMENT, "人工工资率高于标准（管理性浪费），可能原因：使用高级技工、加班工资、工资调整"),
    "rate_low": (None, "人工工资率低于标准，可能原因：使用学徒工、计件工资效率高"),
    "hours_high": (WasteType.PROCESS, "工时消耗超标（工艺性浪费），可能原因：工序安排不合理、设备故障、工人熟练度低、产品返工"),
    "hours_low": (None, "工时节约，可能原因：工艺优化、设备更新、工人熟练度提升")
}

EXPENSE_CAUSES = {
    "high": (WasteType.MANAGEMENT, "制造费用超支（管理性浪费），可能原因：设备折旧增加、水电费上涨、维修费用超预算、车间管理不善"),
    "low": (None, "制造费用节约，可能原因：费用控制有效、产能利用率提升、间接人工优化")
}


def pick_waste_type(components: Dict[str, float],
                    type_by_component: Dict[str, WasteType],
                    fallback: WasteType) -> str:
    """按超支贡献金额较大的差异分量确定浪费类型；无超支分量为空。"""
    over = {k: v for k, v in components.items() if v > 0 and k in type_by_component}
    if not over:
        return fallback.value if components and any(v > 0 for v in components.values()) and fallback else ""
    best = max(over, key=lambda k: over[k])
    return type_by_component[best].value


class VarianceAnalyzer:
    def analyze_all_variances(self, order: ProductionOrder) -> List[CostVariance]:
        variances = []
        variances.extend(self._analyze_material_variances(order))
        variances.extend(self._analyze_labor_variances(order))
        variances.extend(self._analyze_expense_variances(order))
        variances.extend(self._analyze_total_variances(order))
        order.variances = variances
        return variances

    @staticmethod
    def summarize_waste(order: ProductionOrder) -> Dict[str, float]:
        """按浪费根源二级分类汇总订单超支金额（不含合计行）。"""
        summary = {"工艺性浪费": 0.0, "管理性浪费": 0.0, "未归类超支": 0.0}
        for v in order.variances:
            if v.variance_amount > 0 and not v.item_name.startswith("【"):
                if v.waste_type in summary:
                    summary[v.waste_type] += v.variance_amount
                else:
                    summary["未归类超支"] += v.variance_amount
        return summary

    def _analyze_material_variances(self, order: ProductionOrder) -> List[CostVariance]:
        result = []
        comparison_standard = CostAccountingEngine.standard_cost_to_date(order)
        std_mats = {m.material_code: m for m in comparison_standard.materials}
        act_mats = {m.material_code: m for m in order.actual_cost.materials}
        all_codes = set(list(std_mats.keys()) + list(act_mats.keys()))

        for code in all_codes:
            std = std_mats.get(code)
            act = act_mats.get(code)
            name = std.material_name if std else (act.material_name if act else code)
            std_amt = std.amount if std else 0.0
            act_amt = act.amount if act else 0.0
            var = round(act_amt - std_amt, 2)
            # 标准金额为0时无法计算差异率，置0并由展示层显示为"—"
            rate = round(var / std_amt * 100, 2) if std_amt != 0 else 0.0

            causes = []
            suggestions = []
            components = {}
            waste_type = ""
            if std and act:
                price_diff = act.unit_price - std.unit_price
                qty_diff = act.qty - std.qty
                components = {
                    "价格差异": round(act.qty * price_diff, 2),
                    "用量差异": round(qty_diff * std.unit_price, 2),
                }
                if std.unit_price == 0 and act.unit_price > 0:
                    causes.append("标准价格为0但实际发生采购价格，属标准定额缺失（管理性浪费），需补充或修订价格标准")
                    suggestions.append("核查BOM价格标准和采购批次依据。")
                    waste_type = WasteType.MANAGEMENT.value
                elif std.unit_price != 0 and abs(price_diff / std.unit_price) > 0.03:
                    wt, msg = MATERIAL_CAUSES["price_high" if price_diff > 0 else "price_low"]
                    causes.append(msg)
                    suggestions.append(
                        "核查采购价格和供应商依据。"
                        if price_diff > 0 else "确认低价原因及材料质量是否可持续。"
                    )
                    if price_diff > 0:
                        waste_type = wt.value
                if std.qty == 0 and act.qty > 0:
                    causes.append("同期标准用量为0但发生领料，属用量定额缺失（管理性浪费），需核查提前领料或无定额领料")
                    suggestions.append("核对领料时点、订单归属和BOM用量标准。")
                    if not waste_type:
                        waste_type = WasteType.MANAGEMENT.value
                elif std.qty != 0 and abs(qty_diff / std.qty) > 0.03:
                    wt, msg = MATERIAL_CAUSES["qty_high" if qty_diff > 0 else "qty_low"]
                    causes.append(msg)
                    suggestions.append(
                        "检查领退料记录、损耗和返工情况。"
                        if qty_diff > 0 else "总结节约原因并评估是否修订用量标准。"
                    )
                    if qty_diff > 0 and not waste_type:
                        waste_type = wt.value
                # 价格与用量同时超支时，按超支贡献金额较大的分量归类
                if not waste_type and act_amt > std_amt:
                    waste_type = pick_waste_type(
                        components,
                        {"价格差异": WasteType.MANAGEMENT, "用量差异": WasteType.PROCESS},
                        None,
                    )

            cause = "；".join(causes)
            suggestion = "建议：" + " ".join(suggestions) if suggestions else ""
            # 浪费类型仅描述净超支的来源：整体节约（价格下跌掩盖用量超支等）不标注
            if var <= 0:
                waste_type = ""

            result.append(CostVariance(
                category="直接材料",
                item_name=name,
                standard_amount=std_amt,
                actual_amount=act_amt,
                variance_amount=var,
                variance_rate=rate,
                cause_analysis=cause,
                suggestion=suggestion,
                components=components,
                waste_type=waste_type,
            ))

        std_total = comparison_standard.total_material_cost
        act_total = order.actual_cost.total_material_cost
        var_total = round(act_total - std_total, 2)
        rate_total = round(var_total / std_total * 100, 2) if std_total != 0 else 0
        result.append(CostVariance(
            category="直接材料",
            item_name="【材料成本合计】",
            standard_amount=std_total,
            actual_amount=act_total,
            variance_amount=var_total,
            variance_rate=rate_total,
            cause_analysis="综合材料价格差异与数量差异影响",
            suggestion="详见各项材料明细分析"
        ))
        return result

    def _analyze_labor_variances(self, order: ProductionOrder) -> List[CostVariance]:
        result = []
        comparison_standard = CostAccountingEngine.standard_cost_to_date(order)
        # 按工序名称匹配标准与实际，避免按下标错配
        std_labors = {l.process_name: l for l in comparison_standard.labors}
        act_labors = {l.process_name: l for l in order.actual_cost.labors}
        all_names = list(std_labors.keys())
        for name in act_labors:
            if name not in std_labors:
                all_names.append(name)

        for name in all_names:
            std = std_labors.get(name)
            act = act_labors.get(name)
            std_amt = std.amount if std else 0.0
            act_amt = act.amount if act else 0.0
            var = round(act_amt - std_amt, 2)
            rate = round(var / std_amt * 100, 2) if std_amt != 0 else 0.0

            causes = []
            suggestions = []
            components = {}
            waste_type = ""
            if std and act:
                rate_diff = act.hourly_rate - std.hourly_rate
                hours_diff = act.hours - std.hours
                components = {
                    "工资率差异": round(act.hours * rate_diff, 2),
                    "效率差异": round(hours_diff * std.hourly_rate, 2),
                }
                if std.hourly_rate == 0 and act.hourly_rate > 0:
                    causes.append("标准工资率为0但实际发生人工成本，属工资率标准缺失（管理性浪费），需补充工资率标准")
                    suggestions.append("核查工种、工资率标准和加班口径。")
                    waste_type = WasteType.MANAGEMENT.value
                elif std.hourly_rate != 0 and abs(rate_diff / std.hourly_rate) > 0.03:
                    wt, msg = LABOR_CAUSES["rate_high" if rate_diff > 0 else "rate_low"]
                    causes.append(msg)
                    suggestions.append("优化排班并核对工资率标准。")
                    if rate_diff > 0:
                        waste_type = wt.value
                if std.hours == 0 and act.hours > 0:
                    causes.append("同期标准工时为0但发生工时，属工艺定额缺失（管理性浪费），需核查提前报工或无工艺定额报工")
                    suggestions.append("核对报工时点、订单归属和工艺路线。")
                    if not waste_type:
                        waste_type = WasteType.MANAGEMENT.value
                elif std.hours != 0 and abs(hours_diff / std.hours) > 0.03:
                    wt, msg = LABOR_CAUSES["hours_high" if hours_diff > 0 else "hours_low"]
                    causes.append(msg)
                    suggestions.append("分析工序效率并复核工时定额。")
                    if hours_diff > 0 and not waste_type:
                        waste_type = wt.value
                # 工资率与效率同时超支时，按超支贡献金额较大的分量归类
                if not waste_type and act_amt > std_amt:
                    waste_type = pick_waste_type(
                        components,
                        {"工资率差异": WasteType.MANAGEMENT, "效率差异": WasteType.PROCESS},
                        None,
                    )

            cause = "；".join(causes)
            suggestion = "建议：" + " ".join(suggestions) if suggestions else ""
            # 浪费类型仅描述净超支的来源：整体节约不标注
            if var <= 0:
                waste_type = ""

            result.append(CostVariance(
                category="直接人工",
                item_name=name,
                standard_amount=std_amt,
                actual_amount=act_amt,
                variance_amount=var,
                variance_rate=rate,
                cause_analysis=cause,
                suggestion=suggestion,
                components=components,
                waste_type=waste_type,
            ))

        std_total = comparison_standard.total_labor_cost
        act_total = order.actual_cost.total_labor_cost
        var_total = round(act_total - std_total, 2)
        rate_total = round(var_total / std_total * 100, 2) if std_total != 0 else 0
        result.append(CostVariance(
            category="直接人工",
            item_name="【人工成本合计】",
            standard_amount=std_total,
            actual_amount=act_total,
            variance_amount=var_total,
            variance_rate=rate_total,
            cause_analysis="综合工资率差异与工时差异影响",
            suggestion="详见各工序人工明细分析"
        ))
        return result

    def _analyze_expense_variances(self, order: ProductionOrder) -> List[CostVariance]:
        result = []
        comparison_standard = CostAccountingEngine.standard_cost_to_date(order)
        # 按费用名称匹配标准与实际，避免按下标错配
        std_expenses = {e.expense_name: e for e in comparison_standard.expenses}
        act_expenses = {e.expense_name: e for e in order.actual_cost.expenses}
        all_names = list(std_expenses.keys())
        for name in act_expenses:
            if name not in std_expenses:
                all_names.append(name)

        for name in all_names:
            std = std_expenses.get(name)
            act = act_expenses.get(name)
            std_amt = std.amount if std else 0.0
            act_amt = act.amount if act else 0.0
            var = round(act_amt - std_amt, 2)
            rate = round(var / std_amt * 100, 2) if std_amt != 0 else 0.0

            cause = ""
            suggestion = ""
            waste_type = ""
            if act:
                if var > 0 and rate > 5:
                    cause = EXPENSE_CAUSES["high"][1]
                    waste_type = WasteType.MANAGEMENT.value
                    suggestion = "建议：检查费用明细，逐项分析超支原因；加强费用预算管控。"
                elif var < 0 and rate < -5:
                    cause = EXPENSE_CAUSES["low"][1]
                    suggestion = "建议：评估费用节约是否影响产品质量；考虑优化费用分配标准。"
                elif var > 0 and std_amt == 0:
                    waste_type = WasteType.MANAGEMENT.value

            result.append(CostVariance(
                category="制造费用",
                item_name=name,
                standard_amount=std_amt,
                actual_amount=act_amt,
                variance_amount=var,
                variance_rate=rate,
                cause_analysis=cause,
                suggestion=suggestion,
                waste_type=waste_type,
            ))

        std_total = comparison_standard.total_expense_cost
        act_total = order.actual_cost.total_expense_cost
        var_total = round(act_total - std_total, 2)
        rate_total = round(var_total / std_total * 100, 2) if std_total != 0 else 0
        result.append(CostVariance(
            category="制造费用",
            item_name="【制造费用合计】",
            standard_amount=std_total,
            actual_amount=act_total,
            variance_amount=var_total,
            variance_rate=rate_total,
            cause_analysis="综合变动制造费用与固定制造费用差异",
            suggestion="详见各项费用明细分析"
        ))
        return result

    def _analyze_total_variances(self, order: ProductionOrder) -> List[CostVariance]:
        comparison_standard = CostAccountingEngine.standard_cost_to_date(order)
        std_total = comparison_standard.total_cost
        act_total = order.actual_cost.total_cost
        var_total = round(act_total - std_total, 2)
        rate_total = round(var_total / std_total * 100, 2) if std_total != 0 else 0

        cause_parts = []
        mat_var = order.actual_cost.total_material_cost - comparison_standard.total_material_cost
        lab_var = order.actual_cost.total_labor_cost - comparison_standard.total_labor_cost
        exp_var = order.actual_cost.total_expense_cost - comparison_standard.total_expense_cost
        if abs(mat_var) >= abs(lab_var) and abs(mat_var) >= abs(exp_var):
            cause_parts.append("材料成本差异是主因")
        elif abs(lab_var) >= abs(exp_var):
            cause_parts.append("人工成本差异是主因")
        else:
            cause_parts.append("制造费用差异是主因")

        return [CostVariance(
            category="总成本",
            item_name="【订单总成本】",
            standard_amount=std_total,
            actual_amount=act_total,
            variance_amount=var_total,
            variance_rate=rate_total,
            cause_analysis="；".join(cause_parts),
            suggestion="请根据各成本项目明细差异采取针对性措施。"
        )]


class WarningEngine:
    THRESHOLDS = {
        "variance_rate_attention": 3.0,
        "variance_rate_warning": 8.0,
        "variance_rate_critical": 15.0,
        "margin_rate_attention": 15.0,
        "margin_rate_warning": 8.0,
        "margin_rate_critical": 3.0,
        "completion_warning_days": 3,
    }

    def generate_warnings(self, order: ProductionOrder) -> List[Dict]:
        warnings = []
        analyzer = VarianceAnalyzer()
        if not order.variances:
            analyzer.analyze_all_variances(order)

        warnings.extend(self._variance_warnings(order))
        warnings.extend(self._profit_margin_warnings(order))
        warnings.extend(self._delivery_warnings(order))
        warnings.extend(self._completion_warnings(order))

        order.warnings = warnings
        return warnings

    def _variance_warnings(self, order: ProductionOrder) -> List[Dict]:
        result = []
        for v in order.variances:
            if v.item_name.startswith("【"):
                continue
            abs_rate = abs(v.variance_rate)
            level = None
            if v.variance_amount > 0:
                if v.standard_amount == 0:
                    # 标准金额为0却发生实际支出，无法用差异率衡量，直接预警
                    level = WarningLevel.WARNING
                elif abs_rate >= self.THRESHOLDS["variance_rate_critical"]:
                    level = WarningLevel.CRITICAL
                elif abs_rate >= self.THRESHOLDS["variance_rate_warning"]:
                    level = WarningLevel.WARNING
                elif abs_rate >= self.THRESHOLDS["variance_rate_attention"]:
                    level = WarningLevel.ATTENTION
            if level:
                if v.standard_amount == 0:
                    message = f"{v.category}-{v.item_name} 标准金额为0，但实际发生支出 ¥{v.variance_amount:,.2f}"
                else:
                    message = f"{v.category}-{v.item_name} 超支 {v.variance_rate:.2f}%（超支额 ¥{v.variance_amount:,.2f}）"
                result.append({
                    "type": "成本差异预警",
                    "level": level.value,
                    "category": v.category,
                    "item": v.item_name,
                    "message": message,
                    "suggestion": v.suggestion,
                    "variance_rate": v.variance_rate,
                    "variance_amount": v.variance_amount
                })
        return result

    def _profit_margin_warnings(self, order: ProductionOrder) -> List[Dict]:
        result = []
        unit_analysis = CostAccountingEngine.unit_cost_analysis(order)
        margin = unit_analysis.get("expected_margin_rate")
        margin_label = "正式实际毛利率" if order.is_actual_profit_available else "预计毛利率"
        if margin is None:
            margin = unit_analysis["gross_margin_rate_standard"]
            margin_label = "标准毛利率"

        level = None
        if margin <= self.THRESHOLDS["margin_rate_critical"]:
            level = WarningLevel.CRITICAL
        elif margin <= self.THRESHOLDS["margin_rate_warning"]:
            level = WarningLevel.WARNING
        elif margin <= self.THRESHOLDS["margin_rate_attention"]:
            level = WarningLevel.ATTENTION

        if level:
            result.append({
                "type": "利润率预警",
                "level": level.value,
                "category": "盈利能力",
                "item": margin_label,
                "message": f"订单{margin_label}仅 {margin:.2f}%，低于{level.value}阈值",
                "suggestion": "建议：重新评估报价策略；分析是否有降本空间；与客户协商价格调整。",
                "margin_rate": margin
            })
        return result

    def _delivery_warnings(self, order: ProductionOrder) -> List[Dict]:
        from datetime import datetime
        result = []
        if order.status.value == "已交付" or not order.plan_delivery_date:
            return result
        try:
            today = datetime.now().date()
            plan_date = datetime.strptime(order.plan_delivery_date, "%Y-%m-%d").date()
            days_left = (plan_date - today).days
            cr = order.completion_rate
            needed_cr_per_day = (1 - cr) / max(days_left, 1) if days_left > 0 else 999

            if days_left < 0 and cr < 1.0:
                result.append({
                    "type": "交期预警",
                    "level": WarningLevel.CRITICAL.value,
                    "category": "生产进度",
                    "item": "订单交付",
                    "message": f"订单已逾期 {-days_left} 天，当前完工率仅 {cr*100:.1f}%",
                    "suggestion": "建议：立即启动应急方案，协调资源赶工；与客户沟通交付预期。"
                })
            elif 0 <= days_left <= self.THRESHOLDS["completion_warning_days"] and cr < 0.9:
                result.append({
                    "type": "交期预警",
                    "level": WarningLevel.WARNING.value,
                    "category": "生产进度",
                    "item": "订单交付",
                    "message": f"距计划交付仅剩 {days_left} 天，完工率 {cr*100:.1f}%，进度偏紧",
                    "suggestion": "建议：检查剩余工序瓶颈，必要时安排加班或外协。"
                })
        except Exception:
            pass
        return result

    def _completion_warnings(self, order: ProductionOrder) -> List[Dict]:
        from datetime import datetime
        result = []
        if not order.start_date or order.status.value == "已交付":
            return result
        try:
            today = datetime.now().date()
            start = datetime.strptime(order.start_date, "%Y-%m-%d").date()
            plan_end = datetime.strptime(order.plan_delivery_date, "%Y-%m-%d").date()
            total_days = max((plan_end - start).days, 1)
            elapsed = max((today - start).days, 0)
            expected_cr = min(1.0, elapsed / total_days)
            actual_cr = order.completion_rate
            gap = expected_cr - actual_cr

            if gap >= 0.2:
                result.append({
                    "type": "进度预警",
                    "level": WarningLevel.CRITICAL.value,
                    "category": "生产进度",
                    "item": "完工进度",
                    "message": f"进度严重滞后：时间进度 {expected_cr*100:.0f}%，完工率 {actual_cr*100:.1f}%，差 {gap*100:.0f} 个百分点",
                    "suggestion": "建议：排查进度滞后原因（待料/设备/人员），立即整改。"
                })
            elif gap >= 0.1:
                result.append({
                    "type": "进度预警",
                    "level": WarningLevel.WARNING.value,
                    "category": "生产进度",
                    "item": "完工进度",
                    "message": f"进度偏慢：时间进度 {expected_cr*100:.0f}%，完工率 {actual_cr*100:.1f}%，差 {gap*100:.0f} 个百分点",
                    "suggestion": "建议：加强生产调度，密切关注后续工序进度。"
                })
        except Exception:
            pass
        return result
