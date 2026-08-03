from django import forms

from .models import KlineSchedule, NewsWorkflowSchedule


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
            "run_time": "Asia/Shanghai，本地每日执行，不需要填写 Cron。",
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
                "Asia/Shanghai，每天执行一次，不需要填写 Cron。"
            )
        else:
            self.fields["run_time"].label = "每日首轮执行时间"
            self.fields["run_time"].help_text = (
                f"Asia/Shanghai，以该时间为每日首轮，此后每 {interval_hours} 小时执行一次。"
            )

    class Meta:
        model = NewsWorkflowSchedule
        fields = ["enabled", "run_time"]
        widgets = {
            "enabled": forms.CheckboxInput(),
            "run_time": forms.TimeInput(attrs={"type": "time", "step": "60"}),
        }
