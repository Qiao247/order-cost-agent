import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import io
import json
from datetime import datetime

from core.models import (
    ProductionOrder, OrderStatus, AccountingStatus, StandardCost, ActualCost,
    MaterialItem, LaborItem, ExpenseItem
)
from core.cost_accounting import CostAccountingEngine
from core.variance_analysis import VarianceAnalyzer, WarningEngine
from core.validation import OrderValidationError
from data.repository import DataRepository, DataPersistenceError
from data.document_parser import DocumentParser
from data.sample_data import SampleDataGenerator

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "10")) * 1024 * 1024
allowed_origins = [
    origin.strip() for origin in os.environ.get(
        "CORS_ORIGINS", "http://localhost:8501,http://127.0.0.1:8501"
    ).split(",") if origin.strip()
]
CORS(app, resources={r"/api/*": {"origins": allowed_origins}})
API_KEY = os.environ.get("ORDER_COST_API_KEY", "").strip()

repo = DataRepository()
doc_parser = DocumentParser()
variance_analyzer = VarianceAnalyzer()
warning_engine = WarningEngine()
cost_engine = CostAccountingEngine()


@app.before_request
def require_api_key_for_mutations():
    if API_KEY and request.path.startswith("/api/") and request.method in {"POST", "PUT", "DELETE", "PATCH"}:
        if request.headers.get("X-API-Key", "") != API_KEY:
            return jsonify({"error": "缺少或无效的API密钥"}), 401


@app.errorhandler(OrderValidationError)
def handle_validation_error(exc):
    return jsonify({"error": "订单数据校验失败", "details": exc.errors}), 422


@app.errorhandler(DataPersistenceError)
def handle_persistence_error(exc):
    return jsonify({"error": str(exc)}), 500


def order_to_response(order: ProductionOrder, verbose: bool = True) -> dict:
    d = repo._order_to_dict(order)

    unit_info = cost_engine.unit_cost_analysis(order)
    d["unit_analysis"] = unit_info
    d["sales_amount"] = order.sales_amount
    d["standard_profit"] = order.standard_profit
    d["actual_profit"] = order.actual_profit
    d["profit_variance"] = order.profit_variance
    d["expected_profit"] = unit_info.get("expected_profit")
    d["expected_margin_rate"] = unit_info.get("expected_margin_rate")
    d["actual_profit_available"] = order.is_actual_profit_available

    if 0 < order.completion_rate < 1.0 and order.actual_cost.total_cost > 0:
        projected = cost_engine.project_final_cost(order.actual_cost, order.completion_rate)
        d["projected_cost"] = {
            "total_material": projected.total_material_cost,
            "total_labor": projected.total_labor_cost,
            "total_expense": projected.total_expense_cost,
            "total": projected.total_cost,
            "projected_profit": round(order.sales_amount - projected.total_cost, 2),
            "projected_unit_cost": round(projected.total_cost / max(order.order_qty, 1), 4)
        }
    return d


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "订单动态成本诊断智能体-API服务", "version": "1.0.0"})


@app.route("/api/dashboard", methods=["GET"])
def get_dashboard():
    stats = repo.get_dashboard_stats()
    recent_orders = repo.list_orders()[:5]
    recent = [order_to_response(o) for o in recent_orders]

    all_warnings = []
    for o in repo.list_orders():
        for w in o.warnings:
            # 复制一份再加展示字段，避免污染订单对象里的原始预警数据
            all_warnings.append({**w, "order_no": o.order_no, "product_name": o.product_name,
                                 "customer_name": o.customer_name})
    priority_order = {"严重": 0, "预警": 1, "关注": 2, "正常": 3}
    all_warnings.sort(key=lambda x: priority_order.get(x.get("level", "正常"), 99))

    return jsonify({
        "stats": stats,
        "recent_orders": recent,
        "top_warnings": all_warnings[:20]
    })


@app.route("/api/orders", methods=["GET"])
def list_orders():
    status_str = request.args.get("status")
    keyword = request.args.get("keyword", "")
    status = None
    if status_str:
        try:
            status = OrderStatus(status_str)
        except ValueError:
            return jsonify({"error": f"无效订单状态：{status_str}"}), 400
    orders = repo.list_orders(status=status, keyword=keyword)
    return jsonify({
        "total": len(orders),
        "data": [order_to_response(o) for o in orders]
    })


