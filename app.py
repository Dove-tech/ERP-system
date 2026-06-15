# coding = utf-8
import json
import re
import time
from flask_cors import CORS
from flask import Flask, jsonify, request, g
from flasgger import Swagger
from apis.api_planning_hub import ApiPlanningHub
from entity import Parameter, Tool, Task
from memory import ContextManager, MemoryManager
from tasks import GenerateTaskHub
from models import LargeLanguageModel
from prompt.prompt_engineering import PromptRegistry, StructuredOutputParser
from permissions import build_operator_context
from tools.tool_manager import ToolManager
from tasks import TaskManager
from trace import TraceManager
from use_manager.user_manager import UserManagerHub
from utils import RESPONSE_AUTH_CODE_ERROR, RESPONSE_ALLOW_CODE_ERROR, RESPONSE_STATUS_CODE_SUCCESS, DEFAULT_PERMISSIONS
from utils import TASK_STATUS_FINISH, TASK_STATUS_RUNNING, TASK_STATUS_WAIT_CONFIRM, TASK_TYPE_UNKNOWN, \
    TASK_SYS_OUTPUT_STOP
from utils.logger_config import setup_logger,logger
import traceback
import os
from concurrent.futures import ThreadPoolExecutor
import threading
from utils.config import (
    milvus_uri,
    model_path,
    milvus_db_name,
    model_name,
    model_temperature,
    model_top_p,
    mongo_host,
    mongo_db,
    mongo_port,
    topK,
    model_api_key,
    model_base_url, SECRET_KEY, JWT_ALGORITHM,
)
import jwt
from datetime import datetime, timedelta
import uuid
from cachetools import TTLCache
from functools import wraps

# JWT配置
JWT_EXPIRATION_DELTA = timedelta(hours=1)

# 创建TTL缓存，存储用户会话信息，最大1000个，有效期1小时
session_cache = TTLCache(maxsize=1000, ttl=3600)

app = Flask(__name__)

# 获取CPU核心数并计算线程池大小
cpu_count = os.cpu_count()
if cpu_count is None:
    # 无法获取CPU数量时的默认值
    max_workers = 8
else:
    max_workers = cpu_count * 2
executor = ThreadPoolExecutor(max_workers)
tasks = {}  # 用于存储任务状态

# app.logger = setup_logger('copilot')

CORS(app, resources={r"/login_user": {"origins": "http://localhost:3000"},
                        r"/register_user": {"origins": "http://localhost:3000"},
                        r"/get_all_tools": {"origins": "http://localhost:3000"},
                        r"/delete_all_tool": {"origins": "http://localhost:3000"},
                        r"/upload_tool": {"origins": "http://localhost:3000"},
                        r"/delete_tool_by_ids": {"origins": "http://localhost:3000"},
                        r"/test_llm": {"origins": "http://localhost:3000"},
                        r"/api_task_status": {"origins": "http://localhost:3000"},
                        r"/api_trace_status": {"origins": "http://localhost:3000"},
                        r"/api_planning": {"origins": "http://localhost:3000"}})
Swagger(app)

# apiPlanningHub = ApiPlanningHub(uri, model_path, db_name, model, temperature, top_p, host, db, port, topK, base_url,
#                                 api_key)
toolManager = ToolManager(mongo_host, mongo_db, mongo_port, milvus_uri, milvus_db_name)
taskManager = TaskManager(mongo_host, mongo_db, mongo_port)
memoryManager = MemoryManager(mongo_host, mongo_db, mongo_port)
contextManager = ContextManager(memoryManager)
traceManager = TraceManager(mongo_host, mongo_db, mongo_port)
promptRegistry = PromptRegistry()

userManagerHub = UserManagerHub(mongo_host, mongo_db, mongo_port)


def _current_user_context(data):
    current_user = getattr(g, "current_user", {}) or {}
    user_id = str(current_user.get("user_id") or data.get("userId") or data.get("user_id") or "anonymous")
    session_id = str(
        data.get("sessionId")
        or data.get("session_id")
        or data.get("conversationId")
        or data.get("conversation_id")
        or str(uuid.uuid4())
    )
    tenant_id = str(data.get("tenantId") or data.get("tenant_id") or "internal")
    return user_id, session_id, tenant_id


def _current_operator_context(data):
    current_user = getattr(g, "current_user", {}) or {}
    return build_operator_context(current_user, data).to_dict()


def _is_confirm_feedback(text):
    feedback = (text or "").lower()
    confirm_keywords = ["确认", "执行", "继续", "可以", "同意", "ok", "yes", "confirm"]
    abort_keywords = ["取消", "停止", "放弃", "不要", "不执行", "abort", "cancel", "no"]
    if any(keyword in feedback for keyword in abort_keywords):
        return False
    return any(keyword in feedback for keyword in confirm_keywords)


def _is_abort_feedback(text):
    feedback = (text or "").lower()
    abort_keywords = ["取消", "停止", "放弃", "不要", "不执行", "abort", "cancel", "no"]
    return any(keyword in feedback for keyword in abort_keywords)


