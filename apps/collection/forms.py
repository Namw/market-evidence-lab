from datetime import UTC, datetime

from django import forms
from django.utils import timezone


class CollectionForm(forms.Form):
    start_date = forms.DateField(
        label="开始日期",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end_date = forms.DateField(
        label="结束日期（不包含）",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    intervals = forms.MultipleChoiceField(
        label="采集周期",
        choices=(("1d", "1d 日线"), ("1h", "1h 小时线")),
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "请至少选择一个采集周期。"},
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
            raise forms.ValidationError("单次采集范围最长为 366 天。")
        if start_date > timezone.now().date():
            raise forms.ValidationError("开始日期不能是未来日期。")
        return cleaned_data

    @property
    def range_start(self) -> datetime:
        return datetime.combine(self.cleaned_data["start_date"], datetime.min.time(), UTC)

    @property
    def range_end(self) -> datetime:
        return datetime.combine(self.cleaned_data["end_date"], datetime.min.time(), UTC)
