"""File upload security tests.

These tests verify that the application handles malicious or malformed file uploads
safely, including path traversal, oversized files, and invalid file types.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from core.document_loader import DocumentLoader, DocumentError


class TestPathTraversal:
    """Test protection against path traversal attacks."""

    def test_reject_absolute_path(self):
        loader = DocumentLoader()
        with pytest.raises(DocumentError):
            loader.load("C:/Windows/System32/config")

    def test_reject_parent_directory_traversal(self):
        loader = DocumentLoader()
        with pytest.raises(DocumentError):
            loader.load("../../../etc/passwd")

    def test_reject_mixed_traversal(self):
        loader = DocumentLoader()
        with pytest.raises(DocumentError):
            loader.load("safe/../../etc/passwd")


class TestFileTypeValidation:
    """Test file type validation and signature checking."""

    def test_reject_unsupported_extension(self):
        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            f.write(b"fake content")
            temp_path = f.name

        try:
            with pytest.raises(DocumentError):
                loader.load(temp_path)
        finally:
            os.unlink(temp_path)

    def test_reject_mismatched_extension_and_signature(self):
        """Reject files with wrong extension for their actual content."""
        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            # Write EXE signature instead of PDF
            f.write(b"MZ")
            temp_path = f.name

        try:
            with pytest.raises(DocumentError):
                loader.load(temp_path)
        finally:
            os.unlink(temp_path)

    def test_reject_empty_file(self):
        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            temp_path = f.name

        try:
            with pytest.raises(DocumentError):
                loader.load(temp_path)
        finally:
            os.unlink(temp_path)


class TestFileSizeLimits:
    """Test enforcement of file size limits."""

    def test_reject_oversized_pdf(self):
        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            # Write a large file (PDF signature + lots of data)
            f.write(b"%PDF-1.4\n" + b"x" * (11 * 1024 * 1024))  # 11 MB
            temp_path = f.name

        try:
            with pytest.raises(DocumentError):
                loader.load(temp_path)
        finally:
            os.unlink(temp_path)

    def test_reject_oversized_docx(self):
        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            # Write a large file (ZIP signature + lots of data)
            f.write(b"PK\x03\x04" + b"x" * (11 * 1024 * 1024))  # 11 MB
            temp_path = f.name

        try:
            with pytest.raises(DocumentError):
                loader.load(temp_path)
        finally:
            os.unlink(temp_path)


class TestMalformedFileContent:
    """Test handling of malformed file content."""

    def test_corrupted_pdf_signature(self):
        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            # Invalid PDF signature
            f.write(b"NOT-A-PDF")
            temp_path = f.name

        try:
            with pytest.raises(DocumentError):
                loader.load(temp_path)
        finally:
            os.unlink(temp_path)

    def test_corrupted_docx_signature(self):
        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            # Invalid DOCX signature
            f.write(b"NOT-A-DOCX")
            temp_path = f.name

        try:
            with pytest.raises(DocumentError):
                loader.load(temp_path)
        finally:
            os.unlink(temp_path)

    def test_truncated_pdf(self):
        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            # Valid PDF header but incomplete
            f.write(b"%PDF-1.4\n")
            temp_path = f.name

        try:
            with pytest.raises(DocumentError):
                loader.load(temp_path)
        finally:
            os.unlink(temp_path)


class TestSpecialCharactersInFilename:
    """Test handling of special characters in filenames."""

    def test_filename_with_spaces(self):
        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".txt", prefix="test file ", delete=False) as f:
            f.write(b"Test content")
            temp_path = f.name

        try:
            result = loader.load(temp_path)
            assert result.filename is not None
        finally:
            os.unlink(temp_path)

    def test_filename_with_unicode(self):
        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".txt", prefix="тест_", delete=False) as f:
            f.write(b"Test content")
            temp_path = f.name

        try:
            result = loader.load(temp_path)
            assert result.filename is not None
        finally:
            os.unlink(temp_path)


class TestBinaryContentInTextFiles:
    """Test handling of binary content in text files."""

    def test_null_bytes_in_text(self):
        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"Normal text\x00Null byte\x00More text")
            temp_path = f.name

        try:
            result = loader.load(temp_path)
            # Should handle gracefully, not crash
            assert result is not None
        finally:
            os.unlink(temp_path)

    def test_control_characters(self):
        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"Text\x01\x02\x03with\x07control\x08chars")
            temp_path = f.name

        try:
            result = loader.load(temp_path)
            assert result is not None
        finally:
            os.unlink(temp_path)


class TestConcurrentAccess:
    """Test that file handles are properly closed after loading."""

    def test_file_handle_closed_after_load(self):
        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
            f.write("Test content")
            temp_path = f.name

        try:
            result = loader.load(temp_path)
            # Should be able to delete the file after loading
            os.unlink(temp_path)
            assert True
        except (DocumentError, PermissionError):
            try:
                os.unlink(temp_path)
            except:
                pass
            raise


class TestNonExistentFile:
    """Test handling of non-existent files."""

    def test_non_existent_file(self):
        loader = DocumentLoader()
        with pytest.raises(DocumentError):
            loader.load("/nonexistent/path/to/file.pdf")

    def test_directory_instead_of_file(self):
        loader = DocumentLoader()
        with tempfile.TemporaryDirectory() as temp_dir:
            with pytest.raises(DocumentError):
                loader.load(temp_dir)


class TestSymbolicLinks:
    """Test handling of symbolic links (if supported)."""

    def test_symlink_to_safe_file(self):
        """Should follow symlink if it points to a valid file."""
        if os.name == "nt":  # Windows
            pytest.skip("Symlinks require admin on Windows")

        loader = DocumentLoader()
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"Test content")
            real_path = f.name

        with tempfile.TemporaryDirectory() as temp_dir:
            link_path = os.path.join(temp_dir, "linked.txt")
            os.symlink(real_path, link_path)

            try:
                result = loader.load(link_path)
                assert result is not None
            finally:
                os.unlink(real_path)