_DIRECT_CONFIRM_FEEDBACK = {
    "确认", "确认执行", "执行", "立即执行", "执行任务", "继续", "继续执行",
    "可以", "同意", "没问题", "是的", "按这个继续", "按候选方案继续",
    "ok", "yes", "confirm",
}
_DIRECT_ABORT_FEEDBACK = {
    "取消", "取消执行", "停止", "停止执行", "放弃", "放弃执行", "不执行",
    "不要执行", "不用执行", "别执行", "算了", "先不执行", "暂停",
    "abort", "cancel", "no",
}


def _normalize_control_feedback(text):
    return re.sub(r"[\s,，。.!！?？;；:：\"'“”‘’]+", "", (text or "").lower())


def _direct_control_intent(text):
    """
    Only handles short, standalone control replies.
    Mixed replies such as "可以，但供应商改成 S-200" must go through the
    scenario-specific LLM parser so added facts are not discarded.
    """
    normalized = _normalize_control_feedback(text)
    if not normalized:
        return ""
    if normalized in _DIRECT_ABORT_FEEDBACK:
        return "abort"
    if normalized in _DIRECT_CONFIRM_FEEDBACK:
        return "confirm"
    if len(normalized) <= 8:
        if normalized.startswith(("取消", "停止", "放弃", "不执行", "不要执行", "不用执行", "别执行")):
            return "abort"
        if normalized.startswith(("确认", "执行", "继续", "同意")):
            return "confirm"
    return ""


def _persist_assistant_memory(task, query, system_output):
    if task is None or not getattr(task, "user_id", "") or not getattr(task, "session_id", ""):
        return
    memoryManager.add_message(task.user_id, task.session_id, "assistant", system_output or "",
                              task_id=task.task_id, message_type="system_output")
    summarizer = _build_conversation_summary_summarizer()
    summary_state = contextManager.update_after_turn(task.user_id, task.session_id, task.raw_query,
                                                     system_output or "",
                                                     summarizer=summarizer)
    if getattr(task, "trace_id", "") and summary_state:
        traceManager.add_event(task.trace_id, "summary_compacted", {
            "changed": summary_state.get("changed", False),
            "compacted_message_count": summary_state.get("compacted_message_count", 0),
            "recent_window": summary_state.get("recent_window", 0),
            "total_messages": summary_state.get("total_messages", 0),
            "fallback_used": summary_state.get("fallback_used", False),
            "compacted_message_delta": summary_state.get("compacted_message_delta", 0),
        })


def _build_conversation_summary_summarizer():
    def summarize(old_summary, messages, max_summary_chars):
        compact_messages = json.dumps(messages or [], ensure_ascii=False, indent=2)
        prompt = promptRegistry.render("conversation_summary_compaction", model_name, {
            "old_summary": old_summary or "",
            "messages": compact_messages,
            "max_summary_chars": max_summary_chars,
        })
        llm = LargeLanguageModel(model_base_url, model_api_key)
        output = llm.chat_completions(prompt, model_name, model_temperature, model_top_p)
        return (output or "").strip()

    return summarize


def _run_planning_for_task(task, query, data, curr_model_name, curr_temperature,
                           curr_api_url, curr_api_key):
    api_planning_hub = ApiPlanningHub(milvus_uri, model_path, milvus_db_name, curr_model_name,
                                    curr_temperature, model_top_p, mongo_host, mongo_db, mongo_port, topK,
                                    curr_api_url, curr_api_key, executor)
    if not task.trace_id:
        trace_id = traceManager.start_trace(task.task_id, task.user_id, task.session_id, query)
        taskManager.update_task_recorder(task.task_id, TASK_STATUS_RUNNING, task.system_output or "",
                                         trace_id=trace_id)
    api_planning_hub.apis_planning(query, task.task_id)


def _get_tool_by_id(tool_id):
    tools = toolManager.get_tools_by_ids([tool_id])
    return tools[0] if tools else None


def _parameter_to_dict(parameter):
    return {
        "name": parameter.name,
        "description": parameter.description,
        "type": parameter.type,
        "format": parameter.format,
        "required": parameter.required,
        "enum": list(parameter.enum or []),
    }


def _validate_tool_params(tool, params):
    params = dict(params or {})
    missing = []
    for parameter in tool.request_body:
        value = params.get(parameter.name)
        if parameter.required and value in ("", None, [], {}):
            missing.append(_parameter_to_dict(parameter))
            continue
        if value in ("", None, [], {}):
            continue
        if parameter.type in ("int64", "int32") and not isinstance(value, int):
            try:
                params[parameter.name] = int(value)
            except Exception:
                params[parameter.name] = ""
                missing.append(_parameter_to_dict(parameter))
        elif parameter.type == "double" and not isinstance(value, float):
            try:
                params[parameter.name] = float(value)
            except Exception:
                params[parameter.name] = ""
                missing.append(_parameter_to_dict(parameter))
        elif parameter.enum and str(value) not in parameter.enum:
            missing.append(_parameter_to_dict(parameter))
    return params, missing


def _render_missing_params_message(missing_params):
    missing_names = "、".join(item.get("description") or item.get("name") for item in missing_params)
    return f"还需要补充以下信息才能继续：{missing_names}。请直接提供这些信息，或取消本次任务。"


