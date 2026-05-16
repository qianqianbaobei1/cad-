"""Flask REST API — 所有路由定义"""
import json
import uuid
import traceback
from flask import Blueprint, request, jsonify, send_file

from 知识库读取 import (
    get_t3_materials, get_t3_detail, get_categories, get_category_detail,
    get_t3_category_mapping, get_standards, get_standard_detail,
    get_quota_list, get_quota_detail, get_provinces,
    global_search, get_stats,
)
from 流水线执行 import run_breakdown, run_validation
from 省份适配 import load_province_context, load_all_provinces
from 项目存储 import (
    list_projects, create_project, get_project, delete_project,
    add_run_to_project, get_rules, update_rules, reset_rules,
)

api = Blueprint("api", __name__)

# ══════════════════════════════════════
# 拆解相关
# ══════════════════════════════════════

@api.route("/breakdown", methods=["POST"])
def breakdown():
    try:
        data = request.get_json(force=True)
        content = data.get("content", "")
        province = data.get("province", "SN").strip().upper()
        if not content:
            return jsonify({"ok": False, "error": "请输入清单内容"}), 400
        result = run_breakdown(content, province=province)

        # 如果指定了项目ID，关联拆解记录
        project_id = data.get("project_id")
        if project_id:
            add_run_to_project(project_id, result)

        # 缓存拆解结果
        run_cache[result["run_id"]] = result
        return jsonify(result)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

run_cache = {}

@api.route("/breakdown/<run_id>", methods=["GET"])
def get_breakdown(run_id):
    if run_id in run_cache:
        return jsonify(run_cache[run_id])
    return jsonify({"ok": False, "error": "拆解记录不存在"}), 404

@api.route("/breakdown/confirm", methods=["POST"])
def confirm_breakdown():
    """人工复核确认"""
    data = request.get_json(force=True)
    run_id = data.get("run_id")
    actions = data.get("actions", [])
    if run_id not in run_cache:
        return jsonify({"ok": False, "error": "拆解记录不存在"}), 404

    result = run_cache[run_id]
    items_by_id = {i["id"]: i for i in result["items"]}

    for action in actions:
        item_id = action.get("item_id")
        act_type = action.get("action")  # confirm / modify / add / reject
        if act_type == "confirm" and item_id in items_by_id:
            items_by_id[item_id]["confirmed"] = True
        elif act_type == "modify" and item_id in items_by_id:
            for key in ("material_name", "unit", "waste_rate", "supply", "role"):
                if key in action:
                    items_by_id[item_id][key] = action[key]
            items_by_id[item_id]["confirmed"] = True
            items_by_id[item_id]["modified"] = True
        elif act_type == "add":
            new_item = {
                "id": uuid.uuid4().hex[:8],
                "material_name": action.get("material_name", ""),
                "material_id": action.get("material_id", ""),
                "unit": action.get("unit", ""),
                "supply": action.get("supply", "乙供"),
                "waste_rate": float(action.get("waste_rate", 0)),
                "confidence": "manual",
                "confidence_reason": "人工添加",
                "confirmed": True,
                "added_manually": True,
            }
            result["items"].append(new_item)
        elif act_type == "reject" and item_id in items_by_id:
            items_by_id[item_id]["rejected"] = True

    result["confirmed_count"] = sum(1 for i in result["items"] if i.get("confirmed"))
    result["rejected_count"] = sum(1 for i in result["items"] if i.get("rejected"))
    run_cache[run_id] = result
    return jsonify({"ok": True, "run_id": run_id, "confirmed": result["confirmed_count"]})


# ══════════════════════════════════════
# 知识库查询
# ══════════════════════════════════════

@api.route("/kb/t3", methods=["GET"])
def kb_t3():
    search = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    return jsonify(get_t3_materials(search, page, per_page))

@api.route("/kb/t3/<material_id>", methods=["GET"])
def kb_t3_detail(material_id):
    r = get_t3_detail(material_id)
    if r:
        return jsonify(r)
    return jsonify({"error": "未找到该物料"}), 404

@api.route("/kb/categories", methods=["GET"])
def kb_categories():
    search = request.args.get("q", "")
    return jsonify(get_categories(search))

@api.route("/kb/categories/<leaf_id>", methods=["GET"])
def kb_category_detail(leaf_id):
    r = get_category_detail(leaf_id)
    if r:
        return jsonify(r)
    return jsonify({"error": "未找到该品类节点"}), 404

