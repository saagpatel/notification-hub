"""Security boundary tests for the localhost operator review surface."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

import pytest
from httpx import ASGITransport, AsyncClient

import notification_hub.config as config_mod
import notification_hub.server as server_mod


def _review_package_report() -> dict[str, object]:
    return {
        "status": "ok",
        "review_package": {"path": None},
        "actions": [],
    }


def test_runtime_and_launchagent_bind_review_service_to_loopback() -> None:
    assert config_mod.HOST == "127.0.0.1"

    template = (
        Path(__file__).resolve().parents[1]
        / "ops"
        / "launchagents"
        / "com.saagar.notification-hub.plist"
    )
    root = ElementTree.parse(template).getroot()
    strings = [element.text for element in root.iter("string")]
    host_flag = strings.index("--host")
    assert strings[host_flag + 1] == "127.0.0.1"


@pytest.mark.anyio
async def test_review_page_issues_uncached_mutation_capability(
    client: AsyncClient,
) -> None:
    response = await client.get("/review")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    assert "fixture-review-token" in response.text
    assert "__REVIEW_MUTATION_TOKEN__" not in response.text
    assert "X-Notification-Hub-Review-Token" in response.text


@pytest.mark.anyio
async def test_review_mutation_requires_authentication_before_side_effect() -> None:
    transport = ASGITransport(app=server_mod.app)
    with patch(
        "notification_hub.server.run_personal_ops_action_export",
        return_value=_review_package_report(),
    ) as mutation:
        async with AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:9199",
        ) as client:
            response = await client.post(
                "/review/save-package",
                headers={"Origin": "http://127.0.0.1:9199"},
            )

    assert response.status_code == 401
    mutation.assert_not_called()


@pytest.mark.anyio
async def test_review_mutation_rejects_foreign_origin_before_side_effect(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.run_personal_ops_action_export",
        return_value=_review_package_report(),
    ) as mutation:
        response = await client.post(
            "/review/save-package",
            headers={"Origin": "https://attacker.example"},
        )

    assert response.status_code == 403
    mutation.assert_not_called()


@pytest.mark.anyio
async def test_review_mutation_accepts_same_origin_authenticated_request(
    client: AsyncClient,
) -> None:
    with patch(
        "notification_hub.server.run_personal_ops_action_export",
        return_value=_review_package_report(),
    ) as mutation:
        response = await client.post(
            "/review/save-package",
            headers={"Origin": "http://127.0.0.1:9199"},
        )

    assert response.status_code == 200
    mutation.assert_called_once()
