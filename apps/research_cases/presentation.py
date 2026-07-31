SIGNAL_LABELS = {
    "abnormal_change_up": "大幅上涨",
    "abnormal_change_down": "大幅下跌",
    "volume_spike": "成交量异常",
    "long_upper_wick": "长上影线",
    "long_lower_wick": "长下影线",
}


def signal_badges(signals):
    return [
        {
            "type": signal.get("type", "unknown"),
            "label": SIGNAL_LABELS.get(
                signal.get("type"),
                signal.get("type", "未知类型"),
            ),
        }
        for signal in signals
    ]


def _human_percentage(value):
    return f"{value:.2f}"


def prepare_case_for_display(case):
    case.signal_badges = signal_badges(case.anomaly_signals_snapshot)
    labels = "、".join(badge["label"] for badge in case.signal_badges)
    case.anomaly_summary = (
        f"{case.event_time:%Y-%m-%d} UTC，{case.symbol} 日K线触发{labels or '未知异常'}；"
        f"当日涨跌幅 {_human_percentage(case.price_change_pct)}%、"
        f"振幅 {_human_percentage(case.amplitude_pct)}%。"
    )
    return case