@api.route("/kb/categories/mapping", methods=["GET"])
def kb_category_mapping():
    return jsonify(get_t3_category_mapping())

@api.route("/kb/standards", methods=["GET"])
def kb_standards():
    search = request.args.get("q", "")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    return jsonify(get_standards(search, page, per_page))

@api.route("/kb/standards/<standard_id>", methods=["GET"])
def kb_standard_detail(standard_id):
    r = get_standard_detail(standard_id)
    if r:
        return jsonify(r)
    return jsonify({"error": "未找到该规范"}), 404

@api.route("/kb/quota", methods=["GET"])
def kb_quota():
    search = request.args.get("q", "")
    province = request.args.get("province", "")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    return jsonify(get_quota_list(search, province, page, per_page))

@api.route("/kb/quota/<quota_id>", methods=["GET"])
def kb_quota_detail(quota_id):
    r = get_quota_detail(quota_id)
    if r:
        return jsonify(r)
    return jsonify({"error": "未找到该定额"}), 404

@api.route("/kb/provinces", methods=["GET"])
def kb_provinces():
    """获取全部省份配置"""
    from dataclasses import asdict
    profiles = load_all_provinces()
    # Convert dataclass to dict for JSON
    result = []
    for p in profiles:
        d = {
            "code": p.code, "name": p.name, "full_name": p.full_name,
            "climate_zone": p.climate_zone, "is_coastal": p.is_coastal,
            "has_heating": p.has_heating, "is_baseline": p.is_baseline,
            "install_quota": p.install_quota, "install_year": p.install_year,
            "build_quota": p.build_quota, "build_year": p.build_year,
        }
        result.append(d)
    return jsonify(result)


@api.route("/kb/province/<province_code>", methods=["GET"])
def kb_province_detail(province_code):
    """获取单个省份完整配置+规则摘要"""
    ctx = load_province_context(province_code)
    profile = ctx.profile
    if not profile:
        return jsonify({"error": "未配置该省份数据", "province": province_code, "available": [p.code for p in load_all_provinces()]}), 404

    # 规则摘要
    rules_summary = {}
    for r in ctx.material_rules:
        rules_summary.setdefault(r.rule_type, []).append({
            "section_code": r.section_code or "*",
            "material_id": r.material_id or "*",
            "reason": r.reason,
        })

    # 参数覆盖摘要
    param_summary = {}
    for (map_code, pname), ov in ctx.param_overrides.items():
        if ov.diff_tag == "SAME":
            continue
        param_summary.setdefault(ov.diff_tag, []).append({
            "t2_map_code": map_code, "param": pname,
            "base": ov.base_value, "override": ov.override_value,
            "source": ov.quota_source[:80],
        })

    return jsonify({
        "profile": {
            "code": profile.code, "name": profile.name, "full_name": profile.full_name,
            "climate_zone": profile.climate_zone, "is_coastal": profile.is_coastal,
            "has_heating": profile.has_heating, "is_baseline": profile.is_baseline,
            "install_quota": profile.install_quota, "install_year": profile.install_year,
            "build_quota": profile.build_quota, "build_year": profile.build_year,
        },
        "rules": rules_summary,
        "param_overrides": param_summary,
        "t5_dimension_count": len(ctx.t5_dimensions),
    })

@api.route("/kb/search", methods=["GET"])
def kb_search():
    q = request.args.get("q", "")
    if not q:
        return jsonify({"t3": [], "standards": [], "categories": [], "quota": []})
    return jsonify(global_search(q))

@api.route("/kb/validate", methods=["POST"])
def kb_validate():
    data = request.get_json(force=True)
    material_id = data.get("material_id", "")
    params = data.get("params", {})
    return jsonify(run_validation(material_id, params))


# ══════════════════════════════════════
# 项目管理
# ══════════════════════════════════════

@api.route("/projects", methods=["GET"])
def api_list_projects():
    return jsonify(list_projects())

@api.route("/projects", methods=["POST"])
def api_create_project():
    data = request.get_json(force=True)
    return jsonify(create_project(data))

@api.route("/projects/<project_id>", methods=["GET"])
def api_get_project(project_id):
    r = get_project(project_id)
    if r:
        return jsonify(r)
    return jsonify({"error": "项目不存在"}), 404

@api.route("/projects/<project_id>", methods=["DELETE"])
def api_delete_project(project_id):
    delete_project(project_id)
    return jsonify({"ok": True})


# ══════════════════════════════════════
# 规则配置
# ══════════════════════════════════════

@api.route("/rules", methods=["GET"])
def api_get_rules():
    return jsonify(get_rules())

