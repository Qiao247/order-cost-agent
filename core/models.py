from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime
from enum import Enum


class OrderStatus(str, Enum):
    NOT_STARTED = "未开工"
    IN_PROGRESS = "生产中"
    COMPLETED = "已完工"
    DELIVERED = "已交付"


class AccountingStatus(str, Enum):
    PENDING_VALIDATION = "待校验"
    PENDING_REVIEW = "待人工复核"
    MANAGEMENT_ESTIMATE = "管理暂算"
    ACCOUNTING_APPROVED = "会计已审批"
    FORMAL_OUTPUT = "正式输出"


class WarningLevel(str, Enum):
    NORMAL = "正常"
    ATTENTION = "关注"
    WARNING = "预警"
    CRITICAL = "严重"


class DocumentType(str, Enum):
    PURCHASE_ORDER = "采购订单"
    MATERIAL_REQUISITION = "领料单"
    LABOR_TIME_CARD = "工时卡"
    MANUFACTURING_EXPENSE = "制造费用单"
    SALES_ORDER = "销售订单"
    PRODUCT_STORAGE = "产品入库单"


@dataclass
class MaterialItem:
    material_code: str
    material_name: str
    spec: str
    unit: str
    qty: float
    unit_price: float
    amount: float = 0.0

    def __post_init__(self):
        if self.amount == 0:
            self.amount = round(self.qty * self.unit_price, 2)


@dataclass
class LaborItem:
    process_name: str
    work_type: str
    hours: float
    hourly_rate: float
    amount: float = 0.0

    def __post_init__(self):
        if self.amount == 0:
            self.amount = round(self.hours * self.hourly_rate, 2)


@dataclass
class ExpenseItem:
    expense_type: str
    expense_name: str
    allocation_base: str
    amount: float
    allocated_amount: float = 0.0


@dataclass
class StandardCost:
    materials: List[MaterialItem] = field(default_factory=list)
    labors: List[LaborItem] = field(default_factory=list)
    expenses: List[ExpenseItem] = field(default_factory=list)

    @property
    def total_material_cost(self) -> float:
        return round(sum(m.amount for m in self.materials), 2)

    @property
    def total_labor_cost(self) -> float:
        return round(sum(l.amount for l in self.labors), 2)

    @property
    def total_expense_cost(self) -> float:
        return round(sum(e.amount for e in self.expenses), 2)

    @property
    def total_cost(self) -> float:
        return round(self.total_material_cost + self.total_labor_cost + self.total_expense_cost, 2)


@dataclass
class ActualCost(StandardCost):
    pass


class WasteType(str, Enum):
    """成本浪费根源二级分类：工艺性浪费（生产作业过程）/ 管理性浪费（管理控制缺陷）。"""
    PROCESS = "工艺性浪费"
    MANAGEMENT = "管理性浪费"


@dataclass
class CostVariance:
    category: str
    item_name: str
    standard_amount: float
    actual_amount: float
    variance_amount: float
    variance_rate: float
    cause_analysis: str = ""
    suggestion: str = ""
    components: Dict[str, float] = field(default_factory=dict)
    waste_type: str = ""  # "工艺性浪费" / "管理性浪费" / ""（节约项或合计行不标注）


@dataclass
class ProductionOrder:
    order_no: str
    product_name: str
    product_code: str
    spec: str
    order_qty: float
    unit: str
    sales_price: float
    customer_name: str
    start_date: str
    plan_delivery_date: str
    actual_delivery_date: Optional[str] = None
    status: OrderStatus = OrderStatus.NOT_STARTED
    completion_rate: float = 0.0
    accounting_status: AccountingStatus = AccountingStatus.PENDING_VALIDATION
    source_mode: str = "示例/仿真"
    standard_cost: StandardCost = field(default_factory=StandardCost)
    actual_cost: ActualCost = field(default_factory=ActualCost)
    variances: List[CostVariance] = field(default_factory=list)
    warnings: List[Dict] = field(default_factory=list)
    documents: List[Dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    @property
    def sales_amount(self) -> float:
        return round(self.order_qty * self.sales_price, 2)

    @property
    def standard_profit(self) -> float:
        return round(self.sales_amount - self.standard_cost.total_cost, 2)

    @property
    def is_accounting_approved(self) -> bool:
        return self.accounting_status in (
            AccountingStatus.ACCOUNTING_APPROVED,
            AccountingStatus.FORMAL_OUTPUT,
        )

    @property
    def is_actual_profit_available(self) -> bool:
        return (
            self.status in (OrderStatus.COMPLETED, OrderStatus.DELIVERED)
            and self.completion_rate >= 1.0
            and self.is_accounting_approved
        )

    @property
    def actual_profit(self) -> Optional[float]:
        """仅返回已完工且经会计审批的正式实际利润。"""
        if not self.is_actual_profit_available:
            return None
        return round(self.sales_amount - self.actual_cost.total_cost, 2)

    @property
    def profit_variance(self) -> Optional[float]:
        if self.actual_profit is None:
            return None
        return round(self.actual_profit - self.standard_profit, 2)
