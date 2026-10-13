import sys
import os
import io
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

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta

from core.models import (
    ProductionOrder, OrderStatus, AccountingStatus, DocumentType, StandardCost,
    ActualCost, MaterialItem, LaborItem, ExpenseItem
)
from core.cost_accounting import CostAccountingEngine
from core.variance_analysis import VarianceAnalyzer, WarningEngine
from core.cost_drivers import COST_DRIVER_LIBRARY, analyze_order_cost_drivers
from core.production_priority import rank_orders, WEIGHTS, FACTOR_LABELS
from core.llm_advisor import generate_advice, llm_available
from data.repository import DataRepository
from data.document_parser import DocumentParser
from data.sample_data import SampleDataGenerator

st.set_page_config(
    page_title="订单动态成本诊断智能体 | 劳保企业数智会计",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded"
)

LEVEL_COLORS = {
    "正常": "#52c41a",
    "关注": "#faad14",
    "预警": "#fa8c16",
    "严重": "#f5222d"
}

COST_COLORS = {
    "直接材料": "#1890ff",
    "直接人工": "#52c41a",
    "制造费用": "#722ed1"
}


def init_session():
    if "repo" not in st.session_state:
        st.session_state.repo = DataRepository()
        # 仅首次运行（会话内还没有 repo）时才自动生成示例数据，
        # 否则用户清空数据后每次重跑都会被重新填充，导致清空永远无效
        if len(st.session_state.repo.list_orders()) == 0:
            with st.spinner("正在初始化劳保企业示例数据..."):
                gen = SampleDataGenerator(seed=2026)
                orders = gen.generate_orders(count=12)
                st.session_state.repo.add_orders_batch(orders)
    return st.session_state.repo


def format_money(v: float) -> str:
    if v is None:
        return "—"
    try:
        return f"¥{v:,.2f}"
    except Exception:
        return "¥0.00"


def format_pct(v: float) -> str:
    if v is None:
        return "—"
    try:
        return f"{v:.2f}%"
    except Exception:
        return "0.00%"


def format_var_rate(v) -> str:
    # 标准金额为0时差异率无意义，显示"—"而不是0.00%
    return "—" if v.standard_amount == 0 else format_pct(v.variance_rate)


def level_badge(text: str) -> str:
    color = LEVEL_COLORS.get(text, "#888")
    return f'<span style="background:{color};color:#fff;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600;">{text}</span>'


def columns_dataframe_empty(cols):
    return {c: [] for c in cols}


DEFAULT_WARNING_COUNT = {"正常": 0, "关注": 0, "预警": 0, "严重": 0}


def safe_warning_count(obj):
    """把 session_state 里可能被缓存成奇怪类型的 warning_count 统一成 dict"""
    if isinstance(obj, dict):
        out = {}
        for k in DEFAULT_WARNING_COUNT.keys():
            try:
                out[k] = int(obj.get(k, 0))
            except Exception:
                out[k] = 0
        return out
    return dict(DEFAULT_WARNING_COUNT)


def safe_stat(stats, key, default=0):
    """安全地取 stats 里的数值字段，防止缓存的脏数据导致崩溃"""
    try:
        v = stats.get(key, default)
        if isinstance(v, (int, float)):
            return v
        if isinstance(v, str):
            try:
                return float(v.replace(",", ""))
            except Exception:
                return default
        return default
    except Exception:
        return default


def safe_style_applymap(styler, func, subset=None):
    try:
        if subset:
            return styler.map(func, subset=subset)
        return styler.map(func)
    except Exception:
        try:
            if subset:
                return styler.applymap(func, subset=subset)
            return styler.applymap(func)
        except Exception:
            return styler


def st_toggle(label, value=False, key=None):
    try:
        return st.toggle(label, value=value, key=key)
    except Exception:
        return st.checkbox(label, value=value, key=key)


repo = init_session()
analyzer = VarianceAnalyzer()
warning_engine = WarningEngine()
cost_engine = CostAccountingEngine()
doc_parser = DocumentParser()
sample_gen = SampleDataGenerator()


with st.sidebar:
    st.title("🏭 订单动态成本诊断智能体")
    st.caption("🏭 中小劳保企业 · 数智会计系统")

    page = st.radio(
        "📋 功能导航",
        ["📊 成本总览", "📦 订单管理", "🔍 订单成本诊断", "📈 投产优先级参考", "📝 新建订单", "📄 单据智能识别", "⚙️ 数据管理"],
        index=0
    )

    st.markdown("---")
    st.markdown("**系统状态**")
    stats = repo.get_dashboard_stats()
    st.success(f"✅ 订单总数：{safe_stat(stats, 'total_orders', 0)}")
    war = safe_warning_count(stats.get("warning_count", DEFAULT_WARNING_COUNT))
    st.markdown(f"""
    <div style="font-size:13px;line-height:1.8;">
        🔴 严重：{war.get('严重',0)} &nbsp;&nbsp; 🟠 预警：{war.get('预警',0)}<br>
        🟡 关注：{war.get('关注',0)} &nbsp;&nbsp; 🟢 正常：{war.get('正常',0)}
    </div>
    """, unsafe_allow_html=True)


