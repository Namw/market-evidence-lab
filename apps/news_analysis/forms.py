from django import forms

from apps.news_data.models import NewsSource

from .models import NewsAnalysisResult


def _with_blank(choices, label="全部"):
    return [("", label), *choices]


class NewsObservationFilterForm(forms.Form):
    source = forms.ModelChoiceField(
        queryset=NewsSource.objects.all(), required=False, empty_label="全部来源"
    )
    observation_result = forms.ChoiceField(
        choices=_with_blank(NewsAnalysisResult.ObservationResult.choices),
        required=False,
    )
    event_type = forms.ChoiceField(
        choices=_with_blank(NewsAnalysisResult.EventType.choices), required=False
    )
    impact_scope = forms.ChoiceField(
        choices=_with_blank(NewsAnalysisResult.ImpactScope.choices), required=False
    )
    importance = forms.ChoiceField(
        choices=_with_blank(NewsAnalysisResult.Level.choices), required=False
    )
    confidence = forms.ChoiceField(
        choices=_with_blank(NewsAnalysisResult.Level.choices), required=False
    )
    method = forms.ChoiceField(
        choices=_with_blank(NewsAnalysisResult.Method.choices), required=False
    )
    status = forms.ChoiceField(
        choices=_with_blank(NewsAnalysisResult.Status.choices), required=False
    )
