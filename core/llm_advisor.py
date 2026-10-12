# -*- coding: utf-8 -*-
"""DeepSeek 智能诊断建议模块。

设计边界（与计划书承诺严格一致）：
- 所有金额、差异率等财务数字均由本地确定性规则引擎核算；
- 大模型（DeepSeek）只负责把结构化诊断结果转译为自然语言的管理建议，
  禁止模型自行计算或修改任何数字；
- 密钥分层读取：st.secrets（云端 Secrets 面板）-> 环境变量 -> 本地
  .streamlit/secrets.toml（不入 git 仓库、不进部署包）；
- 无密钥 / 断网 / 调用失败 / 超时，一律自动降级为本地规则模板建议，
  页面永不白屏——保证"只有浏览器的电脑"正常使用。
"""
import json
import os

import streamlit as st


def get_api_key():
    """分层读取 DeepSeek API 密钥。"""
    try:
        key = st.secrets.get("DEEPSEEK_API_KEY", "")
        if key:
            return key
    except Exception:
        pass  # 本地无 secrets 文件时 st.secrets 访问会抛异常，属正常
    return os.environ.get("DEEPSEEK_API_KEY", "") or None


def llm_available() -> bool:
    return bool(get_api_key())


# ---------------------------------------------------------------- 提示词
SYSTEM_PROMPT = (
    "你是一名资深成本会计师，服务于中小劳保用品（安全帽/防护服等PPE）制造企业，"
    "正在为经营层撰写订单成本诊断意见。要求：\n"
    "1. 输入 JSON 中所有金额与百分比必须原样引用，严禁自行计算、推算或修改任何数字；\n"
    "2. 输出为 Markdown，仅含三个小节：`### 超支原因研判`（3-4条，需结合"
    "工艺性浪费/管理性浪费归因展开）、`### 管控建议`（3条可执行措施，每条标注"
    "责任部门：生产车间/采购部/人事部/财务部）、`### 风险提示`（1-2条）；\n"
    "3. 若订单整体正常无超支，则给出维持现状的经验建议，不要虚构问题；\n"
    "4. 语言精炼专业，全文不超过350字。"
)


def build_payload(order, waste_sum, unit_ana, driver_rows) -> dict:
    """把规则引擎的诊断结果压成紧凑的结构化载荷（不含任何模型计算）。"""
    top_variances = []
    for v in sorted(order.variances, key=lambda x: abs(x.variance_rate), reverse=True):
        if len(top_variances) >= 6:
            break
        if abs(v.variance_rate) < 3:
            continue
        top_variances.append({
            "项目": v.item_name,
            "类别": v.category,
            "差异率%": round(v.variance_rate, 1),
            "差异额": round(v.variance_amount, 2),
            "浪费类型": v.waste_type or "",
            "规则原因": v.cause_analysis or "",
        })
    warn_count = {"严重": 0, "预警": 0, "关注": 0}
    for w in order.warnings:
        lv = w.get("level", "")
        if lv in warn_count:
            warn_count[lv] += 1
    payload = {
        "订单号": order.order_no,
        "产品": f"{order.product_name}({order.spec})",
        "客户": order.customer_name,
        "状态": order.status.value,
        "完工率%": round(order.completion_rate * 100, 1),
        "整单标准成本": round(order.standard_cost.total_cost, 2),
        "累计实际成本": round(order.actual_cost.total_cost, 2),
        "当前成本差异": round(order.actual_cost.total_cost - order.standard_cost.total_cost, 2),
        "标准毛利率%": round(unit_ana.get("gross_margin_rate_standard", 0), 1),
        "预计毛利率%": round(unit_ana.get("expected_margin_rate", 0), 1),
        "成本三要素": {
            "直接材料": {"标准": round(order.standard_cost.total_material_cost, 2),
                        "实际": round(order.actual_cost.total_material_cost, 2)},
            "直接人工": {"标准": round(order.standard_cost.total_labor_cost, 2),
                        "实际": round(order.actual_cost.total_labor_cost, 2)},
            "制造费用": {"标准": round(order.standard_cost.total_expense_cost, 2),
                        "实际": round(order.actual_cost.total_expense_cost, 2)},
        },
        "异常明细Top": top_variances,
        "浪费归因": {k: round(v, 2) for k, v in waste_sum.items()},
        "预警分布": warn_count,
    }
    if driver_rows:
        payload["最大成本动因"] = driver_rows[0].get("成本项目", "")
    return payload


def _fallback_advice(payload: dict) -> str:
    """本地规则模板建议（断网/无密钥时的降级文案，全部引用规则引擎数字）。"""
    total_var = payload["当前成本差异"]
    waste = payload["浪费归因"]
    craft, mgmt = waste.get("工艺性浪费", 0), waste.get("管理性浪费", 0)
    lines = ["### 超支原因研判"]
    if total_var <= 0:
        lines.append(f"- 订单累计成本低于标准（差异 {payload['当前成本差异']} 元），"
                     f"预计毛利率 {payload['预计毛利率%']}%，成本控制处于健康区间。")
    else:
        lines.append(f"- 订单累计超支 {payload['当前成本差异']} 元；"
                     f"工艺性浪费 {craft} 元、管理性浪费 {mgmt} 元，"
                     f"主要超支来自{'生产作业过程' if craft >= mgmt else '管理控制环节'}。")
        for item in payload["异常明细Top"][:3]:
            lines.append(f"- 「{item['项目']}」差异率 {item['差异率%']}%"
                         + (f"（{item['规则原因']}）" if item["规则原因"] else ""))
    lines.append("### 管控建议")
    if craft >= mgmt:
        lines.append("- 生产车间：核查用量超标工序的损耗率与返工率，收紧领料定额。")
    else:
        lines.append("- 采购部：复核超支物料采购价格，比价寻源锁价。")
    lines.append("- 财务部：按周跟踪本订单完工成本与预计毛利率走势。")
    lines.append("- 人事部：核对工时卡与排班，控制工资率波动。")
    lines.append("### 风险提示")
    n_warn = payload["预警分布"]
    if n_warn["严重"] or n_warn["预警"]:
        lines.append(f"- 当前存在 {n_warn['严重']} 条严重、{n_warn['预警']} 条预警级提醒，需按预警清单逐项处置。")
    else:
        lines.append("- 暂无严重预警，继续按现行节奏监控即可。")
    return "\n".join(lines)


@st.cache_data(ttl=1800, show_spinner=False)
def _call_deepseek(payload_json: str) -> str:
    """实际调用 DeepSeek（结果按载荷指纹缓存 30 分钟，节约额度）。"""
    from openai import OpenAI  # 延迟导入：未安装时也能走降级路径
    client = OpenAI(
        api_key=get_api_key(),
        base_url="https://api.deepseek.com",
        timeout=25,
        max_retries=1,
    )
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": payload_json},
        ],
        max_tokens=800,
        temperature=0.3,
    )
    return resp.choices[0].message.content.strip()


def generate_advice(order, waste_sum, unit_ana, driver_rows):
    """返回 (markdown文本, 来源标签)。来源: 'deepseek' / 'fallback'。"""
    payload = build_payload(order, waste_sum, unit_ana, driver_rows)
    payload_json = json.dumps(payload, ensure_ascii=False)
    if not llm_available():
        return _fallback_advice(payload), "fallback"
    try:
        text = _call_deepseek(payload_json)
        return (text, "deepseek") if text else (_fallback_advice(payload), "fallback")
    except Exception:
        return _fallback_advice(payload), "fallback"
