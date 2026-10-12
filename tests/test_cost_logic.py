import tempfile
import unittest
import contextlib
import io
from concurrent.futures import ThreadPoolExecutor

from core.cost_accounting import CostAccountingEngine
from core.models import (
    AccountingStatus,
    ActualCost,
    MaterialItem,
    OrderStatus,
    ProductionOrder,
    StandardCost,
)
from core.validation import OrderValidationError, validate_order
from core.variance_analysis import VarianceAnalyzer, WarningEngine
from data.repository import DataRepository
from run_demo import run_demo


def make_order(
    order_no="T-001",
    completion_rate=0.5,
    status=OrderStatus.IN_PROGRESS,
    accounting_status=AccountingStatus.MANAGEMENT_ESTIMATE,
):
    standard = StandardCost(materials=[
        MaterialItem("M1", "材料", "", "kg", 10, 10, 100)
    ])
    actual = ActualCost(materials=[
        MaterialItem("M1", "材料", "", "kg", 5, 10, 50)
    ])
    return ProductionOrder(
        order_no=order_no,
        product_name="测试产品",
        product_code="P1",
        spec="",
        order_qty=10,
        unit="件",
        sales_price=20,
        customer_name="测试客户",
        start_date="2026-08-01",
        plan_delivery_date="2026-09-01",
        status=status,
        completion_rate=completion_rate,
        accounting_status=accounting_status,
        source_mode="示例/仿真",
        standard_cost=standard,
        actual_cost=actual,
    )


class CostLogicTests(unittest.TestCase):
    def test_incomplete_order_uses_standard_to_date(self):
        order = make_order()
        VarianceAnalyzer().analyze_all_variances(order)
        total_variance = next(
            item for item in order.variances if item.item_name == "【订单总成本】"
        )
        self.assertEqual(total_variance.variance_amount, 0)
        self.assertEqual(total_variance.variance_rate, 0)
        material_variance = next(
            item for item in order.variances
            if item.category == "直接材料" and not item.item_name.startswith("【")
        )
        self.assertAlmostEqual(sum(material_variance.components.values()), 0, places=2)
        self.assertIsNone(order.actual_profit)
        self.assertEqual(CostAccountingEngine.expected_profit(order), 100)

    def test_actual_profit_requires_completion_and_approval(self):
        order = make_order(
            completion_rate=1,
            status=OrderStatus.COMPLETED,
            accounting_status=AccountingStatus.ACCOUNTING_APPROVED,
        )
        order.actual_cost.materials[0].qty = 10
        order.actual_cost.materials[0].amount = 100
        self.assertEqual(order.actual_profit, 100)

    def test_zero_standard_values_do_not_crash(self):
        order = make_order()
        order.standard_cost.materials = [
            MaterialItem("M1", "材料", "", "kg", 0, 0, 0)
        ]
        order.actual_cost.materials = [
            MaterialItem("M1", "材料", "", "kg", 1, 1, 1)
        ]
        VarianceAnalyzer().analyze_all_variances(order)
        warnings = WarningEngine().generate_warnings(order)
        self.assertTrue(any(item["type"] == "成本差异预警" for item in warnings))

    def test_negative_order_is_rejected(self):
        order = make_order()
        order.order_qty = -1
        with self.assertRaises(OrderValidationError):
            validate_order(order)

    def test_repository_round_trip_preserves_accounting_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = DataRepository(tmp)
            order = make_order()
            repo.add_order(order)
            loaded = DataRepository(tmp).get_order(order.order_no)
            self.assertEqual(loaded.accounting_status, AccountingStatus.MANAGEMENT_ESTIMATE)
            self.assertEqual(loaded.source_mode, "示例/仿真")

    def test_demo_generation_preserves_existing_data_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = DataRepository(tmp)
            repo.add_order(make_order(order_no="KEEP-001"))
            with contextlib.redirect_stdout(io.StringIO()):
                run_demo(storage_path=tmp)
            order_nos = {item.order_no for item in DataRepository(tmp).list_orders()}
            self.assertIn("KEEP-001", order_nos)
            self.assertEqual(len(order_nos), 11)

    def test_concurrent_repositories_do_not_lose_orders(self):
        with tempfile.TemporaryDirectory() as tmp:
            DataRepository(tmp)

            def add(index):
                DataRepository(tmp).add_order(make_order(order_no=f"C-{index:03d}"))

            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(add, range(20)))
            self.assertEqual(len(DataRepository(tmp).list_orders()), 20)


if __name__ == "__main__":
    unittest.main()
