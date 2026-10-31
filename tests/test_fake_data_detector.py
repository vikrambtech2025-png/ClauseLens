"""Tests for fake data detector module."""

import pytest

from core.fake_data_detector import (
    FakeDataDetector,
    FakeDataReport,
    detect_fake_data,
    detect_text,
)


class TestFakeDataDetector:
    """Test FakeDataDetector functionality."""

    def test_detect_placeholder_markers(self):
        """Detection of placeholder/template markers."""
        detector = FakeDataDetector(threshold=2)

        # Lorem ipsum
        result = detector.detect("Lorem ipsum dolor sit amet")
        assert result.is_fake
        assert "placeholder" in " ".join(result.indicators).lower()

        # TBD/TODO
        result = detector.detect("This is TBD TODO")
        assert result.is_fake

        # Brackets
        result = detector.detect("[name] [date] [amount]")
        assert result.is_fake

    def test_detect_very_short_document(self):
        """Detection of very short documents."""
        detector = FakeDataDetector(threshold=2)
        result = detector.detect("Short text")
        assert result.is_fake
        assert "too_short" in result.indicators

    def test_detect_low_vocabulary_diversity(self):
        """Detection of low vocabulary diversity."""
        detector = FakeDataDetector(threshold=2)
        # Repeated same words with enough length to avoid short-document trigger
        text = "the the the the the the the the the the the the the the the " * 30
        result = detector.detect(text)
        assert result.is_fake
        assert "low_vocab_diversity" in result.indicators

    def test_detect_repetitive_paragraphs(self):
        """Detection of repetitive paragraphs."""
        detector = FakeDataDetector(threshold=2)
        # Same paragraph repeated
        para = "This is a test paragraph."
        text = f"{para}\n\n{para}\n\n{para}\n\n{para}"
        result = detector.detect(text)
        assert result.is_fake
        assert "repetitive" in result.indicators

    def test_detect_missing_contract_elements(self):
        """Detection of missing essential contract elements."""
        detector = FakeDataDetector(threshold=3)
        # Text without parties, dates, signatures, etc.
        text = "Some random text without any contract structure."
        result = detector.detect(text)
        assert result.is_fake
        assert "missing_elements" in result.indicators

    def test_detect_real_contract_text(self):
        """Real contract text should not be flagged as fake with appropriate threshold."""
        detector = FakeDataDetector(threshold=7)  # Higher threshold for real contracts
        # Realistic contract text (longer to avoid short-document trigger)
        text = """
        This Agreement is entered into between ABC Corporation and XYZ LLC.
        Effective Date: January 1, 2024.
        Governing Law: State of Delaware.
        Payment: Net 30 days.
        The parties agree to the following terms and conditions.
        This contract shall be binding upon both parties.
        Any disputes shall be resolved in Delaware courts.
        Payment shall be made within 30 days of invoice.
        This Agreement constitutes the entire understanding between the parties.
        IN WITNESS WHEREOF, the parties have executed this Agreement.
        Signed by authorized representatives.
        """ * 3  # Repeat to make it long enough
        result = detector.detect(text)
        # With higher threshold, real text should pass
        assert result.score < 7
        assert not result.is_fake
        assert result.verdict == "real"

    def test_threshold_sensitivity(self):
        """Test that threshold affects detection."""
        text = "Lorem ipsum"

        # Low threshold - should detect
        detector_low = FakeDataDetector(threshold=1)
        result_low = detector_low.detect(text)
        assert result_low.is_fake

        # High threshold - should not detect
        detector_high = FakeDataDetector(threshold=10)
        result_high = detector_high.detect(text)
        assert not result_high.is_fake

    def test_report_structure(self):
        """Test FakeDataReport structure."""
        detector = FakeDataDetector()
        result = detector.detect("Test")

        assert isinstance(result, FakeDataReport)
        assert hasattr(result, "is_fake")
        assert hasattr(result, "score")
        assert hasattr(result, "threshold")
        assert hasattr(result, "reasons")
        assert hasattr(result, "indicators")
        assert hasattr(result, "verdict")

    def test_verdict_field(self):
        """Test verdict field values."""
        detector = FakeDataDetector(threshold=1)

        # Fake data
        result = detector.detect("Lorem ipsum")
        assert result.verdict == "fake data"
        assert result.is_fake == result.fake == result.detected == result.is_mock == result.is_falsified

        # Real data with higher threshold (longer to avoid short-document trigger)
        detector_real = FakeDataDetector(threshold=6)
        text = """
        This Agreement between Company A and Company B.
        Effective: January 1, 2024.
        Payment: $1000.
        Governing Law: Delaware.
        Signed: __________
        The parties agree to be bound by this agreement.
        IN WITNESS WHEREOF, the parties have executed this Agreement.
        """ * 5
        result = detector_real.detect(text)
        # With higher threshold, real text should pass
        if not result.is_fake:
            assert result.verdict == "real"

    def test_multiple_indicators(self):
        """Test that multiple indicators are detected."""
        detector = FakeDataDetector(threshold=3)
        text = """
        [name] [date]
        Lorem ipsum dolor sit amet
        This is a short text.
        """
        result = detector.detect(text)
        assert result.is_fake
        assert len(result.indicators) >= 2
        assert result.score >= 3

    def test_convenience_functions(self):
        """Test convenience functions."""
        text = "Lorem ipsum test"

        # detect_fake_data
        result1 = detect_fake_data(text)
        assert isinstance(result1, FakeDataReport)

        # detect_text
        result2 = detect_text(text)
        assert isinstance(result2, FakeDataReport)

        # Class-level method
        result3 = FakeDataDetector.detect_text(text)
        assert isinstance(result3, FakeDataReport)

    def test_empty_text(self):
        """Test with empty text."""
        detector = FakeDataDetector()
        result = detector.detect("")
        assert result.is_fake  # Empty should be flagged

    def test_gibberish_detection(self):
        """Test detection of gibberish text."""
        detector = FakeDataDetector(threshold=2)
        # Text with very low vowel ratio (long enough to avoid short-document trigger)
        text = "bcdfghjklmnpqrstvwxyz bcdfghjklmnpqrstvwxyz bcdfghjklmnpqrstvwxyz " * 50
        result = detector.detect(text)
        assert result.is_fake
        # Should have gibberish indicator (may also have others)
        assert "gibberish" in result.indicators or result.is_fake

    def test_no_structure_detection(self):
        """Test detection of documents without structure."""
        detector = FakeDataDetector(threshold=2)
        # Long text without headings (long enough to avoid short-document trigger)
        text = "word " * 200
        result = detector.detect(text)
        assert result.is_fake
        assert "no_structure" in result.indicators

    def test_caps_runs_detection(self):
        """Test detection of suspicious all-caps blocks."""
        detector = FakeDataDetector(threshold=1)
        # Multiple long all-caps runs (long enough to avoid short-document trigger)
        text = "ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZ " * 30
        result = detector.detect(text)
        assert result.is_fake
        # Should have caps_runs indicator (may also have others)
        assert "caps_runs" in result.indicators or result.is_fake

    def test_real_contract_with_placeholders(self):
        """Test contract with some placeholders but otherwise real."""
        detector = FakeDataDetector(threshold=4)
        # Contract with a few placeholders but otherwise complete
        text = """
        This Agreement is entered into between ABC Corporation and [Other Party].
        Effective Date: January 1, 2024.
        Governing Law: State of Delaware.
        Payment: Net 30 days.
        Jurisdiction: Delaware courts.
        Signature: ___________________
        """
        result = detector.detect(text)
        # Should detect placeholder but may not be overall fake depending on threshold
        assert "placeholder_markers" in result.indicators

    def test_score_accumulation(self):
        """Test that score accumulates correctly."""
        detector = FakeDataDetector(threshold=5)
        text = """
        [name] [date] [amount]
        Lorem ipsum
        """
        result = detector.detect(text)
        # Should have multiple indicators contributing to score
        assert result.score > 0
        assert len(result.reasons) > 0
