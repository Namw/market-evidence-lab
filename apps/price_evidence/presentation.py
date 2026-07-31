from copy import deepcopy


QUALITY_MESSAGES = {
    "complete": "24根1h K线完整，且聚合 OHLCV 与案例日K快照一致。",
    "partial": "当前 UTC 自然日缺少一根或多根1h K线，不生成完整价格形成结论。",
    "inconsistent": "1h数据齐全，但聚合结果与案例日K快照不一致，或数据无法安全计算。",
    "unavailable": "当前 UTC 自然日没有对应的1h K线，无法生成价格路径事实。",
}

HIGH_LOW_ORDER_LABELS = {
    "high_before_low": "先高后低",
    "low_before_high": "先低后高",
    "same_hour": "高低点位于同一小时",
}

DIRECTION_LABELS = {
    "up": "上涨",
    "down": "下跌",
    "flat": "平盘",
}


def prepare_price_evidence_for_display(price_evidence):
    price_evidence.quality_message = QUALITY_MESSAGES[price_evidence.quality_status]
    metrics = deepcopy(price_evidence.metrics_snapshot)
    price_evidence.display_metrics = metrics
    if metrics:
        metrics["high_low"]["order_label"] = HIGH_LOW_ORDER_LABELS[
            metrics["high_low"]["order"]
        ]
        metrics["largest_hourly_change"]["direction_label"] = DIRECTION_LABELS[
            metrics["largest_hourly_change"]["direction"]
        ]
    return price_evidence
