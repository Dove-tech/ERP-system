from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, Iterable) and not isinstance(value, (dict, bytes)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


@dataclass
class OperatorContext:
    user_id: str = "anonymous"
    tenant_id: str = "internal"
    roles: List[str] = field(default_factory=list)
    endpoint_permissions: List[str] = field(default_factory=list)
    tool_permissions: List[str] = field(default_factory=list)
    allowed_regions: List[str] = field(default_factory=list)
    customer_scope: List[str] = field(default_factory=list)
    data_scope: Dict[str, Any] = field(default_factory=dict)
    request_time: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def effective_permissions(self) -> set:
        return set(self.endpoint_permissions) | set(self.tool_permissions)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "roles": list(self.roles),
            "endpoint_permissions": list(self.endpoint_permissions),
            "tool_permissions": list(self.tool_permissions),
            "allowed_regions": list(self.allowed_regions),
            "customer_scope": list(self.customer_scope),
            "data_scope": dict(self.data_scope or {}),
            "request_time": self.request_time,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any] = None) -> "OperatorContext":
        data = data or {}
        return cls(
            user_id=str(data.get("user_id") or "anonymous"),
            tenant_id=str(data.get("tenant_id") or "internal"),
            roles=_as_list(data.get("roles")),
            endpoint_permissions=_as_list(data.get("endpoint_permissions") or data.get("user_authority")),
            tool_permissions=_as_list(data.get("tool_permissions") or data.get("permissions")),
            allowed_regions=_as_list(data.get("allowed_regions")),
            customer_scope=_as_list(data.get("customer_scope")),
            data_scope=dict(data.get("data_scope") or {}),
            request_time=str(data.get("request_time") or datetime.now(timezone.utc).isoformat()),
        )


def build_operator_context(current_user: Dict[str, Any] = None,
                           request_data: Dict[str, Any] = None) -> OperatorContext:
    current_user = current_user or {}
    request_data = request_data or {}
    data_scope = dict(current_user.get("data_scope") or {})
    allowed_regions = _as_list(current_user.get("allowed_regions"))
    if not allowed_regions:
        allowed_regions = _as_list(data_scope.get("allowed_regions") or data_scope.get("regions"))
    customer_scope = _as_list(current_user.get("customer_scope"))
    if not customer_scope:
        customer_scope = _as_list(data_scope.get("customer_scope") or data_scope.get("customer_levels"))

    return OperatorContext(
        user_id=str(current_user.get("user_id") or request_data.get("userId") or request_data.get("user_id") or "anonymous"),
        tenant_id=str(current_user.get("tenant_id") or request_data.get("tenantId") or request_data.get("tenant_id") or "internal"),
        roles=_as_list(current_user.get("roles")),
        endpoint_permissions=_as_list(current_user.get("user_authority") or current_user.get("endpoint_permissions")),
        tool_permissions=_as_list(current_user.get("tool_permissions") or current_user.get("permissions")),
        allowed_regions=allowed_regions,
        customer_scope=customer_scope,
        data_scope=data_scope,
        request_time=datetime.now(timezone.utc).isoformat(),
    )