@api.route("/rules", methods=["PUT"])
def api_update_rules():
    data = request.get_json(force=True)
    return jsonify(update_rules(data))

@api.route("/rules/reset", methods=["POST"])
def api_reset_rules():
    return jsonify(reset_rules())


# ══════════════════════════════════════
# 系统
# ══════════════════════════════════════

# ══════════════════════════════════════
# 调试端点
# ══════════════════════════════════════

@api.route("/debug/ai-test", methods=["GET"])
def debug_ai_test():
    """直接测试 AI API 连接"""
    import sys, traceback, os
    result = {"steps": []}
    try:
        result["steps"].append("加载配置...")
        from 配置 import _load_dotenv, BASE_DIR
        _load_dotenv(BASE_DIR / ".env")
        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("MOONSHOT_API_KEY", "")
        result["key_loaded"] = bool(api_key)
        result["model"] = os.getenv("DEEPSEEK_MODEL") or os.getenv("MOONSHOT_MODEL","")
        result["steps"].append("创建客户端...")
        from AI客户端 import get_client
        client = get_client()
        result["steps"].append("调用API...")
        test_model = request.args.get("model", os.getenv("DEEPSEEK_MODEL") or os.getenv("MOONSHOT_MODEL",""))
        use_thinking = request.args.get("thinking", "0") == "1"
        max_tok = request.args.get("mt", 200, type=int)
        kwargs = dict(model=test_model, messages=[{"role":"user","content":"回复OK即可"}], max_tokens=max_tok)
        if use_thinking:
            kwargs["extra_body"] = {"thinking": {"mode": "enabled"}}
        resp = client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        result["success"] = True
        result["response"] = msg.content
        result["finish_reason"] = resp.choices[0].finish_reason
        result["has_reasoning"] = hasattr(msg, "reasoning_content")
        if hasattr(msg, "reasoning_content"):
            result["reasoning"] = (msg.reasoning_content or "")[:500]
    except Exception as e:
        result["success"] = False
        result["error"] = str(e)
        result["error_type"] = type(e).__name__
        result["traceback"] = traceback.format_exc()
    return jsonify(result)


@api.route("/health", methods=["GET"])
def api_health():
    return jsonify({"status": "ok"})

@api.route("/stats", methods=["GET"])
def api_stats():
    return jsonify(get_stats())


# ══════════════════════════════════════
# 人工纠错反馈
# ══════════════════════════════════════

@api.route("/feedback", methods=["POST"])
def submit_feedback():
    """记录用户的人工纠错"""
    try:
        from feedback_handler import record_correction
    except ImportError:
        return jsonify({"ok": False, "error": "feedback_handler 模块不可用"}), 500

    data = request.get_json(force=True)
    record = record_correction(
        run_id=data.get("run_id", ""),
        boq_code=data.get("boq_code", ""),
        boq_name=data.get("boq_name", ""),
        action=data.get("action", ""),
        original_item=data.get("original"),
        corrected_item=data.get("corrected"),
        reason=data.get("reason", ""),
        reviewer=data.get("reviewer", ""),
    )
    return jsonify({
        "ok": True,
        "record_id": record.id,
        "correction_type": record.action,
    })


@api.route("/feedback/suggestions", methods=["GET"])
def get_feedback_suggestions():
    """获取基于反馈的KB更新建议"""
    try:
        from feedback_handler import analyze_feedback_batch
    except ImportError:
        return jsonify({"ok": False, "error": "feedback_handler 模块不可用"}), 500

    since_days = request.args.get("since_days", 30, type=int)
    min_freq = request.args.get("min_frequency", 3, type=int)
    analysis = analyze_feedback_batch(since_days=since_days, min_frequency=min_freq)
    return jsonify({
        "ok": True,
        "total_corrections": analysis.total_corrections,
        "period_days": analysis.period_days,
        "by_action": analysis.by_action,
        "by_code": analysis.by_code,
        "suggestions": [
            {
                "type": s["type"],
                "target": s["target"],
                "frequency": s["frequency"],
                "suggestion": s["suggestion"],
                "priority": s["priority"],
            }
            for s in analysis.suggestions
        ],
    })


@api.route("/feedback/stats", methods=["GET"])
def get_feedback_stats():
    """获取反馈系统统计"""
    try:
        from feedback_handler import get_feedback_stats as gfs
    except ImportError:
        return jsonify({"ok": False, "error": "feedback_handler 模块不可用"}), 500
    return jsonify(gfs())
