from django import forms

from .models import KlineSchedule


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
