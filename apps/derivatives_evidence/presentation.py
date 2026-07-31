from copy import deepcopy


OI_DIRECTION_LABELS = {
    "expansion": "扩张",
    "contraction": "收缩",
    "neutral": "不明显",
    None: "暂不判断",
}
PRICE_DIRECTION_LABELS = {"up": "上涨", "down": "下跌", "neutral": "中性", None: "不可用"}
FUNDING_STATUS_LABELS = {"complete": "完整", "partial": "部分可用", "unavailable": "不可用"}
TREND_LABELS = {"up": "上升", "down": "下降", "flat": "持平", None: "不可用"}
CROWDING_LABELS = {
    "significant_positive": "明显正 Funding",
    "significant_negative": "明显负 Funding",
    "positive": "正 Funding",
    "negative": "负 Funding",
    "near_zero": "接近零",
    None: "不可用",
}
VALUE_DIRECTION_LABELS = {"up": "上升", "down": "下降", "flat": "持平", None: "不可用"}


def prepare_derivatives_evidence_for_display(evidence):
    snapshot = deepcopy(evidence.calculation_snapshot)
    evidence.display_snapshot = snapshot
    if snapshot:
        oi = snapshot["oi"]
        oi["direction_label"] = OI_DIRECTION_LABELS[oi["quantity_direction"]]
        oi["value_direction_label"] = VALUE_DIRECTION_LABELS[oi["value_direction"]]
        price = snapshot["price"]
        price["direction_label"] = PRICE_DIRECTION_LABELS[price["daily_direction"]]
        for interval in snapshot["funding_intervals"]:
            interval["status_label"] = FUNDING_STATUS_LABELS[interval["status"]]
            interval["trend_label"] = TREND_LABELS[interval["trend"]]
            interval["average_direction_label"] = CROWDING_LABELS[interval["average_direction"]]
    return evidence
