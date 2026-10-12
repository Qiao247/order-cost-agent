from typing import List, Dict
import random
from datetime import datetime, timedelta
from core.models import (
    ProductionOrder, StandardCost, ActualCost,
    MaterialItem, LaborItem, ExpenseItem, OrderStatus, AccountingStatus
)


class SampleDataGenerator:
    MATERIALS_LIBRARY = [
        {"code": "MAT-001", "name": "HDPE高密度聚乙烯颗粒", "spec": "50kg/袋", "unit": "kg", "base_price": 12.5},
        {"code": "MAT-002", "name": "ABS工程塑料粒", "spec": "25kg/袋", "unit": "kg", "base_price": 18.8},
        {"code": "MAT-003", "name": "棉纱（21支）", "spec": "180kg/包", "unit": "kg", "base_price": 28.5},
        {"code": "MAT-004", "name": "涤纶丝（150D）", "spec": "300D/96F", "unit": "kg", "base_price": 16.2},
        {"code": "MAT-005", "name": "橡胶鞋底料", "spec": "耐磨型", "unit": "kg", "base_price": 22.0},
        {"code": "MAT-006", "name": "EVA发泡材料", "spec": "中密度", "unit": "kg", "base_price": 15.6},
        {"code": "MAT-007", "name": "防砸钢头", "spec": "欧标EN12568", "unit": "个", "base_price": 8.5},
        {"code": "MAT-008", "name": "防刺穿中底", "spec": "凯夫拉材质", "unit": "片", "base_price": 6.8},
        {"code": "MAT-009", "name": "PC防雾镜片", "spec": "2mm厚", "unit": "片", "base_price": 3.2},
        {"code": "MAT-010", "name": "尼龙搭扣", "spec": "5cm宽", "unit": "米", "base_price": 2.5},
        {"code": "MAT-011", "name": "反光条", "spec": "5cm宽高亮", "unit": "米", "base_price": 4.8},
        {"code": "MAT-012", "name": "无纺布（SMS）", "spec": "50g/m²", "unit": "米", "base_price": 3.5},
        {"code": "MAT-013", "name": "拉链", "spec": "5号尼龙", "unit": "条", "base_price": 1.8},
        {"code": "MAT-014", "name": "缝纫线", "spec": "涤纶402", "unit": "轴", "base_price": 5.0},
        {"code": "MAT-015", "name": "包装盒", "spec": "定制彩盒", "unit": "个", "base_price": 3.0},
    ]

    PROCESSES_LIBRARY = [
        {"name": "裁切下料", "work_type": "机加工", "base_hours": 0.15, "base_rate": 28},
        {"name": "注塑成型", "work_type": "机加工", "base_hours": 0.10, "base_rate": 32},
        {"name": "缝纫拼接", "work_type": "人工", "base_hours": 0.25, "base_rate": 26},
        {"name": "装配组装", "work_type": "人工", "base_hours": 0.18, "base_rate": 25},
        {"name": "热压成型", "work_type": "机加工", "base_hours": 0.08, "base_rate": 35},
        {"name": "包边处理", "work_type": "人工", "base_hours": 0.12, "base_rate": 24},
        {"name": "检验包装", "work_type": "人工", "base_hours": 0.08, "base_rate": 22},
        {"name": "硫化处理", "work_type": "机加工", "base_hours": 0.20, "base_rate": 38},
    ]

    EXPENSES_LIBRARY = [
        {"type": "变动制造费用", "name": "水电费", "base_amount": 3.5},
        {"type": "变动制造费用", "name": "机物料消耗", "base_amount": 2.2},
        {"type": "变动制造费用", "name": "辅料费", "base_amount": 1.8},
        {"type": "固定制造费用", "name": "设备折旧", "base_amount": 5.0},
        {"type": "固定制造费用", "name": "车间管理人员工资", "base_amount": 4.2},
        {"type": "固定制造费用", "name": "厂房租金分摊", "base_amount": 3.0},
    ]

    PRODUCT_TEMPLATES = [
        {
            "code": "P-AQM-001",
            "name": "ABS安全帽（V型）",
            "spec": "白/红/黄/蓝 多色可选",
            "unit": "顶",
            "materials": [
                ("MAT-002", 0.45), ("MAT-011", 0.30), ("MAT-010", 0.25),
                ("MAT-014", 0.02), ("MAT-015", 1)
            ],
            "processes": [0, 1, 3, 6],
            "base_price": 63.0
        },
        {
            "code": "P-FS-002",
            "name": "防砸防刺穿安全鞋",
            "spec": "6KV绝缘 牛皮鞋面",
            "unit": "双",
            "materials": [
                ("MAT-005", 0.80), ("MAT-006", 0.35), ("MAT-007", 1),
                ("MAT-008", 1), ("MAT-010", 0.50), ("MAT-014", 0.05),
                ("MAT-015", 1)
            ],
            "processes": [0, 7, 5, 3, 6],
            "base_price": 113.0
        },
        {
            "code": "P-ST-003",
            "name": "棉纱劳保手套",
            "spec": "10针 600g/打",
            "unit": "副",
            "materials": [
                ("MAT-003", 0.055), ("MAT-014", 0.005)
            ],
            "processes": [2, 6],
            "base_price": 40.0
        },
        {
            "code": "P-WC-004",
            "name": "高可视反光背心",
            "spec": "涤纶经编 3M反光条",
            "unit": "件",
            "materials": [
                ("MAT-004", 0.25), ("MAT-011", 2.5), ("MAT-013", 1),
                ("MAT-015", 1)
            ],
            "processes": [0, 2, 6],
            "base_price": 72.0
        },
        {
            "code": "P-GG-005",
            "name": "防雾护目镜",
            "spec": "PC镜片 防冲击防飞溅",
            "unit": "副",
            "materials": [
                ("MAT-002", 0.06), ("MAT-009", 1), ("MAT-010", 0.20),
                ("MAT-015", 1)
            ],
            "processes": [1, 3, 6],
            "base_price": 50.0
        },
        {
            "code": "P-FHF-006",
            "name": "SMS一次性防护服",
            "spec": "50g 带帽连脚",
            "unit": "件",
            "materials": [
                ("MAT-012", 5.5), ("MAT-013", 1), ("MAT-010", 0.40),
                ("MAT-015", 1)
            ],
            "processes": [0, 2, 5, 6],
            "base_price": 81.0
        }
    ]

    CUSTOMERS = [
        "北京建工集团有限责任公司",
        "中国建筑第二工程局有限公司",
        "中铁十六局集团有限公司",
        "北京城建集团有限责任公司",
        "中石化北京石油分公司",
        "京能集团清洁能源股份公司",
        "北京地铁运营有限公司",
        "北汽福田汽车股份有限公司",
        "北京环卫集团",
        "京东物流华北分公司"
    ]

    def __init__(self, seed: int = 42):
        self.random = random.Random(seed)

    def generate_orders(self, count: int = 10, with_actual: bool = True,
                        existing_order_nos=None) -> List[ProductionOrder]:
        orders = []
        today = datetime.now()
        # 已存在的订单号集合，用于续编单号，避免重复单号静默覆盖已有订单
        used_nos = set(existing_order_nos or ())
        seq = 0
        for i in range(count):
            template = self.random.choice(self.PRODUCT_TEMPLATES)
            customer = self.random.choice(self.CUSTOMERS)
            order_qty = self.random.choice([100, 200, 300, 500, 800, 1000, 1500, 2000])

            status_val = self.random.random()
            if status_val < 0.15:
                status = OrderStatus.NOT_STARTED
                completion_rate = 0.0
            elif status_val < 0.6:
                status = OrderStatus.IN_PROGRESS
                completion_rate = round(self.random.uniform(0.1, 0.95), 2)
            elif status_val < 0.85:
                status = OrderStatus.COMPLETED
                completion_rate = 1.0
            else:
                status = OrderStatus.DELIVERED
                completion_rate = 1.0

            if status == OrderStatus.NOT_STARTED:
                start_date = today + timedelta(days=self.random.randint(3, 15))
                delivery_days = self.random.randint(15, 30)
            elif status == OrderStatus.IN_PROGRESS:
                start_date = today - timedelta(days=self.random.randint(2, 18))
                delivery_days = self.random.randint(max(10, (today - start_date).days + 5), max(20, (today - start_date).days + 25))
                elapsed = (today - start_date).days
                time_progress = elapsed / delivery_days
                base_cr = max(0.05, min(0.95, time_progress + self.random.uniform(-0.12, 0.10)))
                completion_rate = round(max(0.05, min(0.98, base_cr)), 2)
            else:
                start_date = today - timedelta(days=self.random.randint(10, 35))
                delivery_days = self.random.randint(12, 30)

            plan_delivery = start_date + timedelta(days=delivery_days)

            actual_delivery = None
            if status in (OrderStatus.COMPLETED, OrderStatus.DELIVERED):
                actual_offset = self.random.randint(-3, 5)
                actual_date = min(plan_delivery + timedelta(days=actual_offset), today)
                actual_delivery = actual_date.strftime("%Y-%m-%d")

            sales_price = round(template["base_price"] * self.random.uniform(1.02, 1.12), 2)
            # 单号自动续编，跳过已占用的单号
            while True:
                seq += 1
                order_no = f"PO{today.strftime('%Y%m')}{seq:04d}"
                if order_no not in used_nos:
                    used_nos.add(order_no)
                    break

            std_cost = self._generate_standard_cost(template, order_qty)
            # 未开工订单尚未投产，不应产生任何实际成本
            if with_actual and status != OrderStatus.NOT_STARTED:
                # 按概率抽成本场景：多数订单成本正常，少数出现关注/预警/严重级差异，
                # 符合真实企业成本分布（异常订单是少数）
                roll = self.random.random()
                if roll < 0.62:
                    scenario = "healthy"
                elif roll < 0.76:
                    scenario = "attention"
                elif roll < 0.88:
                    scenario = "warning"
                else:
                    scenario = "critical"
                act_cost = self._generate_actual_cost(template, order_qty, completion_rate, std_cost, scenario)
            else:
                act_cost = ActualCost()

            docs = self._generate_documents_list(order_no, status)

            order = ProductionOrder(
                order_no=order_no,
                product_name=template["name"],
                product_code=template["code"],
                spec=template["spec"],
                order_qty=order_qty,
                unit=template["unit"],
                sales_price=sales_price,
                customer_name=customer,
                start_date=start_date.strftime("%Y-%m-%d"),
                plan_delivery_date=plan_delivery.strftime("%Y-%m-%d"),
                actual_delivery_date=actual_delivery,
                status=status,
                completion_rate=completion_rate,
                accounting_status=AccountingStatus.MANAGEMENT_ESTIMATE,
                source_mode="示例/仿真",
                standard_cost=std_cost,
                actual_cost=act_cost,
                documents=docs
            )
            orders.append(order)
        return orders

    def _generate_standard_cost(self, template: Dict, qty: int) -> StandardCost:
        std = StandardCost()
        for mat_code, per_unit in template["materials"]:
            mat_info = next((m for m in self.MATERIALS_LIBRARY if m["code"] == mat_code), None)
            if mat_info:
                # 标准成本必须是"纯标准"：不加随机噪声，差异全部体现在实际成本侧
                total_qty = round(per_unit * qty, 4)
                price = mat_info["base_price"]
                std.materials.append(MaterialItem(
                    material_code=mat_code,
                    material_name=mat_info["name"],
                    spec=mat_info["spec"],
                    unit=mat_info["unit"],
                    qty=total_qty,
                    unit_price=price,
                    amount=round(total_qty * price, 2)
                ))

        for proc_idx in template["processes"]:
            proc = self.PROCESSES_LIBRARY[proc_idx]
            hours = round(proc["base_hours"] * qty, 2)
            rate = proc["base_rate"]
            std.labors.append(LaborItem(
                process_name=proc["name"],
                work_type=proc["work_type"],
                hours=hours,
                hourly_rate=rate,
                amount=round(hours * rate, 2)
            ))

        for exp in self.EXPENSES_LIBRARY:
            amt = round(exp["base_amount"] * qty, 2)
            std.expenses.append(ExpenseItem(
                expense_type=exp["type"],
                expense_name=exp["name"],
                allocation_base="机器工时+人工工时",
                amount=amt,
                allocated_amount=amt
            ))
        return std

    # 成本场景 → (异常明细占比, 异常因子三角分布(min, mode, max))
    # healthy 明细因子在 ±2% 内波动：正常订单不应触发 3% 关注线；
    # 异常订单只有部分明细超支，符合"个别项目失控"的真实形态
    SCENARIOS = {
        "healthy": (0.0, (0.99, 1.0, 1.01)),
        "attention": (0.5, (1.02, 1.04, 1.07)),
        "warning": (0.5, (1.06, 1.09, 1.13)),
        "critical": (0.6, (1.13, 1.16, 1.30)),
    }

    def _generate_actual_cost(self, template: Dict, qty: int, completion_rate: float,
                              std: StandardCost, scenario: str = "healthy") -> ActualCost:
        act = ActualCost()
        cr = max(completion_rate, 0.05)
        anomaly_ratio, abnormal_range = self.SCENARIOS.get(scenario, self.SCENARIOS["healthy"])
        normal_range = self.SCENARIOS["healthy"][1]

        def pick_factor() -> float:
            """按场景抽取成本偏差因子：健康明细 ±2%，异常明细按场景分布。"""
            if self.random.random() < anomaly_ratio:
                return self.random.triangular(*abnormal_range)
            return self.random.triangular(*normal_range)

        def pick_factor_pair() -> tuple:
            """材料/人工用：异常明细只在单一维度失控（价格→管理性浪费 / 用量→工艺性浪费），
            另一维度保持健康，避免双因子相乘放大差异等级，也保证两类浪费都有仿真样本。"""
            if self.random.random() < anomaly_ratio:
                if self.random.random() < 0.5:
                    return (self.random.triangular(*abnormal_range),
                            self.random.triangular(*normal_range))
                return (self.random.triangular(*normal_range),
                        self.random.triangular(*abnormal_range))
            return (self.random.triangular(*normal_range),
                    self.random.triangular(*normal_range))

        for i, std_mat in enumerate(std.materials):
            price_factor, qty_factor = pick_factor_pair()
            new_price = round(std_mat.unit_price * price_factor, 2)
            new_qty = round(std_mat.qty * qty_factor * cr, 4)
            act.materials.append(MaterialItem(
                material_code=std_mat.material_code,
                material_name=std_mat.material_name,
                spec=std_mat.spec,
                unit=std_mat.unit,
                qty=new_qty,
                unit_price=new_price,
                amount=round(new_qty * new_price, 2)
            ))

        for i, std_lab in enumerate(std.labors):
            rate_factor, hours_factor = pick_factor_pair()
            new_rate = round(std_lab.hourly_rate * rate_factor, 2)
            new_hours = round(std_lab.hours * hours_factor * cr, 2)
            act.labors.append(LaborItem(
                process_name=std_lab.process_name,
                work_type=std_lab.work_type,
                hours=new_hours,
                hourly_rate=new_rate,
                amount=round(new_hours * new_rate, 2)
            ))

        for i, std_exp in enumerate(std.expenses):
            factor = pick_factor()
            new_amt = round(std_exp.amount * factor * cr, 2)
            act.expenses.append(ExpenseItem(
                expense_type=std_exp.expense_type,
                expense_name=std_exp.expense_name,
                allocation_base=std_exp.allocation_base,
                amount=new_amt,
                allocated_amount=new_amt
            ))
        return act

    def _generate_documents_list(self, order_no: str, status: OrderStatus) -> List[Dict]:
        docs = []
        now = datetime.now()
        docs.append({
            "doc_no": f"SO-{order_no}",
            "type": "销售订单",
            "status": "已审核",
            "date": (now - timedelta(days=35)).strftime("%Y-%m-%d")
        })
        if status != OrderStatus.NOT_STARTED:
            docs.append({
                "doc_no": f"PO-M-{order_no}",
                "type": "采购订单",
                "status": "已入库",
                "date": (now - timedelta(days=32)).strftime("%Y-%m-%d")
            })
            docs.append({
                "doc_no": f"MR-{order_no}",
                "type": "领料单",
                "status": "已出库",
                "date": (now - timedelta(days=28)).strftime("%Y-%m-%d")
            })
        if status in (OrderStatus.IN_PROGRESS, OrderStatus.COMPLETED, OrderStatus.DELIVERED):
            docs.append({
                "doc_no": f"LT-{order_no}-A",
                "type": "工时卡",
                "status": "已录入",
                "date": (now - timedelta(days=15)).strftime("%Y-%m-%d")
            })
            docs.append({
                "doc_no": f"ME-{order_no}",
                "type": "制造费用单",
                "status": "已分配",
                "date": (now - timedelta(days=10)).strftime("%Y-%m-%d")
            })
        if status in (OrderStatus.COMPLETED, OrderStatus.DELIVERED):
            docs.append({
                "doc_no": f"PS-{order_no}",
                "type": "产品入库单",
                "status": "已入库",
                "date": (now - timedelta(days=5)).strftime("%Y-%m-%d")
            })
        return docs
