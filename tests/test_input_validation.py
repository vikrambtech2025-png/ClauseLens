"""Input validation and edge case tests.

These tests verify that the application validates and sanitizes user inputs.
Pydantic already handles most validation; these tests verify edge cases.
"""

from __future__ import annotations

import pytest

from models.schemas import PolicyConfig


class TestPolicyValidation:
    """Test validation of policy configuration values."""

    def test_invalid_negative_payment_days_rejected(self):
        """Negative payment days should be rejected by Pydantic."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            PolicyConfig(
                max_payment_days=-30,
                allow_auto_renewal=False,
                min_renewal_notice_days=30,
                require_liability_cap=True,
                approved_jurisdictions=["Delaware"],
                allow_exclusive_jurisdiction=True,
            )

    def test_invalid_large_payment_days_rejected(self):
        """Values > 365 should be rejected by Pydantic."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            PolicyConfig(
                max_payment_days=999999,
                allow_auto_renewal=False,
                min_renewal_notice_days=30,
                require_liability_cap=True,
                approved_jurisdictions=["Delaware"],
                allow_exclusive_jurisdiction=True,
            )

    def test_invalid_negative_renewal_notice_rejected(self):
        """Negative renewal notice should be rejected by Pydantic."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            PolicyConfig(
                max_payment_days=30,
                allow_auto_renewal=False,
                min_renewal_notice_days=-30,
                require_liability_cap=True,
                approved_jurisdictions=["Delaware"],
                allow_exclusive_jurisdiction=True,
            )

    def test_invalid_empty_jurisdiction_list_rejected(self):
        """Empty jurisdiction list should be rejected by Pydantic."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            PolicyConfig(
                max_payment_days=30,
                allow_auto_renewal=False,
                min_renewal_notice_days=30,
                require_liability_cap=True,
                approved_jurisdictions=[],
                allow_exclusive_jurisdiction=True,
            )

    def test_jurisdiction_with_special_chars_accepted(self):
        """Jurisdictions with special characters should be accepted."""
        policy = PolicyConfig(
            max_payment_days=30,
            allow_auto_renewal=False,
            min_renewal_notice_days=30,
            require_liability_cap=True,
            approved_jurisdictions=["New York", "California", "Québec", "Zürich"],
            allow_exclusive_jurisdiction=True,
        )
        assert policy is not None

    def test_very_long_jurisdiction_name_accepted(self):
        """Very long jurisdiction names should be accepted."""
        policy = PolicyConfig(
            max_payment_days=30,
            allow_auto_renewal=False,
            min_renewal_notice_days=30,
            require_liability_cap=True,
            approved_jurisdictions=["A" * 1000],
            allow_exclusive_jurisdiction=True,
        )
        assert policy is not None

    def test_float_as_int_parameter_rejected(self):
        """Float values for int parameters should be rejected by Pydantic."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            PolicyConfig(
                max_payment_days=30.5,  # Float instead of int
                allow_auto_renewal=False,
                min_renewal_notice_days=30,
                require_liability_cap=True,
                approved_jurisdictions=["Delaware"],
                allow_exclusive_jurisdiction=True,
            )

    def test_none_as_boolean_rejected(self):
        """None values for boolean parameters should be rejected by Pydantic."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            PolicyConfig(
                max_payment_days=30,
                allow_auto_renewal=None,  # None instead of bool
                min_renewal_notice_days=30,
                require_liability_cap=True,
                approved_jurisdictions=["Delaware"],
                allow_exclusive_jurisdiction=True,
            )

    def test_none_as_list_rejected(self):
        """None values for list parameters should be rejected by Pydantic."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            PolicyConfig(
                max_payment_days=30,
                allow_auto_renewal=False,
                min_renewal_notice_days=30,
                require_liability_cap=True,
                approved_jurisdictions=None,  # None instead of list
                allow_exclusive_jurisdiction=True,
            )

    def test_duplicate_list_items_accepted(self):
        """Duplicate items in lists should be accepted."""
        policy = PolicyConfig(
            max_payment_days=30,
            allow_auto_renewal=False,
            min_renewal_notice_days=30,
            require_liability_cap=True,
            approved_jurisdictions=["Delaware", "Delaware", "Delaware"],
            allow_exclusive_jurisdiction=True,
        )
        assert policy is not None

    def test_mixed_types_in_list_rejected(self):
        """Mixed types in lists should be rejected by Pydantic."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            PolicyConfig(
                max_payment_days=30,
                allow_auto_renewal=False,
                min_renewal_notice_days=30,
                require_liability_cap=True,
                approved_jurisdictions=["Delaware", 123, None],
                allow_exclusive_jurisdiction=True,
            )


class TestMaximumValues:
    """Test handling of maximum configuration values."""

    def test_max_upload_size(self):
        """Very large max upload size should be handled."""
        import os
        os.environ["CLAUSELENS_MAX_UPLOAD_MB"] = "10000"
        # Should not crash
        from core.document_loader import DocumentLoader
        try:
            loader = DocumentLoader()
            assert loader is not None
        finally:
            if "CLAUSELENS_MAX_UPLOAD_MB" in os.environ:
                del os.environ["CLAUSELENS_MAX_UPLOAD_MB"]

    def test_max_pdf_pages(self):
        """Very large max PDF pages should be handled."""
        import os
        os.environ["CLAUSELENS_MAX_PDF_PAGES"] = "10000"
        # Should not crash
        from core.document_loader import DocumentLoader
        try:
            loader = DocumentLoader()
            assert loader is not None
        finally:
            if "CLAUSELENS_MAX_PDF_PAGES" in os.environ:
                del os.environ["CLAUSELENS_MAX_PDF_PAGES"]