if page == "📊 成本总览":
    st.header("📊 成本总览看板")
    st.caption(f"数据更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    stats = repo.get_dashboard_stats()

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("订单总数", safe_stat(stats, "total_orders", 0),
                  delta=f"进行中 {safe_stat(stats, 'in_progress', 0)}")
    with c2:
        st.metric("订单预计收入", format_money(safe_stat(stats, "total_sales", 0)))
    with c3:
        st.metric("累计实际成本", format_money(safe_stat(stats, "total_cost_act", 0)),
                  delta=format_money(safe_stat(stats, "total_cost_act", 0) - safe_stat(stats, "total_cost_std_to_date", 0)),
                  delta_color="inverse")
    with c4:
        profit_delta = safe_stat(stats, "total_profit_expected", 0) - safe_stat(stats, "total_profit_std", 0)
        st.metric("预计总利润", format_money(safe_stat(stats, "total_profit_expected", 0)),
                  delta=format_money(profit_delta),
                  delta_color="normal" if profit_delta >= 0 else "inverse")
    with c5:
        avg_margin_delta = safe_stat(stats, "avg_margin_expected", 0) - safe_stat(stats, "avg_margin_std", 0)
        st.metric("预计平均毛利率", format_pct(safe_stat(stats, "avg_margin_expected", 0)),
                  delta=format_pct(avg_margin_delta),
                  delta_color="normal" if avg_margin_delta >= 0 else "inverse")
    st.caption(
        f"正式实际利润仅统计100%完工且会计已审批的订单："
        f"{int(safe_stat(stats, 'actual_profit_order_count', 0))} 单，"
        f"合计 {format_money(safe_stat(stats, 'total_profit_act', 0))}。"
    )

    st.markdown("---")

    col_l, col_m, col_r = st.columns([3, 3, 2])

    with col_l:
        st.subheader("📦 订单状态分布")
        status_list = ["未开工", "生产中", "已完工", "已交付"]
        status_colors = ["#bfbfbf", "#1890ff", "#52c41a", "#13c2c2"]
        status_values = [
            int(safe_stat(stats, "not_started", 0)),
            int(safe_stat(stats, "in_progress", 0)),
            int(safe_stat(stats, "completed", 0)),
            int(safe_stat(stats, "delivered", 0)),
        ]
        fig = go.Figure(data=[go.Pie(
            labels=status_list,
            values=status_values,
            hole=0.55,
            marker=dict(colors=status_colors),
            textinfo="label+value+percent",
            textfont_size=13
        )])
        fig.update_layout(height=320, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

    with col_m:
        st.subheader("💰 同期成本 vs 订单收入")
        orders = repo.list_orders()
        if orders:
            names = [o.order_no[-4:] for o in orders]
            fig = go.Figure()
            fig.add_bar(name="销售额", x=names, y=[float(o.sales_amount) for o in orders], marker_color="#1890ff")
            fig.add_bar(name="截至当前标准成本", x=names, y=[float(cost_engine.standard_cost_to_date(o).total_cost) for o in orders], marker_color="#52c41a")
            fig.add_bar(name="累计实际成本", x=names, y=[float(o.actual_cost.total_cost) for o in orders], marker_color="#f5222d")
            fig.update_layout(barmode="group", height=320, legend=dict(orientation="h", y=1.12),
                              xaxis_title="订单号（后4位）", yaxis_title="金额 (元)")
            st.plotly_chart(fig, use_container_width=True)

    with col_r:
        st.subheader("⚠️ 预警等级分布")
        war = safe_warning_count(stats.get("warning_count", DEFAULT_WARNING_COUNT))
        war_level_order = ["正常", "关注", "预警", "严重"]
        war_x = [k for k in war_level_order if k in war]
        war_y = [int(war[k]) for k in war_x]
        war_c = [LEVEL_COLORS.get(k, "#888") for k in war_x]
        fig = go.Figure(data=[go.Bar(
            x=war_x, y=war_y,
            marker_color=war_c,
            text=war_y, textposition="outside"
        )])
        fig.update_layout(height=320, showlegend=False,
                          xaxis_title="等级", yaxis_title="数量")
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("🧾 成本结构分析（按订单）")
        if orders:
            rows = []
            for o in orders:
                rows.append({
                    "订单": o.order_no[-4:],
                    "直接材料": o.actual_cost.total_material_cost,
                    "直接人工": o.actual_cost.total_labor_cost,
                    "制造费用": o.actual_cost.total_expense_cost,
                })
            df_stacked = pd.DataFrame(rows)
            fig = go.Figure()
            for cat, color in COST_COLORS.items():
                fig.add_bar(name=cat, x=df_stacked["订单"], y=df_stacked[cat], marker_color=color)
            fig.update_layout(barmode="stack", height=360,
                              legend=dict(orientation="h", y=1.1),
                              yaxis_title="金额 (元)")
            st.plotly_chart(fig, use_container_width=True)

    with right:
        st.subheader("🔔 最新预警中心")
        all_warnings = []
        for o in orders:
            for w in o.warnings:
                # 复制一份再加展示字段，避免污染订单对象里的原始预警数据
                all_warnings.append({**w, "order_no": o.order_no, "product_name": o.product_name})
        priority = {"严重": 0, "预警": 1, "关注": 2, "正常": 3}
        all_warnings.sort(key=lambda x: priority.get(x.get("level", "正常"), 99))

        if all_warnings:
            for w in all_warnings[:10]:
                lv = w.get("level", "关注")
                with st.container():
                    st.markdown(f"""
                    <div style="border-left:4px solid {LEVEL_COLORS.get(lv,'#888')};padding:8px 12px;margin-bottom:6px;background:#fafafa;border-radius:4px;">
                        <div style="display:flex;justify-content:space-between;align-items:center;">
                            <strong style="font-size:13px;">{w['order_no']} · {w['product_name']}</strong>
                            {level_badge(lv)}
                        </div>
                        <div style="font-size:12px;color:#555;margin-top:4px;">{w.get('message','')}</div>
                    </div>
                    """, unsafe_allow_html=True)
        else:
            st.info("暂无预警信息，所有订单状态正常 🎉")

elif page == "📦 订单管理":
    st.header("📦 生产订单管理")

    f1, f2, f3, f4 = st.columns([2, 2, 2, 1])
    with f1:
        kw = st.text_input("🔎 关键词（订单号/产品/客户）", placeholder="输入搜索...")
    with f2:
        status_list = ["全部", "未开工", "生产中", "已完工", "已交付"]
        sel_status = st.selectbox("📌 订单状态", status_list)
    with f3:
        profit_options = ["全部", "盈利", "亏损", "成本超支"]
        sel_profit = st.selectbox("💰 筛选", profit_options)
    with f4:
        st.markdown("<br>", unsafe_allow_html=True)
        refresh = st.button("🔄 刷新", use_container_width=True)

    if sel_status == "全部":
        orders = repo.list_orders(keyword=kw)
    else:
        orders = repo.list_orders(status=OrderStatus(sel_status), keyword=kw)

    if sel_profit == "盈利":
        orders = [o for o in orders if (cost_engine.expected_profit(o) or 0) > 0]
    elif sel_profit == "亏损":
        orders = [o for o in orders if cost_engine.expected_profit(o) is not None and cost_engine.expected_profit(o) < 0]
    elif sel_profit == "成本超支":
        orders = [o for o in orders if o.actual_cost.total_cost > cost_engine.standard_cost_to_date(o).total_cost]

    st.caption(f"📋 共筛选出 **{len(orders)}** 条订单")

    if orders:
        rows = []
        for o in orders:
            unit = cost_engine.unit_cost_analysis(o)
            current_standard = cost_engine.standard_cost_to_date(o)
            cost_var = o.actual_cost.total_cost - current_standard.total_cost
            max_w = "正常"
            if o.warnings:
                pr = {"正常": 0, "关注": 1, "预警": 2, "严重": 3}
                max_w = max(o.warnings, key=lambda w: pr.get(w.get("level", "正常"), 0)).get("level", "正常")
            rows.append({
                "订单号": o.order_no,
                "产品名称": o.product_name,
                "规格": o.spec,
                "数量": f"{o.order_qty:,.0f} {o.unit}",
                "客户": o.customer_name,
                "状态": o.status.value,
                "会计状态": o.accounting_status.value,
                "完工率": format_pct(o.completion_rate * 100),
                "销售单价": format_money(o.sales_price),
                "标准单位成本": format_money(unit["standard_unit_cost"]),
                "预计/实际单位成本": format_money(unit["actual_unit_cost"]),
                "同期成本差异额": format_money(cost_var),
                "标准毛利率": format_pct(unit["gross_margin_rate_standard"]),
                "预计毛利率": format_pct(unit["expected_margin_rate"]),
                "预计利润": format_money(unit["expected_profit"]),
                "正式实际利润": format_money(o.actual_profit),
                "预警等级": max_w,
                "计划交付": o.plan_delivery_date,
            })
        df = pd.DataFrame(rows)

        def color_warning(val):
            return f"background-color: {LEVEL_COLORS.get(val, '')}; color: white; font-weight: 600;"

        styled = safe_style_applymap(df.style, color_warning, subset=["预警等级"])
        st.dataframe(styled, use_container_width=True, height=520, hide_index=True)
    else:
        st.warning("暂无符合条件的订单，请调整筛选条件")

elif page == "🔍 订单成本诊断":
    st.header("🔍 订单动态成本诊断")

    orders = repo.list_orders()
    if not orders:
        st.info("暂无订单数据，请先在数据管理中生成示例数据")
        st.stop()

    order_map = {f"{o.order_no} · {o.product_name} · {o.customer_name}": o for o in orders}
    sel = st.selectbox("选择要诊断的订单", list(order_map.keys()))
    order = order_map[sel]
    order = repo.get_order(order.order_no)

    st.markdown("---")

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        st.metric("订单号", order.order_no)
    with c2:
        st.metric("状态", order.status.value)
    with c3:
        st.metric("完工率", format_pct(order.completion_rate * 100))
    with c4:
        st.metric("订单数量", f"{order.order_qty:,.0f} {order.unit}")
    with c5:
        st.metric("销售单价", format_money(order.sales_price))
    with c6:
        analysis = cost_engine.unit_cost_analysis(order)
        margin = analysis["expected_margin_rate"]
        st.metric(
            "正式实际毛利率" if order.is_actual_profit_available else "预计毛利率",
            format_pct(margin),
        )

    st.subheader("📋 订单基本信息")
    with st.expander("查看订单详情", expanded=True):
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.write(f"**产品编码：** {order.product_code}")
            st.write(f"**产品名称：** {order.product_name}")
            st.write(f"**规格型号：** {order.spec}")
        with col_b:
            st.write(f"**客户名称：** {order.customer_name}")
            st.write(f"**开工日期：** {order.start_date}")
            st.write(f"**计划交付：** {order.plan_delivery_date}")
        with col_c:
            st.write(f"**实际交付：** {order.actual_delivery_date or '—'}")
            st.write(f"**订单预计收入：** {format_money(order.sales_amount)}")
            st.write(f"**会计状态：** {order.accounting_status.value}")
            st.write(f"**数据来源：** {order.source_mode}")
            st.write(f"**创建时间：** {order.created_at}")

    st.markdown("---")
    comparison_standard = cost_engine.standard_cost_to_date(order)
    comparison_label = "整单标准成本" if order.completion_rate >= 1 else "截至当前标准成本"
    st.subheader(f"💰 成本三要素对比（{comparison_label} vs 累计实际）")

    col_chart, col_table = st.columns([3, 2])
    with col_chart:
        categories = ["直接材料", "直接人工", "制造费用"]
        std_vals = [comparison_standard.total_material_cost,
                    comparison_standard.total_labor_cost,
                    comparison_standard.total_expense_cost]
        act_vals = [order.actual_cost.total_material_cost,
                    order.actual_cost.total_labor_cost,
                    order.actual_cost.total_expense_cost]

        fig = go.Figure()
        fig.add_bar(name=comparison_label, x=categories, y=std_vals, marker_color="#1890ff", text=[format_money(v) for v in std_vals])
        fig.add_bar(name="累计实际成本", x=categories, y=act_vals, marker_color="#f5222d", text=[format_money(v) for v in act_vals])
        fig.update_traces(textposition="outside", textfont_size=11)
        fig.update_layout(barmode="group", height=380, legend_title="成本类型",
                          title="三大成本项目对比分析",
                          yaxis_title="金额 (元)")
        st.plotly_chart(fig, use_container_width=True)

        fig2 = make_subplots(rows=1, cols=2, specs=[[{"type": "domain"}, {"type": "domain"}]],
                              subplot_titles=[comparison_label + "结构", "累计实际成本结构"])
        colors = [COST_COLORS[c] for c in categories]
        fig2.add_trace(go.Pie(labels=categories, values=std_vals, marker_colors=colors, name="标准"), 1, 1)
        fig2.add_trace(go.Pie(labels=categories, values=act_vals, marker_colors=colors, name="实际"), 1, 2)
        fig2.update_traces(textinfo="percent+label", hole=0.4)
        fig2.update_layout(height=320, showlegend=False)
        st.plotly_chart(fig2, use_container_width=True)

    with col_table:
        unit_ana = cost_engine.unit_cost_analysis(order)
        rows = []
        for cat in categories:
            if cat == "直接材料":
                s, a = comparison_standard.total_material_cost, order.actual_cost.total_material_cost
                su, au = unit_ana["standard_material_unit"], unit_ana["actual_material_unit"]
            elif cat == "直接人工":
                s, a = comparison_standard.total_labor_cost, order.actual_cost.total_labor_cost
                su, au = unit_ana["standard_labor_unit"], unit_ana["actual_labor_unit"]
            else:
                s, a = comparison_standard.total_expense_cost, order.actual_cost.total_expense_cost
                su, au = unit_ana["standard_expense_unit"], unit_ana["actual_expense_unit"]
            var = a - s
            rate = (var / s * 100) if s != 0 else 0
            rows.append({
                "成本项目": cat,
                comparison_label: format_money(s),
                "累计实际总额": format_money(a),
                "差异额": format_money(var),
                "差异率": format_pct(rate),
                "标准单位": format_money(su),
                "预计/实际单位": format_money(au)
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        st.markdown("---")
        full_standard = order.standard_cost.total_cost
        total_std = comparison_standard.total_cost
        total_act = order.actual_cost.total_cost
        total_var = total_act - total_std
        expected_profit = unit_ana.get("expected_profit")

        st.subheader("📊 订单成本汇总")
        col_a, col_b = st.columns(2)
        with col_a:
            st.metric("整单标准成本", format_money(full_standard))
            if order.completion_rate < 1:
                st.metric(comparison_label, format_money(total_std))
            st.metric("累计实际成本", format_money(total_act))
            st.metric("标准利润", format_money(order.standard_profit))
        with col_b:
            st.metric("同期成本差异", format_money(total_var),
                       delta=f"{'超支' if total_var > 0 else '节约'}",
                       delta_color="inverse" if total_var > 0 else "normal")
            st.metric("预计利润", format_money(expected_profit))
            st.metric("正式实际利润", format_money(order.actual_profit))
            st.metric("标准毛利率", format_pct(unit_ana['gross_margin_rate_standard']))
            st.metric("预计毛利率", format_pct(unit_ana['expected_margin_rate']))

        if 0 < order.completion_rate < 1.0 and order.actual_cost.total_cost > 0:
            proj = cost_engine.project_final_cost(order.actual_cost, order.completion_rate)
            proj_profit = order.sales_amount - proj.total_cost
            proj_var_pct = format_pct((proj.total_cost - full_standard) / full_standard * 100 if full_standard else 0)
            st.info(
                f"🔮 **按当前完工率 ({order.completion_rate*100:.1f}%) 推算最终成本**\n\n"
                f"- 预计总成本：**{format_money(proj.total_cost)}**（较整单标准 {proj_var_pct}）\n"
                f"- 预计最终利润：**{format_money(proj_profit)}**"
            )

    st.markdown("---")
    st.subheader("📉 明细差异分析与规则诊断")

    tab_mat, tab_lab, tab_exp, tab_sum = st.tabs(["🧵 直接材料差异", "👷 直接人工差异", "🏭 制造费用差异", "📋 诊断汇总"])

    def show_var_table(variances, category_filter):
        rows = []
        for v in variances:
            if v.category != category_filter:
                continue
            rows.append({
                "项目名称": v.item_name,
                "标准金额": format_money(v.standard_amount),
                "实际金额": format_money(v.actual_amount),
                "差异额": format_money(v.variance_amount),
                "差异率": format_var_rate(v),
                "浪费类型": v.waste_type or "—",
                "差异分解": "；".join(f"{k} {format_money(val)}" for k, val in v.components.items()) or "—",
            })
        df = pd.DataFrame(rows)
        if not df.empty:
            def highlight_var(val):
                if isinstance(val, str) and "%" in val:
                    try:
                        num = float(val.replace("%", ""))
                        if num > 8:
                            return "background-color: #ffccc7; color: #cf1322; font-weight: 600;"
                        elif num > 3:
                            return "background-color: #ffe7ba; color: #d46b08;"
                        elif num < -3:
                            return "background-color: #d9f7be; color: #389e0d;"
                    except Exception:
                        pass
                return ""
            styled = safe_style_applymap(df.style, highlight_var, subset=["差异率"])
            st.dataframe(styled, use_container_width=True, hide_index=True)

        for v in variances:
            if v.category == category_filter and v.item_name.startswith("【"):
                continue
            if v.category == category_filter and (v.cause_analysis or abs(v.variance_rate) >= 3):
                lv = "严重" if abs(v.variance_rate) >= 15 else ("预警" if abs(v.variance_rate) >= 8 else ("关注" if abs(v.variance_rate) >= 3 else "正常"))
                with st.expander(f"{'🔴' if lv=='严重' else '🟠' if lv=='预警' else '🟡' if lv=='关注' else '🟢'} {v.item_name} — 差异 {format_var_rate(v)}", expanded=lv in ("严重", "预警")):
                    st.markdown(f"**差异额：** {format_money(v.variance_amount)} &nbsp; | &nbsp; **差异率：** {format_var_rate(v)}")
                    if v.waste_type:
                        icon = "🔧" if v.waste_type == "工艺性浪费" else "📋"
                        st.markdown(f"**{icon} 浪费类型：** {v.waste_type}")
                    if v.cause_analysis:
                        st.markdown(f"**🔎 原因分析：** {v.cause_analysis}")
                    if v.suggestion:
                        st.markdown(f"**💡 改进建议：** {v.suggestion}")

    with tab_mat:
        show_var_table(order.variances, "直接材料")
    with tab_lab:
        show_var_table(order.variances, "直接人工")
    with tab_exp:
        show_var_table(order.variances, "制造费用")
    with tab_sum:
        sum_rows = []
        for v in order.variances:
            if v.item_name.startswith("【"):
                sum_rows.append({
                    "成本大类": v.category,
                    "项目": v.item_name.replace("【", "").replace("】", ""),
                    "标准金额": format_money(v.standard_amount),
                    "实际金额": format_money(v.actual_amount),
                    "差异额": format_money(v.variance_amount),
                    "差异率": format_var_rate(v),
                    "浪费类型": v.waste_type or "—",
                    "主要原因": v.cause_analysis
                })
        st.dataframe(pd.DataFrame(sum_rows), use_container_width=True, hide_index=True)

        # 浪费根源二级归因（工艺性/管理性）统计——呼应修订版计划书 3.3.3 损耗差异归因
        st.markdown("**🏷️ 浪费根源二级归因（工艺性 vs 管理性）**")
        waste_sum = analyzer.summarize_waste(order)
        waste_total = waste_sum["工艺性浪费"] + waste_sum["管理性浪费"] + waste_sum["未归类超支"]
        wc1, wc2, wc3 = st.columns(3)
        with wc1:
            st.metric(
                "🔧 工艺性浪费（生产作业过程）",
                format_money(waste_sum["工艺性浪费"]),
                delta=f"占超支 {waste_sum['工艺性浪费']/waste_total*100:.0f}%" if waste_total else "无超支",
            )
        with wc2:
            st.metric(
                "📋 管理性浪费（管理控制缺陷）",
                format_money(waste_sum["管理性浪费"]),
                delta=f"占超支 {waste_sum['管理性浪费']/waste_total*100:.0f}%" if waste_total else "无超支",
            )
        with wc3:
            st.metric("❓ 未归类超支", format_money(waste_sum["未归类超支"]))
        if waste_total > 0:
            if waste_sum["工艺性浪费"] >= waste_sum["管理性浪费"]:
                st.info("💡 该订单超支主要来自**工艺性浪费**（生产流水线作业过程）：建议从工艺损耗、返工率、工序节拍、设备状态入手管控。")
            else:
                st.info("💡 该订单超支主要来自**管理性浪费**（管理控制缺陷）：建议从采购价格、定额标准、费用预算、排班管理入手管控。")

    st.markdown("---")
    st.subheader("🧭 作业成本动因分析（ABC）")
    st.caption("作业成本法视角：产品消耗作业、作业消耗资源。下表把本订单实际成本按成本动因逐项归集，识别驱动成本的作业动因。")
    driver_rows = analyze_order_cost_drivers(order)
    if driver_rows:
        drv_df = pd.DataFrame(driver_rows)
        st.dataframe(drv_df, use_container_width=True, hide_index=True)
        top_drv = drv_df.iloc[0]
        st.caption(f"🔎 本订单成本的最大驱动项为「{top_drv['成本项目']}」（{top_drv['对应作业']}，动因：{top_drv['成本动因']}），占订单成本 {top_drv['占订单成本比重(%)']}%，管控要点：{top_drv['管控要点']}")

    st.markdown("---")
    st.subheader("🔔 预警信息汇总")
    if order.warnings:
        warn_df = pd.DataFrame([{
            "类型": w.get("type", ""),
            "等级": w.get("level", ""),
            "类别": w.get("category", ""),
            "项目": w.get("item", ""),
            "预警消息": w.get("message", ""),
            "建议措施": w.get("suggestion", "")
        } for w in order.warnings])

        def color_level(val):
            return f"background-color: {LEVEL_COLORS.get(val, '')}; color: white; font-weight: 600;"
        st.dataframe(safe_style_applymap(warn_df.style, color_level, subset=["等级"]), use_container_width=True, hide_index=True)
    else:
        st.success("✅ 当前订单无预警，一切正常！")

    st.markdown("---")
    st.subheader("🤖 智能诊断意见（DeepSeek 大模型）")
    st.caption(
        "大模型仅把规则引擎产出的结构化诊断结果转译为管理建议，所有金额均来自本地确定性核算，"
        "模型不参与任何数字计算；未配置密钥或网络不可用时自动降级为本地规则建议，页面不受影响。"
    )
    _advice_key = f"ai_advice_{order.order_no}"
    _fp = f"{order.actual_cost.total_cost:.2f}|{order.completion_rate:.4f}|{len(order.warnings)}"
    if st.button("生成 AI 诊断意见", key="btn_ai_advice", type="primary"):
        with st.spinner("DeepSeek 正在结合诊断数据撰写管理建议…"):
            advice_md, advice_src = generate_advice(
                order, analyzer.summarize_waste(order), unit_ana, driver_rows
            )
        st.session_state[_advice_key] = (advice_md, advice_src, _fp)
    _cached_advice = st.session_state.get(_advice_key)
    if _cached_advice:
        advice_md, advice_src, cached_fp = _cached_advice
        if cached_fp != _fp:
            st.caption("⚠️ 订单数据已更新，以下为历史建议，可点击按钮重新生成。")
        if advice_src == "deepseek":
            st.success("建议来源：DeepSeek 大模型（结果已按数据指纹缓存，重跑页面不重复调用）")
        else:
            st.info("建议来源：本地规则模板（当前未配置密钥或网络不可用，已自动降级）")
        st.markdown(advice_md)
    elif not llm_available():
        st.caption("当前未配置 DeepSeek 密钥——点击上方按钮仍会生成本地规则建议。")

    st.markdown("---")
    st.subheader("📄 关联原始单据")
    if order.documents:
        doc_rows = []
        for d in order.documents:
            icon_map = {
                "销售订单": "📋", "采购订单": "🛒", "领料单": "📦",
                "工时卡": "⏱️", "制造费用单": "💰", "产品入库单": "📥"
            }
            doc_rows.append({
                "单据": f"{icon_map.get(d['type'], '📄')} {d['type']}",
                "单据编号": d["doc_no"],
                "状态": d["status"],
                "日期": d["date"]
            })
        st.dataframe(pd.DataFrame(doc_rows), use_container_width=True, hide_index=True)

    st.markdown("---")
    with st.expander("🛠️ 更新订单进度与会计状态", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            new_cr = st.slider("完工率 (%)", 0, 100, int(order.completion_rate * 100))
        with c2:
            status_options = [s.value for s in OrderStatus]
            new_status = st.selectbox("订单状态", status_options, index=status_options.index(order.status.value))
        with c3:
            accounting_options = [s.value for s in AccountingStatus]
            new_accounting_status = st.selectbox(
                "会计状态",
                accounting_options,
                index=accounting_options.index(order.accounting_status.value),
            )
        actual_delivery_text = st.text_input(
            "实际交付日期（已交付必填，YYYY-MM-DD）",
            value=order.actual_delivery_date or "",
        )
        st.caption("只有100%完工的订单才能进入“会计已审批/正式输出”，届时才展示正式实际利润。")
        if st.button("💾 更新并重新诊断", type="primary"):
            try:
                order.completion_rate = new_cr / 100.0
                order.status = OrderStatus(new_status)
                order.accounting_status = AccountingStatus(new_accounting_status)
                order.actual_delivery_date = actual_delivery_text.strip() or None
                repo.update_order(order)
                st.success("✅ 已更新，正在重新诊断...")
                st.rerun()
            except Exception as exc:
                st.error(f"❌ 更新失败：{exc}")

elif page == "📈 投产优先级参考":
    st.header("📈 订单投产优先级参考（运筹学子模块）")
    st.warning(
        "⚠️ 功能边界：本模块为**经营决策智能体内部运筹学子模块**，输出的投产优先级为**结构化经营参考表**，"
        "仅供管理层排产决策参考，**不下发生产工单、不直接控制车间设备**；完整运筹约束求解属于远期迭代。",
        icon="📌",
    )

    orders_all = repo.list_orders()
    if not orders_all:
        st.info("暂无订单数据，请先在数据管理中生成示例数据")
        st.stop()

    with st.expander("🧮 评分模型说明（多因子加权评分）", expanded=False):
        st.markdown(
            "针对柔性按单生产场景，综合订单型号盈利能力、成本投入与投入产出情况，"
            "对每笔在产/待产订单按以下因子加权评分（0-100分），按总分排序输出投产优先级："
        )
        w_df = pd.DataFrame([
            {"评分因子": FACTOR_LABELS[k], "权重": f"{WEIGHTS[k]*100:.0f}%", "因子含义": m}
            for k, m in [
                ("margin", "预计毛利率越高，产出效益越好"),
                ("urgency", "距计划交付越近且剩余完工量越大越紧迫，逾期未完成最高"),
                ("cost_control", "同期成本差异率越低（成本控制越好）得分越高"),
                ("progress", "完工进度与时间进度的偏离，滞后越多越需要资源倾斜"),
                ("roi", "整单预计投入产出比（订单收入/整单标准成本），投入产出效益"),
            ]
        ])
        st.dataframe(w_df, use_container_width=True, hide_index=True)
        st.caption("评分 ≥70 优先投产；52-70 正常排产；<52 暂缓投产（先复盘降本）。已完工/已交付订单不参与排序。")

    ranked = rank_orders(orders_all)
    pending_ranked = [r for r in ranked if r["优先级"] != "—"]

    st.subheader(f"📋 投产优先级参考表（在产/待产 {len(pending_ranked)} 单）")
    rank_df = pd.DataFrame(ranked)
    if not rank_df.empty:
        def _color_advice(val):
            if val == "优先投产":
                return "background-color: #d9f7be; color: #389e0d; font-weight: 600;"
            if val == "正常排产":
                return "background-color: #e6f4ff; color: #1890ff;"
            if val == "暂缓投产（先复盘）":
                return "background-color: #ffe7ba; color: #d46b08;"
            return ""
        styled_rank = safe_style_applymap(rank_df.style, _color_advice, subset=["投产建议"])
        st.dataframe(styled_rank, use_container_width=True, hide_index=True)

        # 综合评分横向条形图（仅未完工订单）
        if pending_ranked:
            plot_rows = list(reversed(pending_ranked))
            fig = go.Figure()
            advice_color = {"优先投产": "#52c41a", "正常排产": "#1890ff", "暂缓投产（先复盘）": "#fa8c16"}
            fig.add_bar(
                y=[f"{r['订单号'][-4:]} {r['产品名称']}" for r in plot_rows],
                x=[float(r["综合评分"]) for r in plot_rows],
                orientation="h",
                marker_color=[advice_color.get(r["投产建议"], "#888") for r in plot_rows],
                text=[r["综合评分"] for r in plot_rows],
                textposition="outside",
            )
            fig.update_layout(height=max(320, 36 * len(plot_rows) + 80), showlegend=False,
                              xaxis_title="综合评分", yaxis_title="订单")
            st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")
    st.subheader("🧮 优先投产批次建议汇总")
    if pending_ranked:
        priority_orders = [r for r in pending_ranked if r["投产建议"] == "优先投产"]
        normal_orders = [r for r in pending_ranked if r["投产建议"] == "正常排产"]
        defer_orders = [r for r in pending_ranked if r["投产建议"] == "暂缓投产（先复盘）"]
        pc1, pc2, pc3 = st.columns(3)
        with pc1:
            avg_margin = (
                round(sum(o["预计毛利率(%)"] for o in priority_orders) / len(priority_orders), 1)
                if priority_orders else 0
            )
            st.metric("🟢 优先投产", f"{len(priority_orders)} 单",
                      f"平均毛利率 {avg_margin}%" if priority_orders else "无")
        with pc2:
            st.metric("🔵 正常排产", f"{len(normal_orders)} 单")
        with pc3:
            st.metric("🟠 暂缓投产", f"{len(defer_orders)} 单")
        if priority_orders:
            st.success(
                "💡 建议优先保障资源："
                + "、".join(f"{r['订单号']}（{r['产品名称']}，评分{r['综合评分']}）" for r in priority_orders[:5])
            )

    st.markdown("---")
    # 导出结构化参考表（Excel），供经营层线下评审使用
    export_df = pd.DataFrame(pending_ranked)
    if not export_df.empty:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            export_df.to_excel(writer, index=False, sheet_name="投产优先级参考表")
        st.download_button(
            "⬇️ 导出投产优先级参考表（Excel）",
            data=buf.getvalue(),
            file_name=f"订单投产优先级参考表_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.caption("参考表仅供经营决策参考，不下发生产工单。")

elif page == "📝 新建订单":
    st.header("📝 新建生产订单")
    st.info("💡 可基于下方劳保产品模板快速创建，或手动自定义成本要素")

    use_template = st_toggle("使用产品模板快速创建", value=True)

    if use_template:
        tmpl_map = {f"{p['code']} | {p['name']} | {p['spec']}": p for p in SampleDataGenerator.PRODUCT_TEMPLATES}
        sel_tmpl = st.selectbox("选择产品模板", list(tmpl_map.keys()))
        tmpl = tmpl_map[sel_tmpl]
    else:
        tmpl = None

    has_actual = st_toggle("录入实际成本数据", value=False, key="new_order_has_actual")
    with st.form("new_order_form", clear_on_submit=False):
        st.subheader("一、订单基本信息")
        col1, col2, col3 = st.columns(3)
        with col1:
            order_no = st.text_input("订单号 *", value=f"PO{datetime.now().strftime('%Y%m%d%H%M%S')}")
            product_code = st.text_input("产品编码", value=tmpl["code"] if tmpl else "")
            customer = st.selectbox("客户名称", SampleDataGenerator.CUSTOMERS)
            source_mode = st.selectbox("数据来源", ["实际业务", "示例/仿真"], index=0)
        with col2:
            product_name = st.text_input("产品名称 *", value=tmpl["name"] if tmpl else "")
            order_qty = st.number_input("订单数量 *", min_value=1, value=500)
            start_date = st.date_input("开工日期", value=datetime.now())
        with col3:
            spec = st.text_input("规格型号", value=tmpl["spec"] if tmpl else "")
            sales_price = st.number_input("销售单价 (元) *", min_value=0.0, value=tmpl["base_price"] if tmpl else 0.0, step=0.5)
            plan_date = st.date_input("计划交付日期", value=datetime.now() + timedelta(days=30))
            unit = st.text_input("计量单位", value=tmpl["unit"] if tmpl else "件")

        st.markdown("---")
        st.subheader("二、标准成本（BOM + 工艺路线）")

        st.markdown("**2.1 直接材料标准（按单位产品用量×订单数量）**")
        mat_defaults = []
        if tmpl:
            for mc, pu in tmpl["materials"]:
                mat_info = next((m for m in SampleDataGenerator.MATERIALS_LIBRARY if m["code"] == mc), None)
                if mat_info:
                    mat_defaults.append({
                        "编码": mat_info["code"], "名称": mat_info["name"], "规格": mat_info["spec"],
                        "单位": mat_info["unit"], "单位用量": pu, "单价": mat_info["base_price"]
                    })
        mat_df = st.data_editor(
            pd.DataFrame(mat_defaults if mat_defaults else columns_dataframe_empty(["编码", "名称", "规格", "单位", "单位用量", "单价"])),
            num_rows="dynamic", use_container_width=True, key="mat_editor"
        )

        st.markdown("**2.2 直接人工标准（按工序）**")
        lab_defaults = []
        if tmpl:
            for pi in tmpl["processes"]:
                p = SampleDataGenerator.PROCESSES_LIBRARY[pi]
                lab_defaults.append({
                    "工序名称": p["name"], "工种": p["work_type"],
                    "单位工时(h)": p["base_hours"], "工资率(元/h)": p["base_rate"]
                })
        lab_df = st.data_editor(
            pd.DataFrame(lab_defaults if lab_defaults else columns_dataframe_empty(["工序名称", "工种", "单位工时(h)", "工资率(元/h)"])),
            num_rows="dynamic", use_container_width=True, key="lab_editor"
        )

        st.markdown("**2.3 制造费用标准（按订单分摊）**")
        exp_defaults = []
        if tmpl:
            for e in SampleDataGenerator.EXPENSES_LIBRARY:
                exp_defaults.append({
                    "费用类型": e["type"], "费用名称": e["name"],
                    "分配基础": "机器工时+人工工时", "单位费用(元/件)": e["base_amount"]
                })
        exp_df = st.data_editor(
            pd.DataFrame(exp_defaults if exp_defaults else columns_dataframe_empty(["费用类型", "费用名称", "分配基础", "单位费用(元/件)"])),
            num_rows="dynamic", use_container_width=True, key="exp_editor"
        )

        st.markdown("---")
        st.subheader("三、实际成本发生情况（可选，可后续录入）")
        act_mat_df = act_lab_df = act_exp_df = pd.DataFrame()

        def _safe_copy_df(df, cols):
            try:
                if isinstance(df, pd.DataFrame):
                    return df.copy()
            except Exception:
                pass
            return pd.DataFrame(columns_dataframe_empty(cols))

        if has_actual:
            completion = st.slider("当前完工率 (%)", 1, 100, 50) / 100.0
            st.caption("💡 下表填写截至当前的累计实际用量、工时和费用；初始值按当前默认50%完工率生成，请按真实单据修正。")
            act_mat_seed = _safe_copy_df(mat_df, ["编码", "名称", "规格", "单位", "单位用量", "单价"])
            if "单位用量" in act_mat_seed:
                act_mat_seed["单位用量"] = pd.to_numeric(act_mat_seed["单位用量"], errors="coerce").fillna(0) * float(order_qty) * completion
            act_mat_df = st.data_editor(
                act_mat_seed,
                num_rows="dynamic", use_container_width=True, key="act_mat",
                column_config={"单位用量": st.column_config.NumberColumn(label="实际总用量"),
                               "单价": st.column_config.NumberColumn(label="实际采购单价")})
            act_lab_seed = _safe_copy_df(lab_df, ["工序名称", "工种", "单位工时(h)", "工资率(元/h)"])
            if "单位工时(h)" in act_lab_seed:
                act_lab_seed["单位工时(h)"] = pd.to_numeric(act_lab_seed["单位工时(h)"], errors="coerce").fillna(0) * float(order_qty) * completion
            act_lab_df = st.data_editor(
                act_lab_seed,
                num_rows="dynamic", use_container_width=True, key="act_lab",
                column_config={"单位工时(h)": st.column_config.NumberColumn(label="实际总工时(h)"),
                               "工资率(元/h)": st.column_config.NumberColumn(label="实际工资率(元/h)")})
            act_exp_seed = _safe_copy_df(exp_df, ["费用类型", "费用名称", "分配基础", "单位费用(元/件)"])
            if "单位费用(元/件)" in act_exp_seed:
                act_exp_seed["单位费用(元/件)"] = pd.to_numeric(act_exp_seed["单位费用(元/件)"], errors="coerce").fillna(0) * float(order_qty) * completion
            act_exp_df = st.data_editor(
                act_exp_seed,
                num_rows="dynamic", use_container_width=True, key="act_exp",
                column_config={"单位费用(元/件)": st.column_config.NumberColumn(label="实际总费用(元)")})
        else:
            completion = 0.0

        submitted = st.form_submit_button("✅ 创建订单并执行成本诊断", type="primary", use_container_width=True)

    if submitted:
        order_no = order_no.strip()
        if not order_no:
            st.error("❌ 订单号不能为空")
            st.stop()
        if repo.exists(order_no):
            st.error(f"❌ 订单号 {order_no} 已存在，请更换订单号，避免覆盖已有订单")
            st.stop()
        try:
            sc = StandardCost()
            for _, r in mat_df.iterrows():
                try:
                    qty = float(r["单位用量"]) * order_qty
                    price = float(r["单价"])
                    sc.materials.append(MaterialItem(
                        material_code=str(r.get("编码", "")),
                        material_name=str(r["名称"]),
                        spec=str(r.get("规格", "")),
                        unit=str(r.get("单位", "")),
                        qty=round(qty, 4), unit_price=round(price, 4),
                        amount=round(qty * price, 2)
                    ))
                except Exception:
                    continue
            for _, r in lab_df.iterrows():
                try:
                    hrs = float(r["单位工时(h)"]) * order_qty
                    rate = float(r["工资率(元/h)"])
                    sc.labors.append(LaborItem(
                        process_name=str(r["工序名称"]),
                        work_type=str(r.get("工种", "")),
                        hours=round(hrs, 4), hourly_rate=round(rate, 4),
                        amount=round(hrs * rate, 2)
                    ))
                except Exception:
                    continue
            for _, r in exp_df.iterrows():
                try:
                    amt = float(r["单位费用(元/件)"]) * order_qty
                    sc.expenses.append(ExpenseItem(
                        expense_type=str(r.get("费用类型", "")),
                        expense_name=str(r["费用名称"]),
                        allocation_base=str(r.get("分配基础", "")),
                        amount=round(amt, 2), allocated_amount=round(amt, 2)
                    ))
                except Exception:
                    continue

            ac = ActualCost()
            if has_actual:
                for _, r in act_mat_df.iterrows():
                    try:
                        qty = float(r["单位用量"])
                        price = float(r["单价"])
                        ac.materials.append(MaterialItem(
                            material_code=str(r.get("编码", "")),
                            material_name=str(r["名称"]),
                            spec=str(r.get("规格", "")),
                            unit=str(r.get("单位", "")),
                            qty=round(qty, 4), unit_price=round(price, 4),
                            amount=round(qty * price, 2)
                        ))
                    except Exception:
                        continue
                for _, r in act_lab_df.iterrows():
                    try:
                        hrs = float(r["单位工时(h)"])
                        rate = float(r["工资率(元/h)"])
                        ac.labors.append(LaborItem(
                            process_name=str(r["工序名称"]),
                            work_type=str(r.get("工种", "")),
                            hours=round(hrs, 4), hourly_rate=round(rate, 4),
                            amount=round(hrs * rate, 2)
                        ))
                    except Exception:
                        continue
                for _, r in act_exp_df.iterrows():
                    try:
                        amt = float(r["单位费用(元/件)"])
                        ac.expenses.append(ExpenseItem(
                            expense_type=str(r.get("费用类型", "")),
                            expense_name=str(r["费用名称"]),
                            allocation_base=str(r.get("分配基础", "")),
                            amount=round(amt, 2), allocated_amount=round(amt, 2)
                        ))
                    except Exception:
                        continue

            if completion >= 1:
                order_status = OrderStatus.COMPLETED
            elif completion > 0:
                order_status = OrderStatus.IN_PROGRESS
            else:
                order_status = OrderStatus.NOT_STARTED
            order = ProductionOrder(
                order_no=order_no,
                product_name=product_name,
                product_code=product_code,
                spec=spec,
                order_qty=float(order_qty),
                unit=unit,
                sales_price=float(sales_price),
                customer_name=customer,
                start_date=start_date.strftime("%Y-%m-%d"),
                plan_delivery_date=plan_date.strftime("%Y-%m-%d"),
                status=order_status,
                completion_rate=float(completion),
                accounting_status=AccountingStatus.PENDING_VALIDATION,
                source_mode=source_mode,
                standard_cost=sc,
                actual_cost=ac
            )
            repo.add_order(order)
            st.success(f"🎉 订单 {order_no} 创建成功！已自动完成成本差异分析与预警诊断。")
            st.markdown(f"""
            **诊断摘要：**
            - 标准总成本：{format_money(order.standard_cost.total_cost)}
            - 累计实际成本：{format_money(order.actual_cost.total_cost)}
            - 同期成本差异：{format_money(order.actual_cost.total_cost - cost_engine.standard_cost_to_date(order).total_cost)}
            - 预计利润：{format_money(cost_engine.expected_profit(order))}
            - 会计状态：{order.accounting_status.value}
            - 发现预警：**{len(order.warnings)}** 条
            """)
        except Exception as e:
            st.error(f"❌ 创建失败：{e}")



elif page == "📄 单据智能识别":
    st.header("📄 原始单据智能识别")
    st.info("💡 支持上传 Excel (.xlsx) 或 CSV 格式的原始单据，系统将自动识别单据类型、解析结构化数据并汇总统计。（.xls 旧格式请先另存为 .xlsx）\n\n可识别单据类型：销售订单、采购订单、领料单、工时卡、制造费用单、产品入库单")

    tab_upload, tab_template = st.tabs(["📤 上传并识别单据", "📋 单据模板示例"])
    with tab_upload:
        uploaded = st.file_uploader("拖放或选择单据文件", type=["xlsx", "csv"], accept_multiple_files=True)

        if uploaded:
            for f in uploaded:
                st.markdown(f"### 📄 {f.name}")
                try:
                    type_choice = st.selectbox(
                        "单据类型",
                        ["自动识别"] + [item.value for item in DocumentType],
                        key=f"doc_type_{f.name}_{getattr(f, 'size', 0)}",
                    )
                    df = doc_parser.read_tabular_file(f, f.name)
                    if type_choice == "自动识别":
                        doc_type = doc_parser.detect_document_type(df, f.name)
                        type_caption = "自动识别"
                    else:
                        doc_type = DocumentType(type_choice)
                        type_caption = "人工指定"
                    st.caption(f"🔍 {type_caption}单据类型：**{doc_type.value}** | 数据行数：{len(df)}")

                    result = doc_parser.parse_document(df, doc_type=doc_type, filename=f.name)

                    with st.expander("查看原始识别数据", expanded=False):
                        st.dataframe(df, use_container_width=True)

                    st.subheader("📊 解析汇总统计")
                    summ = result["summary"]
                    if summ:
                        cols = st.columns(min(4, max(2, len(summ))))
                        ci = 0
                        for k, v in summ.items():
                            label = k.replace("_", " ").replace("total", "总计").title()
                            with cols[ci % len(cols)]:
                                if isinstance(v, (int, float)):
                                    st.metric(label, f"{v:,.2f}" if isinstance(v, float) else v)
                                elif isinstance(v, list) and len(v) <= 5:
                                    st.metric(label + f" ({len(v)})", "、".join(str(x) for x in v[:3]) + ("..." if len(v) > 3 else ""))
                                else:
                                    st.metric(label, f"{len(v)} 项" if isinstance(v, list) else str(v)[:20])
                            ci += 1

                    st.success(f"✅ {f.name} 解析完成，已识别为【{doc_type.value}】")
                    st.caption("解析结果当前属于待复核证据；登记到订单不会自动计入正式成本。")
                    order_options = {"不关联订单": None}
                    for candidate in repo.list_orders():
                        order_options[f"{candidate.order_no} · {candidate.product_name}"] = candidate.order_no
                    selected_order_label = st.selectbox(
                        "登记到订单证据链",
                        list(order_options.keys()),
                        key=f"doc_order_{f.name}_{getattr(f, 'size', 0)}",
                    )
                    if st.button(
                        "登记单据证据（待人工复核）",
                        key=f"doc_register_{f.name}_{getattr(f, 'size', 0)}",
                        disabled=order_options[selected_order_label] is None,
                    ):
                        target_order = repo.get_order(order_options[selected_order_label])
                        if target_order is None:
                            st.error("关联订单不存在，请刷新后重试")
                        elif any(d.get("source_filename") == f.name for d in target_order.documents):
                            st.warning("该文件已登记到此订单，未重复写入")
                        else:
                            doc_no_col = next((c for c in df.columns if "单号" in str(c)), None)
                            doc_no = str(df.iloc[0][doc_no_col]) if doc_no_col is not None and len(df) else f.name
                            target_order.documents.append({
                                "doc_no": doc_no,
                                "type": doc_type.value,
                                "status": "待人工复核",
                                "date": datetime.now().strftime("%Y-%m-%d"),
                                "source_filename": f.name,
                                "summary": result.get("summary", {}),
                            })
                            if target_order.accounting_status == AccountingStatus.PENDING_VALIDATION:
                                target_order.accounting_status = AccountingStatus.PENDING_REVIEW
                            repo.update_order(target_order)
                            st.success(f"已登记到订单 {target_order.order_no}，状态为待人工复核")

                except Exception as e:
                    st.error(f"❌ {f.name} 解析失败：{e}")

    with tab_template:
        st.markdown("#### 📋 常用单据数据模板（点击下载参考）")

        template_map = {
            "采购订单模板": {
                "columns": ["采购单号", "供应商", "物料编码", "物料名称", "规格型号", "单位", "数量", "单价", "金额", "税率", "到货日期", "对应生产订单号"],
                "sample": [
                    ["PO-2026-0001", "北京塑料化工有限公司", "MAT-002", "ABS工程塑料粒", "25kg/袋", "kg", 225, 19.20, 4320.00, "13%", "2026-08-05", "PO2026080001"],
                    ["PO-2026-0001", "北京塑料化工有限公司", "MAT-011", "反光条", "5cm宽高亮", "米", 150, 4.95, 742.50, "13%", "2026-08-05", "PO2026080001"],
                ]
            },
            "领料单模板": {
                "columns": ["领料单号", "领料部门", "领料日期", "物料编码", "物料名称", "规格型号", "单位", "数量", "单价", "金额", "用途/生产订单号", "领料人"],
                "sample": [
                    ["MR-2026-0101", "一车间（注塑）", "2026-08-06", "MAT-002", "ABS工程塑料粒", "25kg/袋", "kg", 220, 19.20, 4224.00, "PO2026080001-安全帽", "张某某"],
                    ["MR-2026-0101", "一车间（注塑）", "2026-08-06", "MAT-010", "尼龙搭扣", "5cm宽", "米", 125, 2.55, 318.75, "PO2026080001-安全帽", "张某某"],
                ]
            },
            "工时卡模板": {
                "columns": ["工时卡号", "日期", "车间", "工序名称", "工种", "员工姓名", "工作内容/订单号", "实际工时(h)", "工资率(元/h)", "人工费(元)"],
                "sample": [
                    ["LT-0806-A", "2026-08-06", "一车间", "注塑成型", "机加工", "李某某", "PO2026080001-安全帽V型", 50, 33.5, 1675.00],
                    ["LT-0806-B", "2026-08-06", "二车间", "缝纫拼接", "人工", "王某某", "PO2026080001-安全帽内衬", 125, 26.8, 3350.00],
                ]
            },
            "制造费用单模板": {
                "columns": ["费用单号", "费用期间", "费用类型", "费用名称", "费用明细", "金额(元)", "分配方式", "受益订单/部门"],
                "sample": [
                    ["ME-2026-08", "2026-08", "变动制造费用", "水电费", "车间照明+设备用电", 1750.00, "机器工时分摊", "全厂生产订单"],
                    ["ME-2026-08", "2026-08", "固定制造费用", "设备折旧", "注塑机/缝纫机折旧", 2500.00, "工作量法", "全厂生产订单"],
                ]
            }
        }

        for name, tmpl in template_map.items():
            df_t = pd.DataFrame(tmpl["sample"], columns=tmpl["columns"])
            with st.expander(f"📑 {name}", expanded=False):
                st.dataframe(df_t, use_container_width=True, hide_index=True)
                csv = df_t.to_csv(index=False).encode("utf-8-sig")
                st.download_button(f"⬇️ 下载 {name} (.csv)", csv, file_name=f"{name}.csv", mime="text/csv")

elif page == "⚙️ 数据管理":
    st.header("⚙️ 数据管理 & 系统配置")

    tab1, tab2, tab3 = st.tabs(["🗂️ 示例数据生成", "📐 基础数据参考库", "🧹 数据清理"])

    with tab1:
        st.subheader("🗂️ 劳保企业订单示例数据生成")
        st.markdown("""
        一键生成符合中小劳保企业实际业务的生产订单数据，包含：
        - **6 种典型劳保产品**：安全帽、安全鞋、劳保手套、反光背心、护目镜、一次性防护服
        - **完整成本要素**：原材料（15种）、人工工序（8种）、制造费用（6项）
        - **动态完工进度**：未开工 / 生产中（含不同完工率）/ 已完工 / 已交付
        - **真实差异波动**：材料价格与用量差异、人工工时与工资率差异、制造费用差异
        - **10 家模拟客户**：覆盖建筑、能源、物流、制造等劳保需求密集行业
        """)

        c1, c2, c3 = st.columns(3)
        with c1:
            cnt = st.slider("生成订单数量", 3, 50, 15)
        with c2:
            seed = st.number_input("随机种子（保证可复现）", value=2026, step=1)
        with c3:
            clear_first = st.checkbox("先清空现有数据", value=False)
        if st.button("🚀 生成示例数据并执行全面诊断", type="primary", use_container_width=True):
            if clear_first:
                repo.clear_all()
            gen = SampleDataGenerator(seed=int(seed))
            # 传入已有单号，新数据从下一个序号续编，避免重复单号覆盖已有订单
            orders = gen.generate_orders(count=cnt, existing_order_nos={o.order_no for o in repo.list_orders()})
            repo.add_orders_batch(orders)
            st.success(f"✅ 成功生成 {len(orders)} 条示例订单，已完成成本诊断！")
            st.rerun()

        st.markdown("---")
        st.subheader("📈 当前数据概览")
        stats = repo.get_dashboard_stats()
        col_a, col_b, col_c, col_d = st.columns(4)
        with col_a:
            st.metric("订单总数", safe_stat(stats, "total_orders", 0))
        with col_b:
            st.metric("销售总额", format_money(safe_stat(stats, "total_sales", 0)))
        with col_c:
            st.metric("累计实际成本", format_money(safe_stat(stats, "total_cost_act", 0)))
        with col_d:
            war = safe_warning_count(stats.get("warning_count", DEFAULT_WARNING_COUNT))
            st.metric("总预警数", sum(war.values()) - war.get("正常", 0))

    with tab2:
        st.subheader("📐 劳保企业基础数据参考库")
        mat_df = pd.DataFrame(SampleDataGenerator.MATERIALS_LIBRARY)
        mat_df.columns = ["物料编码", "物料名称", "规格型号", "计量单位", "基准单价(元)"]
        with st.expander("🧵 原材料库（15种）", expanded=True):
            st.dataframe(mat_df, use_container_width=True, hide_index=True)

        prod_df = pd.DataFrame([{
            "产品编码": p["code"],
            "产品名称": p["name"],
            "规格": p["spec"],
            "单位": p["unit"],
            "物料数": len(p["materials"]),
            "工序数": len(p["processes"]),
            "基准售价(元)": p["base_price"]
        } for p in SampleDataGenerator.PRODUCT_TEMPLATES])
        with st.expander("🏷️ 产品模板库（6种劳保产品）", expanded=True):
            st.dataframe(prod_df, use_container_width=True, hide_index=True)

        proc_df = pd.DataFrame(SampleDataGenerator.PROCESSES_LIBRARY)
        proc_df.columns = ["工序名称", "工种类型", "单位产品基准工时(h)", "基准工资率(元/h)"]
        with st.expander("👷 标准工序库（8道工序）", expanded=False):
            st.dataframe(proc_df, use_container_width=True, hide_index=True)

        exp_df = pd.DataFrame(SampleDataGenerator.EXPENSES_LIBRARY)
        exp_df.columns = ["费用类别", "费用名称", "单位产品基准费用(元)"]
        with st.expander("🏭 制造费用库（6项）", expanded=False):
            st.dataframe(exp_df, use_container_width=True, hide_index=True)

        # 作业成本动因库——呼应修订版计划书 4.5 知识库新增内容
        driver_df = pd.DataFrame(COST_DRIVER_LIBRARY)
        driver_df.columns = ["成本大类", "成本项目", "对应作业", "成本动因", "动因类型", "管控要点"]
        with st.expander("🧭 作业成本动因库（ABC·9条）", expanded=True):
            st.caption("作业成本法视角：产品消耗作业、作业消耗资源。本库识别各项成本的驱动动因，"
                       "订单维度的动因归集见「订单成本诊断 → 作业成本动因分析」。")
            st.dataframe(driver_df, use_container_width=True, hide_index=True)

    with tab3:
        st.subheader("🧹 数据清理")
        st.warning("⚠️ 危险操作：以下操作将删除数据，不可恢复！请谨慎操作。")
        orders = repo.list_orders()
        if orders:
            ord_map = {f"{o.order_no} · {o.product_name}": o.order_no for o in orders}
            sel_del = st.selectbox("选择要删除的订单", list(ord_map.keys()))
            if st.button("🗑️ 删除指定订单"):
                if repo.delete_order(ord_map[sel_del]):
                    st.success(f"已删除：{sel_del}")
                    st.rerun()
        confirm_clear = st.checkbox("我已确认备份，并理解清空操作不可恢复", value=False)
        if st.button("⚠️ 清空全部订单数据", type="secondary", disabled=not confirm_clear):
            repo.clear_all()
            st.success("已清空所有订单数据")
            st.rerun()
