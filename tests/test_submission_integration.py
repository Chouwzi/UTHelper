"""Integration test: upload → save → submit_for_grading flow.

End-to-end test with all API calls mocked — verifies the complete
submission pipeline works correctly when orchestrated together.
"""
import pytest
import sys
import os
from unittest.mock import MagicMock, patch, call

# Setup path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.ws_functions import (
    upload_file_to_draft,
    save_and_submit,
    check_needs_submit,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_api():
    """A fresh MagicMock for call_api."""
    return MagicMock()


# ===========================================================================
# Full submission flow integration
# ===========================================================================

class TestSubmissionFlowIntegration:
    """Integration: upload → save → submit_for_grading with mocked APIs."""

    def test_full_flow_with_drafts_enabled(self, mock_api):
        """Complete flow: upload, check drafts, save+submit."""
        # Step 1: upload returns draft itemid
        # Step 2: get_assignments returns assignment with submissiondrafts=1
        # Step 3: save_submission returns [] (success)
        # Step 4: submit_for_grading returns [] (success)
        mock_api.side_effect = [
            # upload_file_to_draft
            {'itemid': 555},
            # check_needs_submit → get_assignments
            {'courses': [{'assignments': [{'id': 42, 'submissiondrafts': 1}]}]},
            # save_assignment_submission
            [],
            # submit_for_grading
            [],
        ]

        # Step 1: Upload
        draft_id = upload_file_to_draft(
            mock_api,
            filename='baitap.pdf',
            file_content_b64='dGVzdA==',
            user_id=10,
        )
        assert draft_id == 555

        # Step 2: Check if submit needed
        needs_submit = check_needs_submit(mock_api, assign_id=42, course_id=1)
        assert needs_submit is True

        # Step 3+4: Save and submit
        ok = save_and_submit(mock_api, assign_id=42, draft_itemid=draft_id, needs_submit=needs_submit)
        assert ok is True

        # Verify all 4 API calls were made
        assert mock_api.call_count == 4

    def test_full_flow_without_drafts(self, mock_api):
        """When submissiondrafts=0, submit_for_grading is skipped."""
        mock_api.side_effect = [
            # upload
            {'itemid': 777},
            # get_assignments → call_api returns {'courses': [...]}
            {'courses': [{'assignments': [{'id': 42, 'submissiondrafts': 0}]}]},
            # save_submission only (no submit)
            [],
        ]

        draft_id = upload_file_to_draft(
            mock_api, filename='report.docx',
            file_content_b64='ZG9j', user_id=10,
        )
        assert draft_id == 777

        needs_submit = check_needs_submit(mock_api, assign_id=42, course_id=1)
        assert needs_submit is False

        ok = save_and_submit(mock_api, assign_id=42, draft_itemid=draft_id, needs_submit=needs_submit)
        assert ok is True

        # Only 3 calls: upload + get_assignments + save (no submit)
        assert mock_api.call_count == 3

    def test_upload_failure_aborts_flow(self, mock_api):
        """Upload returns None → flow should stop."""
        mock_api.side_effect = ConnectionError("offline")

        draft_id = upload_file_to_draft(
            mock_api, filename='fail.pdf',
            file_content_b64='data', user_id=10,
        )
        assert draft_id is None

        # Flow stops — save_and_submit should NOT be called with None
        assert mock_api.call_count == 1

    def test_save_failure_aborts_submit(self, mock_api):
        """Upload OK, but save fails → submit never called."""
        mock_api.side_effect = [
            # upload
            {'itemid': 888},
            # get_assignments
            {'courses': [{'assignments': [{'id': 42, 'submissiondrafts': 1}]}]},
            # save_submission fails (returns None)
            None,
        ]

        draft_id = upload_file_to_draft(
            mock_api, filename='hw.pdf',
            file_content_b64='aHc=', user_id=10,
        )
        assert draft_id == 888

        needs_submit = check_needs_submit(mock_api, assign_id=42, course_id=1)

        ok = save_and_submit(mock_api, assign_id=42, draft_itemid=draft_id, needs_submit=needs_submit)
        assert ok is False

        # 3 calls total: upload + get_assignments + save (submit skipped)
        assert mock_api.call_count == 3

    def test_submit_grading_failure(self, mock_api):
        """Upload+save OK, but submit_for_grading fails with warnings."""
        mock_api.side_effect = [
            # upload
            {'itemid': 111},
            # get_assignments
            {'courses': [{'assignments': [{'id': 42, 'submissiondrafts': 1}]}]},
            # save OK
            [],
            # submit_for_grading fails (warnings)
            {'warnings': [{'message': 'Already submitted'}]},
        ]

        draft_id = upload_file_to_draft(
            mock_api, filename='final.pdf',
            file_content_b64='ZmluYWw=', user_id=10,
        )

        needs_submit = check_needs_submit(mock_api, assign_id=42, course_id=1)

        ok = save_and_submit(mock_api, assign_id=42, draft_itemid=draft_id, needs_submit=needs_submit)
        assert ok is False
        assert mock_api.call_count == 4


class TestMultiFileUploadIntegration:
    """Upload multiple files to the same draft area."""

    def test_reuse_draft_itemid(self, mock_api):
        """Second upload reuses itemid from first upload."""
        mock_api.side_effect = [
            {'itemid': 500},  # first file
            {'itemid': 500},  # second file, same draft area
            [],               # save
        ]

        # Upload first file → get draft area
        draft1 = upload_file_to_draft(
            mock_api, filename='file1.pdf',
            file_content_b64='ZjE=', user_id=10, itemid=0,
        )
        assert draft1 == 500

        # Upload second file → reuse draft area
        draft2 = upload_file_to_draft(
            mock_api, filename='file2.pdf',
            file_content_b64='ZjI=', user_id=10, itemid=500,
        )
        assert draft2 == 500

        # Verify second call used itemid=500
        calls = mock_api.call_args_list
        # First upload: itemid=0
        assert calls[0][1]['itemid'] == 0
        # Second upload: itemid=500 (reuse)
        assert calls[1][1]['itemid'] == 500


class TestUploadEdgeCases:
    """Edge cases for upload_file_to_draft."""

    def test_upload_returns_exception_in_result(self, mock_api):
        """API returns dict with 'exception' → None."""
        mock_api.return_value = {
            'exception': 'webservice_access_exception',
            'message': 'Access denied',
        }
        result = upload_file_to_draft(
            mock_api, filename='x.pdf',
            file_content_b64='eA==', user_id=10,
        )
        assert result is None

    def test_upload_returns_unexpected_type(self, mock_api):
        """API returns non-dict (e.g. string) → None."""
        mock_api.return_value = "unexpected"
        result = upload_file_to_draft(
            mock_api, filename='y.pdf',
            file_content_b64='eQ==', user_id=10,
        )
        assert result is None

    @pytest.mark.parametrize("response", [None, [], 42])
    def test_upload_non_dict_responses(self, mock_api, response):
        """Various non-dict API responses → None."""
        mock_api.return_value = response
        result = upload_file_to_draft(
            mock_api, filename='z.pdf',
            file_content_b64='eg==', user_id=10,
        )
        assert result is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
