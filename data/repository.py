import json
import os
import shutil
import threading
from typing import Dict, List, Optional
from datetime import datetime
from filelock import FileLock
from core.models import ProductionOrder, OrderStatus, AccountingStatus
from core.cost_accounting import CostAccountingEngine
from core.variance_analysis import VarianceAnalyzer, WarningEngine
from core.validation import validate_order


class DataPersistenceError(RuntimeError):
    pass


class DataRepository:
    def __init__(self, storage_path: str = None):
        if storage_path is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            storage_path = os.path.join(base_dir, "data_storage")
        self.storage_path = storage_path
        os.makedirs(self.storage_path, exist_ok=True)
        self._lock = FileLock(os.path.join(self.storage_path, "orders_data.lock"), timeout=10)
        self._orders: Dict[str, ProductionOrder] = {}
        self._analyzer = VarianceAnalyzer()
        self._warning_engine = WarningEngine()
        self._load_from_disk()

    def _data_file(self) -> str:
        return os.path.join(self.storage_path, "orders_data.json")

    def _load_from_disk(self):
        fp = self._data_file()
        if os.path.exists(fp):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._orders = {k: self._dict_to_order(v) for k, v in raw.items()}
            except Exception as e:
                # 保留原文件并另存快照，禁止静默清空后继续运行。
                backup = fp + ".corrupt." + datetime.now().strftime("%Y%m%d%H%M%S")
                try:
                    shutil.copy2(fp, backup)
                except Exception:
                    pass
                raise DataPersistenceError(
                    f"订单数据加载失败，原文件已保留：{e}"
                ) from e

    def _save_to_disk(self):
        fp = self._data_file()
        tmp_fp = f"{fp}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            data = {k: self._order_to_dict(v) for k, v in self._orders.items()}
            # 先写临时文件再原子替换，避免写一半中断导致数据文件损坏
            with open(tmp_fp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_fp, fp)
        except Exception as e:
            try:
                if os.path.exists(tmp_fp):
                    os.remove(tmp_fp)
            except Exception:
                pass
            raise DataPersistenceError(f"订单数据保存失败：{e}") from e

    def _refresh(self):
        # 每次操作前重读磁盘，保证 Flask 与 Streamlit 双进程间数据一致
        with self._lock:
            self._load_from_disk()

    def exists(self, order_no: str) -> bool:
        with self._lock:
            self._load_from_disk()
            return order_no in self._orders

    def _order_to_dict(self, order: ProductionOrder) -> Dict:
        return {
            "order_no": order.order_no,
            "product_name": order.product_name,
            "product_code": order.product_code,
            "spec": order.spec,
            "order_qty": order.order_qty,
            "unit": order.unit,
            "sales_price": order.sales_price,
            "customer_name": order.customer_name,
            "start_date": order.start_date,
            "plan_delivery_date": order.plan_delivery_date,
            "actual_delivery_date": order.actual_delivery_date,
            "status": order.status.value,
            "completion_rate": order.completion_rate,
            "accounting_status": order.accounting_status.value,
            "source_mode": order.source_mode,
            "standard_cost": {
                "materials": [m.__dict__ for m in order.standard_cost.materials],
                "labors": [l.__dict__ for l in order.standard_cost.labors],
                "expenses": [e.__dict__ for e in order.standard_cost.expenses],
            },
            "actual_cost": {
                "materials": [m.__dict__ for m in order.actual_cost.materials],
                "labors": [l.__dict__ for l in order.actual_cost.labors],
                "expenses": [e.__dict__ for e in order.actual_cost.expenses],
            },
            "variances": [v.__dict__ for v in order.variances],
            "warnings": order.warnings,
            "documents": order.documents,
            "created_at": order.created_at
        }

    def _dict_to_order(self, d: Dict) -> ProductionOrder:
        from core.models import StandardCost, ActualCost, MaterialItem, LaborItem, ExpenseItem, CostVariance
        sc = StandardCost()
        sc.materials = [MaterialItem(**m) for m in d["standard_cost"]["materials"]]
        sc.labors = [LaborItem(**l) for l in d["standard_cost"]["labors"]]
        sc.expenses = [ExpenseItem(**e) for e in d["standard_cost"]["expenses"]]

        ac = ActualCost()
        ac.materials = [MaterialItem(**m) for m in d["actual_cost"]["materials"]]
        ac.labors = [LaborItem(**l) for l in d["actual_cost"]["labors"]]
        ac.expenses = [ExpenseItem(**e) for e in d["actual_cost"]["expenses"]]

        order = ProductionOrder(
            order_no=d["order_no"],
            product_name=d["product_name"],
            product_code=d["product_code"],
            spec=d["spec"],
            order_qty=d["order_qty"],
            unit=d["unit"],
            sales_price=d["sales_price"],
            customer_name=d["customer_name"],
            start_date=d["start_date"],
            plan_delivery_date=d["plan_delivery_date"],
            actual_delivery_date=d.get("actual_delivery_date"),
            status=OrderStatus(d["status"]),
            completion_rate=d["completion_rate"],
            accounting_status=AccountingStatus(d.get("accounting_status", "管理暂算")),
            source_mode=d.get("source_mode", "示例/仿真"),
            standard_cost=sc,
            actual_cost=ac,
            variances=[CostVariance(**v) for v in d.get("variances", [])],
            warnings=d.get("warnings", []),
            documents=d.get("documents", []),
            created_at=d.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        return order

    def add_order(self, order: ProductionOrder, run_diagnosis: bool = True) -> ProductionOrder:
        validate_order(order)
        with self._lock:
            self._load_from_disk()
            if order.order_no in self._orders:
                raise ValueError(f"订单号 {order.order_no} 已存在")
            if run_diagnosis:
                self.run_full_diagnosis(order)
            self._orders[order.order_no] = order
            self._save_to_disk()
        return order

    def add_orders_batch(self, orders: List[ProductionOrder]) -> List[ProductionOrder]:
        for order in orders:
            validate_order(order)
        order_nos = [order.order_no for order in orders]
        repeated = sorted({number for number in order_nos if order_nos.count(number) > 1})
        if repeated:
            raise ValueError(f"批次内订单号重复：{','.join(repeated)}")
        with self._lock:
            self._load_from_disk()
            duplicates = [o.order_no for o in orders if o.order_no in self._orders]
            if duplicates:
                raise ValueError(f"订单号已存在：{','.join(duplicates)}")
            for o in orders:
                self.run_full_diagnosis(o)
                self._orders[o.order_no] = o
            self._save_to_disk()
        return orders

    def get_order(self, order_no: str) -> Optional[ProductionOrder]:
        with self._lock:
            self._load_from_disk()
            order = self._orders.get(order_no)
            if order and not order.variances:
                self.run_full_diagnosis(order)
            return order

    def list_orders(self, status: Optional[OrderStatus] = None,
                    keyword: str = "") -> List[ProductionOrder]:
        with self._lock:
            self._load_from_disk()
            result = list(self._orders.values())
        if status:
            result = [o for o in result if o.status == status]
        if keyword:
            kw = keyword.lower()
            result = [o for o in result if
                      kw in o.order_no.lower() or
                      kw in o.product_name.lower() or
                      kw in o.product_code.lower() or
                      kw in o.customer_name.lower()]
        result.sort(key=lambda o: o.created_at, reverse=True)
        return result

    def update_order(self, order: ProductionOrder, run_diagnosis: bool = True) -> Optional[ProductionOrder]:
        validate_order(order)
        with self._lock:
            self._load_from_disk()
            if order.order_no not in self._orders:
                return None
            if run_diagnosis:
                self.run_full_diagnosis(order)
            self._orders[order.order_no] = order
            self._save_to_disk()
            return order

    def delete_order(self, order_no: str) -> bool:
        with self._lock:
            self._load_from_disk()
            if order_no in self._orders:
                del self._orders[order_no]
                self._save_to_disk()
                return True
            return False

    def run_full_diagnosis(self, order: ProductionOrder):
        self._analyzer.analyze_all_variances(order)
        self._warning_engine.generate_warnings(order)

    def get_dashboard_stats(self) -> Dict:
        with self._lock:
            self._load_from_disk()
            orders = list(self._orders.values())
        if not orders:
            return {
                "total_orders": 0,
                "in_progress": 0,
                "completed": 0,
                "total_sales": 0,
                "total_cost_std": 0,
                "total_cost_std_to_date": 0,
                "total_cost_act": 0,
                "total_profit_std": 0,
                "total_profit_act": 0,
                "total_profit_expected": 0,
                "actual_profit_order_count": 0,
                "warning_count": {"正常": 0, "关注": 0, "预警": 0, "严重": 0},
                "avg_margin_std": 0,
                "avg_margin_act": 0,
                "avg_margin_expected": 0,
            }

        sales_total = sum(o.sales_amount for o in orders)
        cost_std = sum(o.standard_cost.total_cost for o in orders)
        cost_std_to_date = sum(
            CostAccountingEngine.standard_cost_to_date(o).total_cost for o in orders
        )
        cost_act = sum(o.actual_cost.total_cost for o in orders)
        profit_std = sum(o.standard_profit for o in orders)
        actual_profits = [o.actual_profit for o in orders if o.actual_profit is not None]
        profit_act = sum(actual_profits)
        expected_profits = [
            CostAccountingEngine.expected_profit(o) for o in orders
        ]
        expected_profits = [p for p in expected_profits if p is not None]

        warning_counts = {"正常": 0, "关注": 0, "预警": 0, "严重": 0}
        for o in orders:
            if not o.warnings:
                warning_counts["正常"] += 1
            else:
                max_level = "关注"
                for w in o.warnings:
                    lv = w.get("level", "关注")
                    priority = {"正常": 0, "关注": 1, "预警": 2, "严重": 3}
                    if priority.get(lv, 0) > priority.get(max_level, 0):
                        max_level = lv
                warning_counts[max_level] += 1

        margins_std = []
        margins_act = []
        margins_expected = []
        for o in orders:
            if o.sales_price > 0:
                ua = CostAccountingEngine.unit_cost_analysis(o)
                margins_std.append(ua["gross_margin_rate_standard"])
                if ua.get("expected_margin_rate") is not None:
                    margins_expected.append(ua["expected_margin_rate"])
                if o.actual_profit is not None and o.sales_amount > 0:
                    margins_act.append(round(o.actual_profit / o.sales_amount * 100, 2))

        return {
            "total_orders": len(orders),
            "not_started": sum(1 for o in orders if o.status == OrderStatus.NOT_STARTED),
            "in_progress": sum(1 for o in orders if o.status == OrderStatus.IN_PROGRESS),
            "completed": sum(1 for o in orders if o.status == OrderStatus.COMPLETED),
            "delivered": sum(1 for o in orders if o.status == OrderStatus.DELIVERED),
            "total_sales": round(sales_total, 2),
            "total_cost_std": round(cost_std, 2),
            "total_cost_std_to_date": round(cost_std_to_date, 2),
            "total_cost_act": round(cost_act, 2),
            "total_profit_std": round(profit_std, 2),
            "total_profit_act": round(profit_act, 2),
            "total_profit_expected": round(sum(expected_profits), 2),
            "actual_profit_order_count": len(actual_profits),
            "warning_count": warning_counts,
            "avg_margin_std": round(sum(margins_std) / len(margins_std), 2) if margins_std else 0,
            "avg_margin_act": round(sum(margins_act) / len(margins_act), 2) if margins_act else 0,
            "avg_margin_expected": round(sum(margins_expected) / len(margins_expected), 2) if margins_expected else 0,
        }

    def clear_all(self):
        with self._lock:
            self._orders = {}
            fp = self._data_file()
            if os.path.exists(fp):
                os.remove(fp)
