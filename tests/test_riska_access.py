"""Security regressions: revoked assignment and cross-unit requests cannot reach reports."""
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/test")

import pytest
from fastapi import HTTPException
from modules.siabumdes.adapters.api.v1.riska import ChatIn, DENIED, chat


@pytest.mark.asyncio
async def test_revoked_assignment_blocks_chat():
    user = SimpleNamespace(role="pengelola", unit_usaha_id="own", riska_enabled=False)
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(active=True)))
    with pytest.raises(HTTPException) as error:
        await chat(ChatIn(message="saldo kas"), user, session)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_cross_unit_and_macro_requests_denied():
    user = SimpleNamespace(role="pengelola", unit_usaha_id="own", riska_enabled=True)
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(active=True)))
    with pytest.raises(HTTPException) as error:
        await chat(ChatIn(message="saldo kas", unit_usaha_id="other"), user, session)
    assert error.value.status_code == 403
    result = await chat(ChatIn(message="transfer bagi hasil"), user, session)
    assert result["message"] == DENIED
    assert session.get.await_count == 2
