from django import forms

from apps.news_data.models import NewsSource

from .models import NewsAnalysisResult, ObjectiveFactExtractionResult
from .objective_fact_schema import EVENT_STATUS_CHOICES
from .objective_fact_validation import INFORMATION_COMPLETENESS


def _with_blank(choices, label="全部"):
    return [("", label), *choices]


class NewsClassificationFilterForm(forms.Form):
    source = forms.ModelChoiceField(
        label="来源",
        queryset=NewsSource.objects.all(),
        required=False,
        empty_label="全部来源",
    )
    authority_level = forms.ChoiceField(
        label="来源权威",
        choices=_with_blank(NewsSource.AuthorityLevel.choices, "全部等级"),
        required=False,
    )
    conclusion = forms.ChoiceField(
        label="ETH 结论",
        choices=_with_blank(
            [
                choice
                for choice in NewsAnalysisResult.Conclusion.choices
                if choice[0] != NewsAnalysisResult.Conclusion.IRRELEVANT
            ]
        ),
        required=False,
    )
    classification_stage = forms.ChoiceField(
        label="判断阶段",
        choices=_with_blank(NewsAnalysisResult.ClassificationStage.choices),
        required=False,
    )
    start_time = forms.DateTimeField(
        label="分类开始时间",
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}
        ),
    )
    end_time = forms.DateTimeField(
        label="分类结束时间",
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}
        ),
    )

    def clean(self):
        cleaned = super().clean()
        start_time = cleaned.get("start_time")
        end_time = cleaned.get("end_time")
        if start_time and end_time and start_time > end_time:
            raise forms.ValidationError("分类开始时间不能晚于结束时间。")
        return cleaned


class ObjectiveFactFilterForm(forms.Form):
    published_start = forms.DateField(
        label="新闻开始日期",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    published_end = forms.DateField(
        label="新闻结束日期",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    source = forms.ModelChoiceField(
        label="新闻来源",
        queryset=NewsSource.objects.all(),
        required=False,
        empty_label="全部来源",
    )
    keyword = forms.CharField(
        label="关键词",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "标题或客观摘要"}),
    )
    event_status = forms.ChoiceField(
        label="事件状态",
        required=False,
        choices=_with_blank(EVENT_STATUS_CHOICES),
    )
    information_completeness = forms.ChoiceField(
        label="信息完整度",
        required=False,
        choices=_with_blank(
            [(value, value) for value in sorted(INFORMATION_COMPLETENESS)]
        ),
    )
    extraction_status = forms.ChoiceField(
        label="提取状态",
        required=False,
        choices=_with_blank(
            [
                ("not_extracted", "尚未提取"),
                *ObjectiveFactExtractionResult.ExtractionStatus.choices,
            ]
        ),
    )
    validation_status = forms.ChoiceField(
        label="校验状态",
        required=False,
        choices=_with_blank(ObjectiveFactExtractionResult.ValidationStatus.choices),
    )
    has_body = forms.ChoiceField(
        label="是否保存正文",
        required=False,
        choices=[("", "全部"), ("yes", "有正文"), ("no", "无正文")],
    )
    facts_count = forms.ChoiceField(
        label="事实数量",
        required=False,
        choices=[
            ("", "全部"),
            ("zero", "0 条"),
            ("one", "1 条"),
            ("multiple", "多条"),
        ],
    )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("published_start")
        end = cleaned.get("published_end")
        if start and end and start > end:
            raise forms.ValidationError("新闻开始日期不能晚于结束日期。")
        return cleaned
