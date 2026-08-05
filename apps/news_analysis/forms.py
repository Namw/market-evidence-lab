from django import forms

from apps.news_data.models import NewsSource

from .models import (
    CanonicalEvent,
    EventMergeRun,
    NewsAnalysisResult,
    ObjectiveFactExtractionResult,
)
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


class EventMergeRunFilterForm(forms.Form):
    status = forms.ChoiceField(
        label="状态", required=False, choices=_with_blank(EventMergeRun.Status.choices)
    )
    trigger = forms.ChoiceField(
        label="触发方式", required=False, choices=_with_blank(EventMergeRun.Trigger.choices)
    )
    started_from = forms.DateField(
        label="开始日期", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    started_to = forms.DateField(
        label="结束日期", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    algorithm_version = forms.CharField(label="算法版本", required=False)
    prompt_version = forms.CharField(label="Prompt 版本", required=False)
    model = forms.CharField(label="模型", required=False)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("started_from")
        end = cleaned.get("started_to")
        if start and end and start > end:
            raise forms.ValidationError("开始日期不能晚于结束日期。")
        return cleaned


class CanonicalEventFilterForm(forms.Form):
    status = forms.ChoiceField(
        label="状态", required=False, choices=_with_blank(CanonicalEvent.Status.choices)
    )
    grouping_method = forms.ChoiceField(
        label="归组方式",
        required=False,
        choices=_with_blank(CanonicalEvent.GroupingMethod.choices),
    )
    source = forms.ModelChoiceField(
        label="来源", queryset=NewsSource.objects.all(), required=False, empty_label="全部来源"
    )
    publication_start = forms.DateField(
        label="成员发布开始", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    publication_end = forms.DateField(
        label="成员发布结束", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    keyword = forms.CharField(label="关键词", required=False)
    min_members = forms.IntegerField(label="最小成员数", required=False, min_value=1)

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("publication_start")
        end = cleaned.get("publication_end")
        if start and end and start > end:
            raise forms.ValidationError("成员发布开始日期不能晚于结束日期。")
        return cleaned
