from django import forms

from .models import (
    DeribitOptionsSchedule,
    KlineSchedule,
    MarketPilotSchedule,
    NewsAISchedule,
    NewsWorkflowSchedule,
)


class KlineScheduleForm(forms.ModelForm):
    lookback_days = forms.IntegerField(
        label="回看天数",
        min_value=1,
        max_value=30,
        help_text="允许 1 至 30 天，只处理完整 UTC 自然日。",
        widget=forms.NumberInput(attrs={"min": "1", "max": "30"}),
    )

    class Meta:
        model = KlineSchedule
        fields = ["enabled", "run_time", "lookback_days"]
        labels = {
            "enabled": "启用每日自动任务",
            "run_time": "每日执行时间",
        }
        help_texts = {
            "run_time": "北京时间，每天执行一次，不需要填写 Cron。",
        }
        widgets = {
            "enabled": forms.CheckboxInput(),
            "run_time": forms.TimeInput(attrs={"type": "time", "step": "60"}),
        }


class DeribitOptionsScheduleForm(forms.ModelForm):
    dvol_lookback_days = forms.IntegerField(
        label="DVOL 回补天数",
        min_value=1,
        max_value=30,
        help_text="每次同步最近 1 至 30 天的小时 DVOL；期权合约和行情快照采集当前状态。",
        widget=forms.NumberInput(attrs={"min": "1", "max": "30"}),
    )

    class Meta:
        model = DeribitOptionsSchedule
        fields = ["enabled", "run_time", "dvol_lookback_days"]
        labels = {
            "enabled": "启用每日自动任务",
            "run_time": "每日执行时间",
        }
        help_texts = {
            "run_time": "北京时间，每天执行一次，不需要填写 Cron。",
        }
        widgets = {
            "enabled": forms.CheckboxInput(),
            "run_time": forms.TimeInput(attrs={"type": "time", "step": "60"}),
        }


class NewsWorkflowScheduleForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        interval_hours = self.instance.interval_hours if self.instance.pk else 24
        group_label = self.instance.get_feed_group_display()
        self.fields["enabled"].label = f"启用{group_label}工作流"
        if interval_hours == 24:
            self.fields["run_time"].label = "每日执行时间"
            self.fields["run_time"].help_text = (
                "北京时间，每天执行一次，不需要填写 Cron。"
            )
        else:
            self.fields["run_time"].label = "每日首轮执行时间"
            self.fields["run_time"].help_text = (
                f"北京时间，以该时间为每日首轮，此后每 {interval_hours} 小时执行一次。"
            )

    class Meta:
        model = NewsWorkflowSchedule
        fields = ["enabled", "run_time"]
        widgets = {
            "enabled": forms.CheckboxInput(),
            "run_time": forms.TimeInput(attrs={"type": "time", "step": "60"}),
        }


class NewsAIScheduleForm(forms.ModelForm):
    class Meta:
        model = NewsAISchedule
        fields = [
            "enabled",
            "run_time",
            "max_direction_requests",
            "max_objective_records",
            "max_event_ai_calls",
        ]
        labels = {
            "enabled": "启用每日 DeepSeek 增量分析",
            "run_time": "每日执行时间",
            "max_direction_requests": "方向分类最多请求数",
            "max_objective_records": "客观事实最多处理新闻数",
            "max_event_ai_calls": "事件合并最多 AI 比较数",
        }
        help_texts = {
            "run_time": "北京时间，建议安排在深夜；分时本身不改变 DeepSeek 单价。",
            "max_direction_requests": "限制方向分类本轮实际 API 请求，重试也计入。",
            "max_objective_records": "超过上限的新闻保留到下一轮继续增量处理。",
            "max_event_ai_calls": "预计超过上限时整轮事件合并暂缓，避免不可控费用；填 0 表示禁止 AI 比较。",
        }
        widgets = {
            "enabled": forms.CheckboxInput(),
            "run_time": forms.TimeInput(attrs={"type": "time", "step": "60"}),
            "max_direction_requests": forms.NumberInput(attrs={"min": "1", "max": "500"}),
            "max_objective_records": forms.NumberInput(attrs={"min": "1", "max": "1000"}),
            "max_event_ai_calls": forms.NumberInput(attrs={"min": "0", "max": "1000"}),
        }


class MarketPilotScheduleForm(forms.ModelForm):
    threshold_pct = forms.DecimalField(
        label="异常阈值",
        min_value=0.1,
        max_value=20,
        decimal_places=3,
        help_text="按窗口开盘到收盘的绝对涨跌幅判断。",
        widget=forms.NumberInput(attrs={"min": "0.1", "max": "20", "step": "0.1"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        symbol = self.instance.symbol.removesuffix("USDT")
        hours = self.instance.window_hours
        self.fields["enabled"].label = f"启用 {symbol} {hours}小时影子监控"
        self.fields["threshold_pct"].label = f"{hours}小时异常阈值"
        self.fields["threshold_pct"].help_text = (
            f"按{hours}小时开盘到收盘的绝对涨跌幅判断；"
            f"当前建议保持 {self.instance.threshold_pct}%。"
        )
        self.fields["run_time"].help_text = (
            f"北京时间；以该时间为首轮，此后每 {self.instance.interval_hours} 小时执行一次。"
        )

    class Meta:
        model = MarketPilotSchedule
        fields = ["enabled", "run_time", "threshold_pct"]
        labels = {"run_time": "每日首轮执行时间"}
        widgets = {
            "enabled": forms.CheckboxInput(),
            "run_time": forms.TimeInput(attrs={"type": "time", "step": "60"}),
        }