@app.route("/api/orders/<order_no>", methods=["GET"])
def get_order(order_no):
    order = repo.get_order(order_no)
    if not order:
        return jsonify({"error": "订单不存在"}), 404
    return jsonify(order_to_response(order))


@app.route("/api/orders", methods=["POST"])
def create_order():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "请求体必须是JSON对象"}), 400
    try:
        order = _dict_to_order(data)
        if repo.exists(order.order_no):
            return jsonify({"error": f"订单号 {order.order_no} 已存在，请使用 PUT 接口更新，避免覆盖已有订单"}), 409
        repo.add_order(order)
        return jsonify(order_to_response(order)), 201
    except (OrderValidationError, DataPersistenceError):
        raise
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/orders/<order_no>", methods=["PUT"])
def update_order(order_no):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "请求体必须是JSON对象"}), 400
    try:
        order = _dict_to_order(data)
        if order.order_no != order_no:
            return jsonify({"error": "订单号不一致"}), 400
        result = repo.update_order(order)
        if not result:
            return jsonify({"error": "订单不存在"}), 404
        return jsonify(order_to_response(result))
    except (OrderValidationError, DataPersistenceError):
        raise
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/orders/<order_no>", methods=["DELETE"])
def delete_order(order_no):
    ok = repo.delete_order(order_no)
    if not ok:
        return jsonify({"error": "订单不存在"}), 404
    return jsonify({"status": "ok", "deleted": order_no})


@app.route("/api/orders/<order_no>/diagnosis", methods=["POST"])
def run_diagnosis(order_no):
    order = repo.get_order(order_no)
    if not order:
        return jsonify({"error": "订单不存在"}), 404
    repo.run_full_diagnosis(order)
    repo.update_order(order, run_diagnosis=False)
    return jsonify(order_to_response(order))


@app.route("/api/orders/<order_no>/completion", methods=["POST"])
def update_completion(order_no):
    order = repo.get_order(order_no)
    if not order:
        return jsonify({"error": "订单不存在"}), 404
    data = request.get_json(silent=True) or {}
    try:
        cr = float(data.get("completion_rate", order.completion_rate))
    except (TypeError, ValueError):
        return jsonify({"error": "完工率必须是数字"}), 400
    status_str = data.get("status")
    order.completion_rate = cr
    if status_str:
        try:
            order.status = OrderStatus(status_str)
        except ValueError:
            return jsonify({"error": f"无效订单状态：{status_str}"}), 400
    accounting_status_str = data.get("accounting_status")
    if accounting_status_str:
        try:
            order.accounting_status = AccountingStatus(accounting_status_str)
        except ValueError:
            return jsonify({"error": f"无效会计状态：{accounting_status_str}"}), 400
    repo.update_order(order)
    return jsonify(order_to_response(order))


@app.route("/api/documents/parse", methods=["POST"])
def parse_document():
    try:
        if "file" in request.files:
            file = request.files["file"]
            filename = file.filename or ""
            df = doc_parser.read_tabular_file(file, filename)
        else:
            data = request.get_json(silent=True) or {}
            df = pd.DataFrame(data.get("data", []))
            filename = data.get("filename", "")

        doc_type_str = (
            request.form.get("document_type")
            if "file" in request.files
            else data.get("document_type")
        )
        doc_type = None
        if doc_type_str:
            from core.models import DocumentType
            try:
                doc_type = DocumentType(doc_type_str)
            except ValueError:
                return jsonify({"error": f"无效单据类型：{doc_type_str}"}), 400
        result = doc_parser.parse_document(df, doc_type=doc_type, filename=filename)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sample/generate", methods=["POST"])
def generate_sample():
    data = request.get_json(silent=True) or {}
    try:
        count = int(data.get("count", 10))
    except (TypeError, ValueError):
        return jsonify({"error": "生成数量必须是整数"}), 400
    if not 1 <= count <= 100:
        return jsonify({"error": "生成数量必须在1到100之间"}), 400
    clear = bool(data.get("clear", False))
    if clear:
        repo.clear_all()
    gen = SampleDataGenerator(seed=data.get("seed", 42))
    # 传入已有单号，新数据从下一个序号续编，避免重复单号覆盖已有订单
    orders = gen.generate_orders(count=count, existing_order_nos={o.order_no for o in repo.list_orders()})
    repo.add_orders_batch(orders)
    return jsonify({
        "generated": len(orders),
        "order_nos": [o.order_no for o in orders]
    })


