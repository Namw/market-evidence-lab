from django import forms

from apps.news_data.models import NewsSource

from .models import NewsAnalysisResult


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