class ToolPermissionGuard:
    REGION_PARAM_KEYS = {
        "region", "region_name", "regionName", "area", "area_name", "sales_region",
        "district", "区域", "地区",
    }
    TENANT_PARAM_KEYS = {"tenant_id", "tenantId"}
    CUSTOMER_LEVEL_PARAM_KEYS = {
        "customer_level", "customerLevel", "customer_type", "customerType",
        "客户等级", "客户类型",
    }

    def validate_tool_access(self, tool: Any, context: OperatorContext) -> Dict[str, Any]:
        context = self._ensure_context(context)
        required_permissions = _as_list(getattr(tool, "required_permissions", None))
        if not required_permissions:
            return self._result(True, "tool_permission_passed", "tool has no required permissions", {
                "required_permissions": [],
                "missing_permissions": [],
            })

        missing_permissions = [
            permission for permission in required_permissions
            if permission not in context.effective_permissions
        ]
        if missing_permissions:
            return self._result(False, "tool_permission_denied", "missing required tool permissions", {
                "required_permissions": required_permissions,
                "missing_permissions": missing_permissions,
            })

        return self._result(True, "tool_permission_passed", "required tool permissions satisfied", {
            "required_permissions": required_permissions,
            "missing_permissions": [],
        })

    def filter_allowed_tools(self, tools: List[Any],
                             context: OperatorContext) -> Tuple[List[Any], List[Dict[str, Any]]]:
        allowed_tools = []
        denied_tools = []
        for tool in tools or []:
            result = self.validate_tool_access(tool, context)
            if result["allow"]:
                allowed_tools.append(tool)
            else:
                denied_tools.append({
                    "tool_id": getattr(tool, "tool_id", None),
                    "operation_id": getattr(tool, "operationId", ""),
                    "tool_name": getattr(tool, "name_for_human", ""),
                    "reason": result["reason"],
                    "missing_permissions": result["details"].get("missing_permissions", []),
                })
        return allowed_tools, denied_tools

    def validate_parameter_scope(self, params: Dict[str, Any],
                                 context: OperatorContext) -> Dict[str, Any]:
        context = self._ensure_context(context)
        params = params or {}
        violations = []

        region_values = self._collect_param_values(params, self.REGION_PARAM_KEYS)
        if context.allowed_regions and region_values:
            denied_regions = [
                region for region in region_values
                if region not in set(context.allowed_regions)
            ]
            if denied_regions:
                violations.append({
                    "type": "region_scope_denied",
                    "values": denied_regions,
                    "allowed_values": context.allowed_regions,
                })

        tenant_values = self._collect_param_values(params, self.TENANT_PARAM_KEYS)
        denied_tenants = [
            tenant for tenant in tenant_values
            if tenant and tenant != context.tenant_id
        ]
        if denied_tenants:
            violations.append({
                "type": "tenant_scope_denied",
                "values": denied_tenants,
                "allowed_values": [context.tenant_id],
            })

        customer_levels = self._collect_param_values(params, self.CUSTOMER_LEVEL_PARAM_KEYS)
        if context.customer_scope and customer_levels:
            denied_levels = [
                level for level in customer_levels
                if level not in set(context.customer_scope)
            ]
            if denied_levels:
                violations.append({
                    "type": "customer_scope_denied",
                    "values": denied_levels,
                    "allowed_values": context.customer_scope,
                })

        if violations:
            return self._result(False, "parameter_scope_denied", "parameters exceed operator scope", {
                "violations": violations,
            })
        return self._result(True, "parameter_scope_passed", "parameters are within operator scope", {
            "violations": [],
        })

    def validate_tool_call(self, tool: Any, params: Dict[str, Any],
                           context: OperatorContext) -> Dict[str, Any]:
        tool_result = self.validate_tool_access(tool, context)
        if not tool_result["allow"]:
            return tool_result
        param_result = self.validate_parameter_scope(params, context)
        if not param_result["allow"]:
            return param_result
        return self._result(True, "permission_passed", "tool and parameters are allowed", {
            "tool_permission": tool_result,
            "parameter_scope": param_result,
        })

    def _collect_param_values(self, params: Dict[str, Any], keys: set) -> List[str]:
        values = []
        for key, value in params.items():
            if key in keys:
                values.extend(_as_list(value))
        return values

    def _ensure_context(self, context: OperatorContext) -> OperatorContext:
        if isinstance(context, OperatorContext):
            return context
        if isinstance(context, dict):
            return OperatorContext.from_dict(context)
        return OperatorContext()

    def _result(self, allow: bool, action: str, reason: str,
                details: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "allow": allow,
            "action": action,
            "reason": reason,
            "details": details or {},
        }