@app.route("/api/sample/materials", methods=["GET"])
def get_sample_materials():
    return jsonify(SampleDataGenerator.MATERIALS_LIBRARY)


@app.route("/api/sample/products", methods=["GET"])
def get_sample_products():
    return jsonify(SampleDataGenerator.PRODUCT_TEMPLATES)


@app.route("/api/sample/processes", methods=["GET"])
def get_sample_processes():
    return jsonify(SampleDataGenerator.PROCESSES_LIBRARY)


@app.route("/api/sample/expenses", methods=["GET"])
def get_sample_expenses():
    return jsonify(SampleDataGenerator.EXPENSES_LIBRARY)


def _dict_to_order(data: dict) -> ProductionOrder:
    sc = StandardCost()
    for m in data.get("standard_cost", {}).get("materials", []):
        sc.materials.append(MaterialItem(**m))
    for l in data.get("standard_cost", {}).get("labors", []):
        sc.labors.append(LaborItem(**l))
    for e in data.get("standard_cost", {}).get("expenses", []):
        sc.expenses.append(ExpenseItem(**e))

    ac = ActualCost()
    for m in data.get("actual_cost", {}).get("materials", []):
        ac.materials.append(MaterialItem(**m))
    for l in data.get("actual_cost", {}).get("labors", []):
        ac.labors.append(LaborItem(**l))
    for e in data.get("actual_cost", {}).get("expenses", []):
        ac.expenses.append(ExpenseItem(**e))

    status = OrderStatus(data.get("status", "未开工"))
    accounting_status = AccountingStatus(data.get("accounting_status", "待校验"))
    order = ProductionOrder(
        order_no=data["order_no"],
        product_name=data["product_name"],
        product_code=data.get("product_code", ""),
        spec=data.get("spec", ""),
        order_qty=float(data["order_qty"]),
        unit=data.get("unit", "件"),
        sales_price=float(data["sales_price"]),
        customer_name=data.get("customer_name", ""),
        start_date=data.get("start_date", ""),
        plan_delivery_date=data.get("plan_delivery_date", ""),
        actual_delivery_date=data.get("actual_delivery_date"),
        status=status,
        completion_rate=float(data.get("completion_rate", 0.0)),
        accounting_status=accounting_status,
        source_mode=data.get("source_mode", "实际业务"),
        standard_cost=sc,
        actual_cost=ac,
        documents=data.get("documents", []),
        created_at=data.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    return order


if __name__ == "__main__":
    print("=" * 60)
    print("  订单动态成本诊断智能体 - Flask API 后端")
    print("=" * 60)
    if len(repo.list_orders()) == 0:
        print("[1/2] 数据库为空，正在初始化示例数据...")
        gen = SampleDataGenerator(seed=2026)
        orders = gen.generate_orders(count=12)
        repo.add_orders_batch(orders)
        print(f"      ✅ 已生成 {len(orders)} 条示例订单")
    else:
        print(f"[1/2] ✅ 已加载现有数据：{len(repo.list_orders())} 条订单")
    port = int(os.environ.get("PORT", 5000))
    print(f"[2/2] 🚀 正在启动 API 服务，监听端口 {port} ...")
    print("")
    print("  🌐 健康检查接口：  http://localhost:{port}/api/health".format(port=port))
    print("  📊 仪表盘数据：    http://localhost:{port}/api/dashboard".format(port=port))
    print("  📋 订单列表：      http://localhost:{port}/api/orders".format(port=port))
    print("")
    print("  💡 提示：比赛演示用 Streamlit 前端 (端口8501) 即可，")
    print("           本 API 服务用于小程序/独立前端对接扩展")
    print("=" * 60)
    try:
        app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
    except OSError as e:
        print("")
        print(f"❌ 启动失败：端口 {port} 被占用或无法绑定。错误：{e}")
        print("   请关闭占用端口的程序后重试，或修改 app.py 底部 port 为其他端口")
        print("   如临时测试： set PORT=5001  然后再启动")
        input("按回车退出...")
        sys.exit(1)
