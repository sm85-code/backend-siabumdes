from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError

from adapters.external import gdrive_adapter as drive


@pytest.mark.parametrize('status', [404, 403, 503])
def test_delete_is_idempotent_only_for_actual_not_found(monkeypatch, status):
    # Text containing 404 must never make a permission/provider error succeed.
    error = HttpError(SimpleNamespace(status=status, reason='error'), b'{"error":{"message":"file404 unavailable"}}')
    service = Mock()
    service.files.return_value.delete.return_value.execute.side_effect = error
    monkeypatch.setattr(drive, '_drive_service', lambda: service)
    if status == 404:
        drive._delete_sync('file404')
    else:
        with pytest.raises(HttpError):
            drive._delete_sync('file404')


@pytest.mark.parametrize('status', [404, 403, 503])
def test_verification_does_not_discard_proofs_on_drive_failure(monkeypatch, status):
    error = HttpError(SimpleNamespace(status=status, reason='error'), b'{}')
    service = Mock()
    service.files.return_value.get.return_value.execute.side_effect = error
    monkeypatch.setattr(drive, '_drive_service', lambda: service)
    if status == 404:
        assert drive._exists_sync('file') is False
    else:
        with pytest.raises(HttpError):
            drive._exists_sync('file')
