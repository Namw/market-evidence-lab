from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.news_analysis.models import NewsAnalysisResult, NewsAnalysisRun
from apps.news_analysis.rules import match_fixed_rule

from .helpers import make_record


class FixedRuleTests(TestCase):
    def test_high_certainty_binance_marketing_rules_match_with_stable_ids(self):
        titles_and_rules = (
            ("Take the WOTD Quiz and Win Rewards", "binance_marketing_quiz_v1"),
            ("Referral Campaign: Refer Friends and Earn", "binance_marketing_referral_v1"),
            ("Join the Trading Competition", "binance_marketing_competition_v1"),
            ("Complete Tasks to Share Rewards", "binance_marketing_rewards_v1"),
        )
        for title, rule_id in titles_and_rules:
            with self.subTest(title=title):
                decision = match_fixed_rule(make_record(title=title))
                self.assertIsNotNone(decision)
                self.assertEqual(decision.rule_id, rule_id)
                self.assertEqual(decision.observation_result, "noise")
                self.assertEqual(decision.confidence, "high")

    def test_broad_words_and_material_other_asset_events_are_not_false_positives(self):
        titles = (
            "Binance Will Support the Ethereum Network Upgrade",
            "New Activity Is Now Live",
            "Binance Adds Support for Asset X",
            "Asset X Listing and Trading Rule Update",
            "Airdrop Distribution Following Protocol Governance Vote",
        )
        for title in titles:
            with self.subTest(title=title):
                self.assertIsNone(match_fixed_rule(make_record(title=title)))

    def test_ethereum_foundation_never_uses_binance_marketing_rules(self):
        record = make_record(
            source_code="ethereum_foundation",
            title="Trading Competition Quiz Giveaway",
        )
        self.assertIsNone(match_fixed_rule(record))


class ModelConstraintTests(TestCase):
    def setUp(self):
        self.record = make_record()
        self.run = NewsAnalysisRun.objects.create(
            trigger="manual",
            mode="incremental",
            analysis_version="news-v1",
            prompt_version="prompt-v1",
            model_name="test-model",
            started_at=timezone.now(),
        )

    def result_values(self):
        return {
            "news_record": self.record,
            "analysis_version": "news-v1",
            "prompt_version": "prompt-v1",
            "status": "success",
            "observation_result": "noteworthy",
            "event_type": "security_incident",
            "impact_scope": "crypto_market",
            "importance": "high",
            "rationale": "值得观察。",
            "confidence": "high",
            "method": "ai",
            "analysis_run": self.run,
            "analyzed_at": timezone.now(),
        }

    def test_same_news_and_analysis_version_is_unique(self):
        NewsAnalysisResult.objects.create(**self.result_values())
        with self.assertRaises(IntegrityError), transaction.atomic():
            NewsAnalysisResult.objects.create(**self.result_values())

    def test_model_validation_rejects_invalid_choices_and_incomplete_success(self):
        values = self.result_values()
        values["event_type"] = "bullish"
        result = NewsAnalysisResult(**values)
        with self.assertRaises(ValidationError):
            result.full_clean()

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
                analysis_version="news-v1",
                prompt_version="prompt-v1",
                model_name="test-model",
                started_at=timezone.now(),
            )
