"""Explain persisted execution records without inventing model reasoning."""
TOOL_LABELS = {
    "get_data_quality": "检查数据质量",
    "get_microstructure_summary": "查询盘口与成交摘要",
    "get_market_series": "查询分段时间序列",
    "compare_windows": "比较前后时间窗口",
    "build_trade_plan": "计算候选入场与止盈止损",
}


def execution_trace(turn, executions, baseline_evidence):
    steps = [{
        "id": "question", "title": "收到你的问题", "actor": "系统", "status": "done",
        "description": turn.question, "recorded_at": turn.created_at.isoformat(),
        "details": {"交易对": turn.conversation.symbol, "持有情景（分钟）": turn.horizon_minutes,
                    "本轮请求": "更新行情" if turn.refresh_data else "沿用原快照"},
    }]
    if turn.snapshot_id:
        steps.append({
            "id": "snapshot", "title": "准备行情快照与基础证据", "actor": "程序", "status": "done",
            "description": "本轮查询使用同一个固定快照。E0 是提供给模型的基础证据。",
            "details": {"快照编号": str(turn.snapshot_id), "数据截止": turn.snapshot.cutoff.isoformat(),
                        "快照采集时间": turn.snapshot.captured_at.isoformat(),
                        "计算版本": turn.snapshot.calculation_version, "基础证据 E0": baseline_evidence},
        })
    if turn.input_context:
        payload = turn.input_context.get("input", {})
        history = turn.input_context.get("history", [])
        steps.append({
            "id": "context", "title": "准备模型上下文", "actor": "程序", "status": "done",
            "description": f"带入 {len(history)} 轮已保存的历史问答，以及本轮问题和基础证据。",
            "details": {"历史问题": [item.get("question") for item in history],
                        "数据模式": payload.get("data_mode"),
                        "带入历史价格方案": bool(payload.get("last_discussed_plans")),
                        "提示词版本": turn.prompt_version, "提示词哈希": turn.prompt_hash},
        })
    for item in executions:
        result = item.result
        steps.append({
            "id": f"tool-{item.pk}", "title": TOOL_LABELS.get(item.name, item.name),
            "actor": "AI 选择工具 · 程序执行", "status": "error" if result.get("error") else "done" if result else "pending",
            "description": f"{item.name} · {result.get('evidence_id', '结果尚未保存')}",
            "recorded_at": item.created_at.isoformat(),
            "details": {"工具参数": item.arguments, "工具返回": result},
        })
    for event in turn.usage.get("format_recovery_events", []):
        steps.append({
            "id": f"format-retry-{event['attempt']}", "title": f"报告格式自动修复 · 第 {event['attempt']} 次",
            "actor": "程序检查 · AI 重新生成", "status": "done",
            "description": f"{event['reason']}。已保留此前成功的工具结果并请求重新生成；不代表本次修复已成功。",
            "recorded_at": event["recorded_at"], "details": {},
        })
    if turn.status == "succeeded":
        steps.append({
            "id": "outcome", "title": "保存最终报告", "actor": "AI 生成 · 程序校验与保存", "status": "done",
            "description": "已检查输出结构、证据引用和数据质量限制；这些检查不等于逐句核实回答中的数值。",
            "recorded_at": turn.finished_at.isoformat() if turn.finished_at else None,
            "details": {"引用证据": turn.report.get("evidence_ids", []), "限制提示": turn.report.get("guard_notes", [])},
        })
    else:
        steps.append({
            "id": "outcome", "title": "本轮未完成" if turn.status == "failed" else "当前进度",
            "actor": "系统", "status": "error" if turn.status == "failed" else "pending",
            "description": turn.safe_error or turn.progress, "details": {},
        })
    elapsed = (turn.finished_at - turn.started_at).total_seconds() if turn.finished_at and turn.started_at else None
    return {
        "status": turn.status, "steps": steps, "model": turn.model_name,
        "model_calls": turn.usage.get("model_calls", 0), "tool_records": len(executions),
        "token_usage": turn.usage.get("token_usage", {}),
        "elapsed_seconds": round(elapsed, 1) if elapsed is not None and elapsed >= 0 else None,
        "note": "按处理阶段和已保存的工具记录展示；未记录每次模型请求与工具调用的完整交错顺序，也不展示模型内部思考。同参数的缓存复用不另计工具记录。",
    }
