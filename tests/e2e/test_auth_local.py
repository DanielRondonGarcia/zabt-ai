# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 Afeef Janjua
"""E2E coverage for the first-party local email/password flow.

Prerequisites:
  - The local web and API services must be running.
  - Run with the repository's Playwright test configuration.
"""

import os
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:3000")


@pytest.fixture(scope="module")
def browser_context_args(browser_context_args):
    """Ignore HTTPS errors for local development certificates."""

    return {**browser_context_args, "ignore_https_errors": True}


class TestLocalAuthFlow:
    def test_unauthenticated_redirect(self, page: Page):
        page.goto(BASE_URL)
        expect(page).to_have_url(f"{BASE_URL}/login", timeout=5000)

    def test_register_with_local_credentials(self, page: Page):
        email = f"e2e-local-{uuid4().hex}@example.com"
        page.goto(f"{BASE_URL}/register")

        page.get_by_label("Full name").fill("Local E2E User")
        page.get_by_label("Email address").fill(email)
        page.get_by_label("Password").fill("correct horse battery staple")
        page.get_by_role("button", name="Sign up").click()

        expect(page).not_to_have_url(f"{BASE_URL}/register", timeout=8000)

    def test_backend_profile_rejects_unauthenticated(self, page: Page):
        response = page.request.get(
            f"{os.getenv('E2E_API_URL', 'http://localhost:8000')}/api/v1/users/me"
        )
        assert response.status == 401, (
            f"Expected 401 for unauthenticated profile request, got {response.status}"
        )
