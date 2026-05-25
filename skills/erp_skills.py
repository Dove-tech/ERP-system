from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ERPSkill:
    skill_id: str
    name: str
    description: str
    keywords: List[str]
    required_slots: List[str]
    risk_level: str
    tool_intents: List[str]
    guardrails: List[str] = field(default_factory=list)


class ERPSkillRegistry:
    """Static ERP Skill registry used to bound planning and hallucination risk."""

    def __init__(self):
        self.skills = [
            ERPSkill(
                skill_id="customer_order_entry",
                name="客户订单录入 Skill",
                description="抽取客户订单字段，校验规格、数量和交期，确认后创建订单。",
                keywords=["订单", "客户订单", "创建订单", "录入", "下单"],
                required_slots=["产品/物料", "数量", "交期或配送区域"],
                risk_level="write",
                tool_intents=["create_order", "query_product", "query_supplier"],
                guardrails=["required_slots", "hitl", "quantity_range"],
            ),
            ERPSkill(
                skill_id="inventory_lookup",
                name="物料库存查询 Skill",
                description="查询物料、成品、替代料和库存风险。",
                keywords=["库存", "物料", "产品", "替代", "缺料"],
                required_slots=["产品/物料"],
                risk_level="read",
                tool_intents=["query_product", "query_inventory", "query_substitute"],
                guardrails=["evidence_answer"],
            ),
            ERPSkill(
                skill_id="supplier_selection",
                name="供应商选择 Skill",
                description="根据区域、供应能力、状态和历史偏好推荐供应商。",
                keywords=["供应商", "采购", "配送", "供货", "物流"],
                required_slots=["区域或物料"],
                risk_level="read",
                tool_intents=["query_supplier"],
                guardrails=["supplier_status", "evidence_answer"],
            ),
            ERPSkill(
                skill_id="production_plan_adjustment",
                name="生产计划调整 Skill",
                description="结合订单、库存、供应商和生产线状态生成计划调整方案。",
                keywords=["生产计划", "排产", "生产线", "设备", "加急", "计划调整"],
                required_slots=["订单/产品", "数量", "交期"],
                risk_level="high_risk_process",
                tool_intents=["query_order", "query_inventory", "update_plan"],
                guardrails=["hitl", "capacity_check", "workflow_check"],
            ),
            ERPSkill(
                skill_id="production_progress_update",
                name="生产进度更新 Skill",
                description="确认生产订单和目标状态后更新生产进度。",
                keywords=["进度", "状态", "生产中", "暂停", "完成", "更新"],
                required_slots=["订单", "目标状态"],
                risk_level="write",
                tool_intents=["update_status", "query_order"],
                guardrails=["state_transition", "hitl"],
            ),
            ERPSkill(
                skill_id="ambiguity_resolution",
                name="模糊需求澄清 Skill",
                description="处理老样子、照上次、按原计划等模糊请求。",
                keywords=["老样子", "上次", "之前", "照旧", "按原计划", "加急处理"],
                required_slots=["候选记忆或用户补充"],
                risk_level="clarify",
                tool_intents=[],
                guardrails=["memory_scope", "hitl"],
            ),
        ]

    def match(self, query: str) -> Dict[str, object]:
        text = query or ""
        best_skill = None
        best_score = 0
        for skill in self.skills:
            score = sum(1 for keyword in skill.keywords if keyword in text)
            if score > best_score:
                best_score = score
                best_skill = skill
        if best_skill is None:
            return {
                "skill_id": "general_erp_copilot",
                "name": "通用 ERP Copilot Skill",
                "confidence": 0.3,
                "risk_level": "unknown",
                "required_slots": [],
                "guardrails": ["tool_grounding", "clarify_if_unsupported"],
            }
        confidence = min(0.95, 0.45 + best_score * 0.2)
        return {
            "skill_id": best_skill.skill_id,
            "name": best_skill.name,
            "description": best_skill.description,
            "confidence": confidence,
            "risk_level": best_skill.risk_level,
            "required_slots": best_skill.required_slots,
            "tool_intents": best_skill.tool_intents,
            "guardrails": best_skill.guardrails,
        }
