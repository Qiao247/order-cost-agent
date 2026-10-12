import os
import tempfile
import unittest

import pandas as pd

import app as app_module
from core.models import DocumentType
from data.document_parser import DocumentParser
from data.repository import DataRepository


PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ApiAndDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        app_module.repo = DataRepository(self.temp_dir.name)
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_generic_labor_and_storage_detection(self):
        parser = DocumentParser()
        labor = pd.DataFrame({"实际工时(h)": [8], "工资率(元/h)": [30]})
        storage = pd.DataFrame({"入库数量": [10], "生产订单号": ["PO1"]})
        self.assertEqual(
            parser.detect_document_type(labor, "upload.xlsx"),
            DocumentType.LABOR_TIME_CARD,
        )
        self.assertEqual(
            parser.detect_document_type(storage, "upload.xlsx"),
            DocumentType.PRODUCT_STORAGE,
        )

    def test_all_sample_xlsx_files_are_json_serializable(self):
        sample_dir = os.path.join(PROJECT, "sample_documents")
        files = sorted(name for name in os.listdir(sample_dir) if name.endswith(".xlsx"))
        self.assertEqual(len(files), 6)
        for name in files:
            with self.subTest(name=name):
                with open(os.path.join(sample_dir, name), "rb") as handle:
                    response = self.client.post(
                        "/api/documents/parse",
                        data={"file": (handle, name)},
                        content_type="multipart/form-data",
                    )
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertIn("document_type", response.get_json())

    def test_labor_summary_uses_labor_cost_not_hourly_rate(self):
        path = os.path.join(PROJECT, "sample_documents", "工时卡-2026年8月18日.xlsx")
        frame = pd.read_excel(path)
        result = DocumentParser().parse_document(
            frame, DocumentType.LABOR_TIME_CARD, os.path.basename(path)
        )
        self.assertEqual(result["summary"]["total_labor_cost"], 1737.5)

    def test_invalid_numeric_order_data_returns_422(self):
        payload = {
            "order_no": "NEG-001",
            "product_name": "测试产品",
            "order_qty": -10,
            "sales_price": -20,
            "completion_rate": 1.5,
            "status": "已完工",
            "accounting_status": "会计已审批",
            "source_mode": "实际业务",
            "start_date": "2026-08-01",
            "plan_delivery_date": "2026-09-01",
            "standard_cost": {},
            "actual_cost": {},
        }
        response = self.client.post("/api/orders", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertTrue(response.get_json().get("details"))

    def test_invalid_status_filter_returns_400(self):
        response = self.client.get("/api/orders?status=不存在")
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
