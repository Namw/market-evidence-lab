from datetime import UTC, datetime

from django import forms
from django.utils import timezone


class MarketScanForm(forms.Form):
    start_date = forms.DateField(
        label="UTC开始日期",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end_date = forms.DateField(
        label="UTC结束日期（不包含）",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if start_date is None or end_date is None:
            return cleaned_data
        if start_date >= end_date:
            raise forms.ValidationError("开始日期必须早于结束日期。")
        if (end_date - start_date).days > 366:
            raise forms.ValidationError("单次市场异常巡检范围最长为 366 天。")
        current_utc_date = timezone.now().astimezone(UTC).date()
        if end_date > current_utc_date:
            raise forms.ValidationError("结束日期不得超过当前 UTC 日期 00:00。")
        return cleaned_data

    @property
    def range_start(self) -> datetime:
        return datetime.combine(self.cleaned_data["start_date"], datetime.min.time(), UTC)

    @property
    def range_end(self) -> datetime:
        return datetime.combine(self.cleaned_data["end_date"], datetime.min.time(), UTC)

