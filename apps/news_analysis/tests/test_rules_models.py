from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.news_analysis.models import NewsAnalysisResult, NewsAnalysisRun
from apps.news_analysis.rules import match_fixed_rule

from .helpers import make_record


class FixedRuleTests(TestCase):
    def test_binance_marketing_titles_are_irrelevant(self):
        titles = (
            "Take the WOTD Quiz and Win Rewards",
            "Referral Campaign: Refer Friends and Earn",
            "Join the Trading Competition",
            "Complete Tasks to Share Rewards",
        )
        for title in titles:
            with self.subTest(title=title):
                decision = match_fixed_rule(make_record(title=title))
                self.assertIsNotNone(decision)
                self.assertEqual(decision.conclusion, "irrelevant")

    def test_high_certainty_eth_etf_title_rules_return_direction(self):
        cases = (
            ("SEC Approves Spot Ethereum ETF", "bullish"),
            ("Regulator Rejects Spot ETH ETF", "bearish"),
        )
        for title, conclusion in cases:
            with self.subTest(title=title):
                self.assertEqual(
                    match_fixed_rule(make_record(title=title)).conclusion, conclusion
                )

    def test_ambiguous_title_is_left_for_ai(self):
        self.assertIsNone(
            match_fixed_rule(
                make_record(title="Binance Will Support the Ethereum Network Upgrade")
            )
        )


class ModelConstraintTests(TestCase):
    def setUp(self):
        self.record = make_record()
        self.run = NewsAnalysisRun.objects.create(
            trigger="manual",
            mode="incremental",
            analysis_version="news-eth-v2",
            prompt_version="prompt-v2",
            model_name="test-model",
            started_at=timezone.now(),
        )

    def result_values(self):
        return {
            "news_record": self.record,
            "analysis_version": "news-eth-v2",
            "prompt_version": "prompt-v2",
            "status": "success",
            "conclusion": "bullish",
            "classification_stage": "title_ai",
            "rationale": "ETH 合规入口明确增加。",
            "content_summary": "正文摘要。",
            "method": "ai",
            "analysis_run": self.run,
            "analyzed_at": timezone.now(),
        }

    def test_same_news_and_analysis_version_is_unique(self):
        NewsAnalysisResult.objects.create(**self.result_values())
        with self.assertRaises(IntegrityError), transaction.atomic():
            NewsAnalysisResult.objects.create(**self.result_values())

    def test_model_validation_rejects_invalid_or_incomplete_success(self):
        values = self.result_values()
        values["conclusion"] = "maybe"
        with self.assertRaises(ValidationError):
            NewsAnalysisResult(**values).full_clean()

        values = self.result_values()
        values["rationale"] = ""
        with self.assertRaises(ValidationError):
            NewsAnalysisResult(**values).full_clean()

    def test_failed_result_cannot_contain_fake_classification(self):
        values = self.result_values()
        values["status"] = "failed"
        with self.assertRaises(ValidationError):
            NewsAnalysisResult(**values).full_clean()

    def test_only_one_running_run_per_version(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            NewsAnalysisRun.objects.create(
                trigger="manual",
                mode="incremental",
                analysis_version="news-eth-v2",
                prompt_version="prompt-v2",
                model_name="test-model",
                started_at=timezone.now(),
            )
