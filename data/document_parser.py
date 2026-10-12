import pandas as pd
import numpy as np
import io
from typing import Dict, List, Any, Optional
from datetime import date, datetime
from core.models import DocumentType


class DocumentParser:
    SUPPORTED_TYPES = [
        DocumentType.PURCHASE_ORDER,
        DocumentType.MATERIAL_REQUISITION,
        DocumentType.LABOR_TIME_CARD,
        DocumentType.MANUFACTURING_EXPENSE,
        DocumentType.SALES_ORDER,
        DocumentType.PRODUCT_STORAGE,
    ]

    @staticmethod
    def read_tabular_file(file_obj, filename: str) -> pd.DataFrame:
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
        if hasattr(file_obj, "getvalue"):
            raw = file_obj.getvalue()
        else:
            raw = file_obj.read()
        if ext == "xlsx":
            return pd.read_excel(io.BytesIO(raw))
        if ext == "csv":
            last_error = None
            for encoding in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    return pd.read_csv(io.BytesIO(raw), encoding=encoding)
                except UnicodeDecodeError as exc:
                    last_error = exc
            raise ValueError(f"CSV编码无法识别：{last_error}")
        raise ValueError("不支持的文件格式，仅支持 .xlsx 和 .csv")

    @staticmethod
    def _json_safe(value):
        if value is None:
            return None
        if isinstance(value, dict):
            return {str(k): DocumentParser._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [DocumentParser._json_safe(v) for v in value]
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(value, np.generic):
            return DocumentParser._json_safe(value.item())
        if isinstance(value, (pd.Timestamp, datetime, date)):
            return value.isoformat()
        return value

    def detect_document_type(self, df: pd.DataFrame, filename: str = "") -> DocumentType:
        filename = filename.lower()
        if any(k in filename for k in ["采购", "purchase", "po"]):
            return DocumentType.PURCHASE_ORDER
        elif any(k in filename for k in ["领料", "出库", "requisition"]):
            return DocumentType.MATERIAL_REQUISITION
        elif any(k in filename for k in ["工时", "考勤", "time", "labor"]):
            return DocumentType.LABOR_TIME_CARD
        elif any(k in filename for k in ["费用", "制造费用", "expense", "overhead"]):
            return DocumentType.MANUFACTURING_EXPENSE
        elif any(k in filename for k in ["销售", "sales", "sales_order"]):
            return DocumentType.SALES_ORDER
        elif any(k in filename for k in ["入库", "storage", "完工"]):
            return DocumentType.PRODUCT_STORAGE

        cols = [str(c).strip().lower() for c in df.columns]
        joined = "|".join(cols)
        # 领料单与采购单都含"单价/数量"列，"单价"区分度低；
        # 优先用领料、工时、费用等专有列判断，最后才用"单价/供应商"兜底识别采购单
        if any(k in joined for k in ["入库数量", "完工数量", "入库日期", "产品入库"]):
            return DocumentType.PRODUCT_STORAGE
        elif any(k in joined for k in ["领料部门", "领用", "领料人", "领用人", "领料单号"]):
            return DocumentType.MATERIAL_REQUISITION
        elif any(k in joined for k in ["工时", "工序", "hours", "process", "工资率"]):
            return DocumentType.LABOR_TIME_CARD
        elif any(k in joined for k in ["费用类型", "水电费", "折旧", "expense", "分配方式"]):
            return DocumentType.MANUFACTURING_EXPENSE
        elif any(k in joined for k in ["供应商", "采购单号", "unit price", "supplier", "vendor"]):
            return DocumentType.PURCHASE_ORDER
        elif any(k in joined for k in ["客户", "销售订单", "购货方", "customer", "sales"]):
            return DocumentType.SALES_ORDER
        raise ValueError("无法可靠识别单据类型，请手动指定单据类型或检查字段名称")

    def parse_document(self, df: pd.DataFrame, doc_type: Optional[DocumentType] = None,
                       filename: str = "") -> Dict[str, Any]:
        if doc_type is None:
            doc_type = self.detect_document_type(df, filename)

        result = {
            "document_type": doc_type.value,
            "filename": filename,
            "parse_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "row_count": len(df),
            "columns": [str(c) for c in df.columns],
            "data": df.to_dict(orient="records"),
            "summary": {}
        }

        if doc_type == DocumentType.PURCHASE_ORDER:
            result["summary"] = self._summarize_purchase(df)
        elif doc_type == DocumentType.MATERIAL_REQUISITION:
            result["summary"] = self._summarize_requisition(df)
        elif doc_type == DocumentType.LABOR_TIME_CARD:
            result["summary"] = self._summarize_labor(df)
        elif doc_type == DocumentType.MANUFACTURING_EXPENSE:
            result["summary"] = self._summarize_expense(df)
        elif doc_type == DocumentType.SALES_ORDER:
            result["summary"] = self._summarize_sales(df)
        elif doc_type == DocumentType.PRODUCT_STORAGE:
            result["summary"] = self._summarize_storage(df)

        return self._json_safe(result)

    def _find_col(self, df: pd.DataFrame, keywords: List[str]) -> Optional[str]:
        cols = {str(c): c for c in df.columns}
        # 关键词列表代表业务优先级，例如“人工成本”必须优先于“工资率”。
        for kw in keywords:
            for col_name_lower, col_orig in cols.items():
                if kw.lower() in col_name_lower.lower():
                    return col_orig
        return None

    def _safe_sum(self, df: pd.DataFrame, col: Optional[str]) -> float:
        if col is None or col not in df.columns:
            return 0.0
        try:
            return round(pd.to_numeric(df[col], errors="coerce").fillna(0).sum(), 2)
        except Exception:
            return 0.0

    def _summarize_purchase(self, df: pd.DataFrame) -> Dict:
        amt_col = self._find_col(df, ["金额", "amount", "total", "价税合计", "合计"])
        qty_col = self._find_col(df, ["数量", "qty", "quantity"])
        price_col = self._find_col(df, ["单价", "price", "unit price"])
        supp_col = self._find_col(df, ["供应商", "supplier", "vendor"])
        mat_col = self._find_col(df, ["物料", "材料", "商品", "material", "product", "品名"])

        suppliers = df[supp_col].dropna().unique().tolist() if supp_col else []
        total_amount = self._safe_sum(df, amt_col)
        total_qty = self._safe_sum(df, qty_col)
        if total_amount and total_qty:
            avg_price = round(total_amount / total_qty, 4)
        elif price_col:
            prices = pd.to_numeric(df[price_col], errors="coerce").dropna()
            avg_price = round(float(prices.mean()), 4) if not prices.empty else 0
        else:
            avg_price = 0
        return {
            "total_amount": total_amount,
            "total_qty": total_qty,
            "avg_price": avg_price,
            "suppliers": suppliers,
            "material_count": df[mat_col].nunique() if mat_col else len(df)
        }

    def _summarize_requisition(self, df: pd.DataFrame) -> Dict:
        amt_col = self._find_col(df, ["金额", "amount"])
        qty_col = self._find_col(df, ["数量", "qty"])
        dept_col = self._find_col(df, ["部门", "车间", "department", "workshop"])
        order_col = self._find_col(df, ["订单号", "生产订单", "order"])

        depts = df[dept_col].dropna().unique().tolist() if dept_col else []
        orders = df[order_col].dropna().unique().tolist() if order_col else []
        return {
            "total_amount": self._safe_sum(df, amt_col),
            "total_qty": self._safe_sum(df, qty_col),
            "departments": depts,
            "related_orders": orders
        }

    def _summarize_labor(self, df: pd.DataFrame) -> Dict:
        hours_col = self._find_col(df, ["工时", "hours", "时长"])
        amt_col = self._find_col(df, ["人工成本", "人工费", "工资总额", "金额", "amount"])
        proc_col = self._find_col(df, ["工序", "process", "工段"])
        emp_col = self._find_col(df, ["员工", "姓名", "employee", "worker"])

        processes = df[proc_col].dropna().unique().tolist() if proc_col else []
        return {
            "total_hours": self._safe_sum(df, hours_col),
            "total_labor_cost": self._safe_sum(df, amt_col),
            "processes": processes,
            "worker_count": df[emp_col].nunique() if emp_col else 0
        }

    def _summarize_expense(self, df: pd.DataFrame) -> Dict:
        amt_col = self._find_col(df, ["金额", "amount", "费用额"])
        type_col = self._find_col(df, ["类型", "费用类型", "expense type", "category"])

        types = df[type_col].dropna().unique().tolist() if type_col else []
        return {
            "total_expense": self._safe_sum(df, amt_col),
            "expense_types": types,
            "expense_items": len(df)
        }

    def _summarize_sales(self, df: pd.DataFrame) -> Dict:
        amt_col = self._find_col(df, ["金额", "amount", "销售额", "价税合计"])
        qty_col = self._find_col(df, ["数量", "qty"])
        cust_col = self._find_col(df, ["客户", "customer", "购货方"])
        prod_col = self._find_col(df, ["产品", "商品", "product", "品名"])

        customers = df[cust_col].dropna().unique().tolist() if cust_col else []
        return {
            "total_sales_amount": self._safe_sum(df, amt_col),
            "total_qty": self._safe_sum(df, qty_col),
            "customers": customers,
            "product_count": df[prod_col].nunique() if prod_col else len(df)
        }

    def _summarize_storage(self, df: pd.DataFrame) -> Dict:
        qty_col = self._find_col(df, ["数量", "qty", "入库数量"])
        prod_col = self._find_col(df, ["产品", "商品", "product", "品名"])
        order_col = self._find_col(df, ["订单号", "生产订单", "order"])

        orders = df[order_col].dropna().unique().tolist() if order_col else []
        return {
            "total_storage_qty": self._safe_sum(df, qty_col),
            "products": df[prod_col].dropna().unique().tolist() if prod_col else [],
            "related_orders": orders
        }