def _request_tool_execution_confirmation(task, tool, params, task_desc, raw_query):
    system_output = f'''根据您的查询要求{raw_query}，我发现目前需要使用工具{tool.name_for_human}，
相关参数是[{params}]。
请确认该工具是否正确且立即使用。如果您想停止本次任务执行也请告诉我。

如果该工具正确且立即使用，建议回答“立即执行”。
如果您想停止本次任务执行，建议回答“不执行”。
'''
    taskManager.update_task_recorder(
        task.task_id,
        TASK_STATUS_WAIT_CONFIRM,
        system_output,
        graph_title="请确认",
        curr_task_desc=task_desc,
        curr_tool_id=tool.tool_id,
        curr_tool_param=params,
        pending_action="tool_execution_confirm",
        pending_payload={
            "tool_id": tool.tool_id,
            "tool_name": tool.name_for_human,
            "operation_id": tool.operationId,
            "params": params,
            "task_desc": task_desc,
            "raw_query": raw_query,
        },
    )

# 权限验证装饰器
def require_permission(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 检查是否是免验证的端点
        if f.__name__ in ['register', 'login']:
            return f(*args, **kwargs)

        # 检查Authorization头
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({
                "status": RESPONSE_AUTH_CODE_ERROR,
                "message": "未提供认证令牌",
                "data": None
            }), RESPONSE_AUTH_CODE_ERROR

        try:
            # 验证Bearer令牌格式
            if not auth_header.startswith('Bearer '):
                return jsonify({
                    "status": RESPONSE_AUTH_CODE_ERROR,
                    "message": "无效的令牌格式",
                    "data": None
                }), RESPONSE_AUTH_CODE_ERROR

            # 提取令牌
            token = auth_header.split(' ')[1]

            # 验证令牌是否在缓存中
            if token not in session_cache:
                return jsonify({
                    "status": RESPONSE_AUTH_CODE_ERROR,
                    "message": "令牌无效或已过期",
                    "data": None
                }), RESPONSE_AUTH_CODE_ERROR

            # 解码JWT令牌（验证签名和有效期）
            try:
                payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
            except jwt.ExpiredSignatureError:
                # 令牌已过期，从缓存中移除
                if token in session_cache:
                    del session_cache[token]
                return jsonify({
                    "status": RESPONSE_AUTH_CODE_ERROR,
                    "message": "令牌已过期",
                    "data": None
                }), RESPONSE_AUTH_CODE_ERROR
            except jwt.InvalidTokenError:
                return jsonify({
                    "status": RESPONSE_AUTH_CODE_ERROR,
                    "message": "无效的令牌",
                    "data": None
                }), RESPONSE_AUTH_CODE_ERROR

            # 将用户信息存储到g对象，供后续处理使用
            g.current_user = session_cache[token]

            # 检查用户是否有权限访问当前接口
            if f.__name__ not in g.current_user['user_authority']:
                return jsonify({
                    "status": RESPONSE_ALLOW_CODE_ERROR,
                    "message": f"没有权限访问 {f.__name__} 接口",
                    "data": None
                }), RESPONSE_ALLOW_CODE_ERROR

        except Exception as e:
            logger.error(f"认证失败: {str(e)}")
            return jsonify({
                "status": RESPONSE_AUTH_CODE_ERROR,
                "message": "认证失败",
                "data": None
            }), RESPONSE_AUTH_CODE_ERROR

        return f(*args, **kwargs)
    return decorated_function

@app.route('/delete_all_tool', methods=['GET'])
@require_permission
def delete_tool_db():
    """
            工具数据库删除
            ---
            tags:
              - Tool Delete
            description:
                上传文件到服务器
            responses:
              200:
                description: 数据库清空成功
              400:
                description: 数据库未清空成功
        """

    toolManager.delete_all_tools()
    return jsonify({'message': 'delete tool success! '}), RESPONSE_STATUS_CODE_SUCCESS


@app.route('/insert_tool', methods=['POST'])
@require_permission
def insert_tool():
    """
    工具插入
    ---
    tags:
      - Tool Management
    description:
        插入一个新的工具到数据库中
    parameters:
      - name: body
        in: body
        required: true
        schema:
          id: Tool Insert Request
          required:
            - operationId
            - name_for_human
            - name_for_model
            - description
            - url
            - path
            - method
            - params
          properties:
            operationId:
              type: string
              description: 操作ID
            name_for_human:
              type: string
              description: 人类可读的工具名称
            name_for_model:
              type: string
              description: 模型使用的工具名称
            description:
              type: string
              description: 工具的描述
            url:
              type: string
              description: API 的 URL
            path:
              type: string
              description: API 的路径
            method:
              type: string
              description: HTTP 方法 (如 GET, POST 等)
            params:
              type: array
              items:
                type: object
                properties:
                  param_name:
                    type: string
                    description: 参数名称
                  paramType:
                    type: string
                    description: 参数类型
                  param_description:
                    type: string
                    description: 参数描述
                  enum:
                    type: array
                    items:
                      type: string
                    description: 参数的枚举值
                  in_:
                    type: string
                    description: 参数的位置 (如 query, body 等)
    responses:
      200:
        description: 工具插入成功
        schema:
          type: object
          properties:
            message:
              type: string
      400:
        description: 请求参数缺失或无效
        schema:
          type: object
          properties:
            error:
              type: string
    """

    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing query parameter'}), 400

    params = []
    for tmp in data["params"]:
        parameter = Parameter(
            name=tmp["param_name"],
            type=tmp["paramType"],
            description=tmp["param_description"],
            enum=tmp["enum"],
            required=True,
            in_=tmp["in_"]
        )
        params.append(parameter)

    tool = Tool(
        tool_id=0,
        operationId=data["operationId"],
        name_for_human=data["name_for_human"],
        name_for_model=data["name_for_model"],
        description=data["description"],
        api_url=data["url"],
        path=data["path"],
        method=data["method"],
        request_body=params
    )
    toolManager.insert_tools([tool])
    return jsonify({'message': 'delete tool success! '}), RESPONSE_STATUS_CODE_SUCCESS


@app.route('/register_user', methods=['POST'])
def register():
    """
    用户注册
    ---
    tags:
      - User Management
    description:
        注册新用户
    parameters:
      - name: body
        in: body
        required: true
        schema:
          id: User Registration Request
          required:
            - userName
            - password
            - confirm_password
          properties:
            userName:
              type: string
              description: 用户名
            password:
              type: string
              description: 密码
            confirm_password:
              type: string
              description: 确认密码
    responses:
      200:
        description: 用户注册成功
        schema:
          type: object
          properties:
            message:
              type: string
              example: "create user success!"
      400:
        description: 用户注册失败
        schema:
          type: object
          properties:
            message:
              type: string
              example: "create user failed!"
      409:
        description: 用户名已存在
        schema:
          type: object
          properties:
            message:
              type: string
              example: "该用户已注册"
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing query parameter'}), 400
    
    required_fields = ["username", "password", "confirm"]
    if not all(field in data for field in required_fields):
        return jsonify({'message': '缺少必要的字段: username, password, confirm'}), 400

    userName = data["username"]
    password = data["password"]
    confirm_password = data["confirm"]
    status_code, message = userManagerHub.create_user(userName, password, confirm_password, DEFAULT_PERMISSIONS)
    if status_code == RESPONSE_STATUS_CODE_SUCCESS:
        logger.info(f"成功创建用户")
        return jsonify({'message': 'create user success!'}), RESPONSE_STATUS_CODE_SUCCESS
    elif status_code == 409:
        logger.warning(f"尝试注册已存在的用户: {userName}")
        return jsonify({'message': '该用户已注册'}), 409
    else:
        logger.error(f"创建用户失败: {userName}, 原因: {message}")
        return jsonify({'message': message}), status_code


@app.route('/login_user', methods=['POST'])
def login():
    """
    用户登录
    ---
    tags:
      - User Management
    description:
        用户登录接口
    parameters:
      - name: body
        in: body
        required: true
        schema:
          id: User Login Request
          required:
            - userName
            - password
          properties:
            userName:
              type: string
              description: 用户名
            password:
              type: string
              description: 密码
    responses:
      200:
        description: 用户登录成功
        schema:
          type: object
          properties:
            status:
              type: integer
              example: 200
            message:
              type: string
              example: "登录成功"
            data:
              type: object
              properties:
                token:
                  type: object
                  properties:
                    access_token:
                      type: string
                      example: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
                    expires_in:
                      type: integer
                      example: 3600
                    token_type:
                      type: string
                      example: "Bearer"
      400:
        description: 用户登录失败
        schema:
          type: object
          properties:
            message:
              type: string
              example: "登录失败！请检查用户名和密码"
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing query parameter'}), 400

    userName = data["username"]
    password = data["password"]
    user = userManagerHub.login(userName, password)
    if user.user_id > 0:
        logger.info(f"登录成功")

        # 生成唯一访问令牌
        access_token = jwt.encode({
            'jti': str(uuid.uuid4()),  # JWT ID，确保唯一性
            'user_id': user.user_id,
            'username': user.userName,
            'user_authority': user.user_authority,
            'roles': list(getattr(user, "roles", []) or []),
            'tool_permissions': list(getattr(user, "tool_permissions", []) or []),
            'allowed_regions': list(getattr(user, "allowed_regions", []) or []),
            'data_scope': dict(getattr(user, "data_scope", {}) or {}),
            'exp': datetime.utcnow() + JWT_EXPIRATION_DELTA
        }, SECRET_KEY, algorithm=JWT_ALGORITHM)

        logger.info(f"生成访问令牌: {access_token}")

        # 将用户信息存储到缓存
        session_cache[access_token] = {
            'user_id': user.user_id,
            'username': user.userName,
            'user_authority': user.user_authority,
            'roles': list(getattr(user, "roles", []) or []),
            'tool_permissions': list(getattr(user, "tool_permissions", []) or []),
            'allowed_regions': list(getattr(user, "allowed_regions", []) or []),
            'data_scope': dict(getattr(user, "data_scope", {}) or {}),
        }

        logger.info(f"用户访问令牌: {access_token}已存入本地缓存")

        # 返回符合要求的响应结构
        return jsonify({
            "status": RESPONSE_STATUS_CODE_SUCCESS,
            "message": "登录成功",
            "auth_data": {
                "token": {
                    "access_token": access_token,
                    "expires_in": 3600,
                    "token_type": "Bearer"
                }
            }
        }), 200
    else:
        return jsonify({'message': '登录失败！请检查用户名和密码'}), 400


@app.route('/logout_user', methods=['POST'])
def logout():
    """
    登出用户
    ---
    tags:
      - user management
    description:
      用户登出接口，通过 POST 方法接收用户 ID 并执行登出操作。
    parameters:
      - name: body
        in: body
        required: true
        schema:
          id: logout_body
          required:
            - user_id
          properties:
            user_id:
              type: integer
              format: int32
              description: 需要登出的用户 ID。
    responses:
      200:
        description: 登出成功
        schema:
          type: object
          properties:
            message:
              type: string
              example: logout success!
      400:
        description: 登出失败或缺少参数
        schema:
          type: object
          properties:
            error:
              type: string
              example: logout failed! 或 Missing query parameter
    """
    # 从Authorization头获取令牌
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Invalid token format'}), 400

    token = auth_header.split(' ')[1]

    # 从缓存中删除令牌
    if token in session_cache:
        del session_cache[token]

    return jsonify({'message': 'logout success!'}), RESPONSE_STATUS_CODE_SUCCESS


@app.route('/delete_tool_by_ids', methods=['POST'])
@require_permission
def delete_tool_db_by_ids():
    """
特定id工具数据库删除
---
tags:
  - Tool Delete By Ids
description:
    根据提供的ID列表删除工具
parameters:
  - name: body
    in: body
    required: true
    schema:
      id: Tool Delete Request
      required:
        - ids
      properties:
        ids:
          type: array
          items:
            type: integer
          description: 需要删除的工具ID列表
responses:
  200:
    description: 数据库删除成功
    schema:
      type: object
      properties:
        message:
          type: string
  400:
    description: 数据库未删除成功
    schema:
      type: object
      properties:
        error:
          type: string
"""
    data = request.get_json()
    if not data or 'ids' not in data:
        return jsonify({'error': 'Missing query parameter'}), 400

    ids = data['ids']
    toolManager.delete_tools(ids)
    logger.info(f"工具删除成功")
    return jsonify({'message': 'delete tool success! '}), RESPONSE_STATUS_CODE_SUCCESS


@app.route('/upload_tool', methods=['POST'])
@require_permission
def upload_file():
    """
        文件上传接口
        ---
        tags:
          - File Upload
        description:
            上传文件到服务器
        parameters:
          - name: file
            in: formData
            type: file
            required: true
            description: 要上传的文件
        responses:
          200:
            description: 文件上传成功，重定向到主页
          400:
            description: 未上传文件或文件名为空
    """
    if 'file' not in request.files:
        return jsonify({'error': '没有文件部分'}), 400

    uploaded_file = request.files['file']
    if uploaded_file.filename != '':
        uploaded_file.save('./api_data/' + uploaded_file.filename)

        time.sleep(5)

        toolManager.upload_file('./api_data/' + uploaded_file.filename)
        logger.info(f"文件上传成功")
        return jsonify({'message': 'upload file success! '}), RESPONSE_STATUS_CODE_SUCCESS
    else:
        return jsonify({'error': 'No file uploaded or filename is empty'}), 400


@app.route('/get_all_tools', methods=['GET'])
@require_permission
def get_all_tools():
    """
            获取所有工具接口
            ---
            tags:
              - Tools
            description:
                获取服务器上所有工具的列表
            responses:
              200:
                description: 成功获取工具列表
              500:
                description: 服务器内部错误
        """
    datas = toolManager.get_all_tools()
    logger.debug(f"Output: {datas}")
    return jsonify({"results": datas}), RESPONSE_STATUS_CODE_SUCCESS


@app.route('/api_planning', methods=['POST'])
@require_permission
def mesh_query():
    """
        Agent Planning
        ---
        tags:
            - Planning API
        description:
            Agent Planning接口，json格式
        parameters:
            - name: body
            in: body
            required: true
            schema:
                id: Planning Request body
                required:
                - query
            properties:
                modelName:
                    type: string
                    description: 模型名称
                    required: true
                temperature:
                    type: number
                    description: 温度参数
                    required: true
                api_key:
                    type: string
                    description: API 密钥
                    required: true
                api_url:
                    type: string
                    description: API 地址
                    required: true
                query:
                    type: string
                    description: 用户需求.
                task_id:
                    type: string
                    description: 任务ID，用于人类反馈.
                human_feedback:
                    type: string
                    description: 人类反馈信息.
        responses:
            200:
                description: 转化成功
        """
    data = request.get_json()
    logger.debug(f"接收到请求{data}")

    if not data:
        return jsonify({'error': 'Missing query parameter'}), 400
    else:
        # 检查是否是人类反馈
        if  "taskId" in data and data["taskId"]:
            logger.info(f"人类反馈[{data['taskId']}]开始处理.....")
            task_id = data["taskId"]
            human_feedback = data.get("query", "")
            executor.submit(process_human_feedback, task_id, human_feedback)  # 将人类反馈处理提交到线程池
            return jsonify({'task_id': task_id})
        else:
            # 处理新的查询请求
            user_id, session_id, tenant_id = _current_user_context(data)
            operator_context = _current_operator_context(data)
            task = taskManager.create_task(
                data["query"],
                user_id=user_id,
                session_id=session_id,
                tenant_id=tenant_id,
                operator_context=operator_context,
            )
            logger.info(f"新任务[{task.task_id}]开始处理.....")
            trace_id = traceManager.start_trace(task.task_id, task.user_id, task.session_id, data["query"])
            taskManager.update_task_recorder(task.task_id, task.status, task.system_output, trace_id=trace_id)
            task.trace_id = trace_id
            executor.submit(process_init_task, task, data)  # 将任务提交到线程池
            # threading.Thread(target=process_task, args=(task_id, data)).start()
            return jsonify({'task_id': task.task_id, 'session_id': task.session_id, 'trace_id': task.trace_id})


def _handle_pending_missing_params(task, human_feedback):
    if task.pending_action != "missing_params_clarify":
        return False

    payload = task.pending_payload or {}
    if _direct_control_intent(human_feedback) == "abort":
        taskManager.update_task_recorder(task.task_id, TASK_STATUS_FINISH, TASK_SYS_OUTPUT_STOP+"已取消参数补充任务",
                                        graph_title="任务已取消", pending_action="", pending_payload={})
        traceManager.add_event(task.trace_id, "missing_params_aborted", {"feedback": human_feedback})
        traceManager.finish_trace(task.trace_id, "用户取消参数补充任务", status="aborted")
        return True

    tool = _get_tool_by_id(payload.get("tool_id"))
    if tool is None:
        taskManager.update_task_recorder(task.task_id, TASK_STATUS_FINISH, TASK_SYS_OUTPUT_STOP+"无法找到待补参工具，请重新发起任务",
                                        graph_title="任务失败", pending_action="", pending_payload={})
        return True

    parse_result = _parse_slot_filling_feedback(payload, human_feedback)
    if parse_result.get("intent") == "abort":
        taskManager.update_task_recorder(task.task_id, TASK_STATUS_FINISH, TASK_SYS_OUTPUT_STOP+"已取消参数补充任务",
                                        graph_title="任务已取消", pending_action="", pending_payload={})
        return True
    if parse_result.get("intent") != "provide_info":
        taskManager.update_task_recorder(
            task.task_id,
            TASK_STATUS_WAIT_CONFIRM,
            _render_missing_params_message(payload.get("missing_params", [])),
            graph_title="等待参数补充",
            pending_action="missing_params_clarify",
            pending_payload=payload,
        )
        traceManager.add_event(task.trace_id, "missing_params_feedback_unclear", {
            "feedback": human_feedback,
            "parse": parse_result,
        })
        return True

    known_params = dict(payload.get("known_params", {}) or {})
    known_params.update(parse_result.get("filled_params", {}) or {})
    known_params, still_missing = _validate_tool_params(tool, known_params)
    traceManager.add_event(task.trace_id, "missing_params_feedback_parsed", {
        "feedback": human_feedback,
        "parse": parse_result,
        "merged_params": known_params,
        "still_missing": still_missing,
    })

    if still_missing:
        payload["known_params"] = known_params
        payload["missing_params"] = still_missing
        taskManager.update_task_recorder(
            task.task_id,
            TASK_STATUS_WAIT_CONFIRM,
            _render_missing_params_message(still_missing),
            graph_title="等待参数补充",
            curr_tool_id=tool.tool_id,
            curr_tool_param=known_params,
            pending_action="missing_params_clarify",
            pending_payload=payload,
        )
        return True

    memoryManager.add_message(task.user_id, task.session_id, "user", human_feedback,
                              task_id=task.task_id, message_type="missing_params_feedback")
    _request_tool_execution_confirmation(
        task,
        tool,
        known_params,
        payload.get("current_task_desc") or task.curr_task_desc,
        payload.get("original_query") or task.raw_query,
    )
    return True


def _parse_slot_filling_feedback(payload, human_feedback):
    if _direct_control_intent(human_feedback) == "abort":
        return {"intent": "abort", "filled_params": {}, "confidence": 1.0, "reason": "keyword abort"}

    prompt = promptRegistry.render("slot_filling_intent", model_name, {
        "tool_name": payload.get("tool_name", ""),
        "task_description": payload.get("current_task_desc", ""),
        "known_params": json.dumps(payload.get("known_params", {}), ensure_ascii=False),
        "missing_params": json.dumps(payload.get("missing_params", []), ensure_ascii=False),
        "user_feedback": human_feedback,
    })
    try:
        llm = LargeLanguageModel(model_base_url, model_api_key)
        output = llm.chat_completions(prompt, model_name, model_temperature, model_top_p)
        return StructuredOutputParser.parse_slot_filling_intent(output)
    except Exception as exc:
        logger.error(f"解析参数补充反馈失败: {exc}\n{traceback.format_exc()}")
        return {"intent": "unclear", "filled_params": {}, "confidence": 0.0, "reason": "parse failed"}


def process_human_feedback(task_id, human_feedback):
    """
    处理人类反馈的函数
    """
    logger.info(f"准备处理任务{task_id}的人类反馈，反馈内容：{human_feedback}，处理中......")
    try:
        task = taskManager.get_task_by_id(task_id)
        if task is None:
            logger.error(f"前端消息{human_feedback}的任务[{task_id}]不存在")
            taskManager.create_task(f"无法根据前端消息{human_feedback}查到后端任务，创建停止任务尝试让前端正常化", task_id)
            return

        # 未提供实际的反馈意见时
        if not human_feedback:
            logger.warning(f"用户未提供任务[{task_id}]反馈意见，继续让用户输入反馈")
            taskManager.update_task_recorder(task_id, TASK_STATUS_WAIT_CONFIRM, "请告诉我您的选择")
            return

        logger.debug(f"任务{task_id}的人类反馈{human_feedback}的task详情{task.to_dict()}")

        if _handle_pending_missing_params(task, human_feedback):
            return

        # 更新任务状态为正在处理
        taskManager.update_task_recorder(task_id, TASK_STATUS_RUNNING, "正在处理您的选择")

        # 获取ApiPlanningHub实例并处理人类反馈
        api_planning_hub = ApiPlanningHub(milvus_uri, model_path, milvus_db_name, model_name,
                                        model_temperature, model_top_p, mongo_host, mongo_db, mongo_port, topK,
                                        model_base_url, model_api_key, executor)

        # 处理人类反馈
        used_langgraph_resume = False
        try:
            from utils.config import use_langgraph_workflow
            if use_langgraph_workflow:
                from workflows import ERPAgentLangGraphWorkflow
                workflow = ERPAgentLangGraphWorkflow(api_planning_hub)
                if workflow.is_available:
                    used_langgraph_resume = True
                    workflow.resume_with_human_feedback(task, human_feedback)
                else:
                    api_planning_hub.api_planning_handle_human_feedback(task, human_feedback)
            else:
                api_planning_hub.api_planning_handle_human_feedback(task, human_feedback)
        except Exception as exc:
            logger.error(f"LangGraph resume failed: {exc}\n{traceback.format_exc()}")
            if used_langgraph_resume:
                raise
            api_planning_hub.api_planning_handle_human_feedback(task, human_feedback)
        updated_task = taskManager.get_task_by_id(task_id)
        if updated_task is not None and updated_task.status == TASK_STATUS_FINISH:
            _persist_assistant_memory(updated_task, updated_task.changed_query, updated_task.system_output)
            traceManager.finish_trace(updated_task.trace_id, updated_task.system_output, status="finished")

    except Exception as e:
        logger.error(f"任务[{task_id}]处理人类反馈失败: {e}\n{traceback.format_exc()}")
        taskManager.update_task_recorder(task_id, TASK_STATUS_FINISH, TASK_SYS_OUTPUT_STOP+"系统失败，无法处理您的选择，请联系管理员",
                                        graph_title="系统失败，无法处理您的选择，请联系管理员")


def process_init_task(task:Task, data):
    logger.info(f"准备处理任务{task.task_id}，任务数据：{data}，处理中......")
    try:
        query = data["query"]
        contexts = data.get("contexts", [])
        isCopilot = data.get("isCopilot", True)
        isContext = data.get("isContext", True)
        contextNumber = int(data.get("contextNumber", 6) or 6)

        curr_model_name = model_name
        curr_temperature = model_temperature
        curr_api_key = model_api_key
        curr_api_url = model_base_url
        memoryManager.add_message(task.user_id, task.session_id, "user", query,
                                  task_id=task.task_id, message_type="user_query")
        context_state = contextManager.build_context_state(
            task.user_id,
            task.session_id,
            query,
            contexts=contexts,
            context_number=contextNumber,
        )
        if not task.trace_id:
            trace_id = traceManager.start_trace(task.task_id, task.user_id, task.session_id, query)
            taskManager.update_task_recorder(task.task_id, TASK_STATUS_RUNNING, "任务上下文初始化完成",
                                            trace_id=trace_id,
                                            context_summary=context_state.get("summary", ""))
            task = taskManager.get_task_by_id(task.task_id)

        # # 历史遗留问题，曾经允许用户提供自己的模型参数，后关闭了该功能
        # if not(curr_api_key and curr_api_url and curr_model_name):
        #     curr_api_key = model_api_key
        #     curr_api_url = model_base_url
        #     curr_model_name = model_name
        # logger.info(f"准备处理任务{curr_api_key}，任务数据：{curr_api_url}，处理中......")
        # if not curr_temperature:
        #     curr_temperature = model_temperature
        # logger.info(f"准备处理任务{curr_temperature}，任务数据：{curr_model_name}，处理中......")

    except Exception as e:
        taskManager.update_task_recorder(task.task_id,  TASK_STATUS_FINISH, '获取前端参数失败！')
        logger.error(f"任务[{task.task_id}]获取前端参数失败: {e}\n{traceback.format_exc()}")
        return jsonify({'error': '获取前端参数失败！'}), 400

    if not isCopilot:
        #普通聊天模式
        llm = LargeLanguageModel(curr_api_url, curr_api_key)
        try:
            if isContext:
                chat_contexts = contexts or context_state.get("recent_messages", [])
                results = llm.context_chat_completions(
                    chat_contexts,
                    curr_model_name,
                    curr_temperature,
                    model_top_p,
                    context_state.get("recent_window", contextNumber),
                )
            else:
                results = llm.chat_completions(query, curr_model_name, curr_temperature,model_top_p)
        except:
            results = ""
        taskManager.update_task_recorder(task.task_id, TASK_STATUS_FINISH, results)
        _persist_assistant_memory(task, query, results)
        traceManager.finish_trace(task.trace_id, results, status="finished")
        if results is not None and len(results) != 0:
            return jsonify({"nodes": [], "edges": [], "systemOutput": results})
        else:
            return jsonify({"nodes": [], "edges": [], "systemOutput": results}), 400
    else:
        logger.info(f"Task[{task.task_id}] started successfully, Go on ===>")

    try:

        generate_task_hub = GenerateTaskHub(curr_model_name, curr_temperature, model_top_p,
                                            curr_api_url, curr_api_key, mongo_host, mongo_db, mongo_port, milvus_uri, milvus_db_name)
        if isContext:
            context_window = context_state.get("recent_window", contextNumber)
            source_contexts = contexts or context_state.get("recent_messages", [])
            target_contexts = source_contexts[-context_window:] if source_contexts else []
            target_query = generate_task_hub.gen_context_request_task(target_contexts) if target_contexts else query
        else:
            target_query = query
        target_query = contextManager.rewrite_query_with_context(target_query, context_state)
        traceManager.add_event(task.trace_id, "context_rewrite_completed", {
            "target_query": target_query,
            "is_context": bool(isContext),
            "recent_window": context_state.get("recent_window", contextNumber),
            "summary_used": bool(context_state.get("summary")),
        })

        taskManager.update_task_recorder(task.task_id, TASK_STATUS_RUNNING, "任务开始处理....",
                                        changed_query=target_query,
                                        context_summary=context_state.get("summary", ""))
        # 任务开始实际执行
        refreshed_task = taskManager.get_task_by_id(task.task_id)
        _run_planning_for_task(refreshed_task, target_query, data, curr_model_name, curr_temperature,
                               curr_api_url, curr_api_key)
        updated_task = taskManager.get_task_by_id(task.task_id)
        if updated_task is not None and updated_task.status == TASK_STATUS_FINISH:
            _persist_assistant_memory(updated_task, target_query, updated_task.system_output)
            traceManager.finish_trace(updated_task.trace_id, updated_task.system_output, status="finished")

    except Exception as e:
        logger.error(f"用户请求[{query}]的任务[{task.task_id}]处理失败: {e}\n{traceback.format_exc()}")
        taskManager.update_task_recorder(task.task_id, TASK_STATUS_FINISH,
                                            TASK_SYS_OUTPUT_STOP+f"系统发生严重错误，您的要求[{query}]处理失败，请联系你的系统管理员")
        # return jsonify({'message': f"您的要求[{query}]处理失败，任务编号[{task.task_id}]，请联系你的系统管理员"}), 400

@app.route('/api_task_status', methods=['POST'])
@require_permission
def get_task_status():
    """
        ---
        tags:
          - Task Management
        description:
          获取任务状态接口
        parameters:
          - name: body
            in: body
            required: true
            schema:
              id: Task Status Request Body
              required:
                - task_id
              properties:
                task_id:
                  type: string
                  description: 任务ID
                  required: true
        responses:
          200:
            description: 任务状态获取成功
            schema:
              id: Task Status Response
              properties:
                task:
                  type: object
                  description: 任务详情
                  properties:
                    task_id:
                      type: string
                      description: 任务ID
                    status:
                      type: integer
                      description: 任务状态
                    nodes:
                      type: array
                      items:
                        type: object
                      description: 节点列表
                    edges:
                      type: array
                      items:
                        type: object
                      description: 边列表
                    isSuccess:
                      type: string
                      description: 是否成功
                    systemOutput:
                      type: string
                      description: 系统输出
          400:
            description: 请求参数错误
            schema:
              id: Error Response
              properties:
                error:
                  type: string
                  description: 错误信息
          404:
            description: 任务未找到或仍在运行
            schema:
              id: Error Response
              properties:
                status:
                  type: string
                  description: 错误状态
        """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing query parameter'}), 400
    task_id = data['task_id']
    task = taskManager.get_task_by_id(task_id)
    if task is not None:
        return jsonify({'task': task.to_dict()})
    else:
        return jsonify({'status': 'Task not found or still running'}), 404


@app.route('/api_trace_status', methods=['POST'])
@require_permission
def get_trace_status():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Missing query parameter'}), 400
    trace = traceManager.get_trace(trace_id=data.get("trace_id", ""), task_id=data.get("task_id", ""))
    if trace:
        return jsonify({'trace': trace})
    return jsonify({'status': 'Trace not found'}), 404

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5001)
