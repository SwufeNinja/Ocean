from __future__ import annotations

import hashlib
import unittest
from typing import Any

from fastapi import HTTPException

from ocean.web import make_app


class WebAuthTest(unittest.TestCase):
    def test_auth_disabled_uses_local_password_mode(self) -> None:
        app = make_app(config={}, output_dir="outputs_test_web")
        login = _route_endpoint(app, "/api/auth/login", "POST")
        get_me = _route_endpoint(app, "/api/auth/me", "GET")

        with self.assertRaises(HTTPException) as missing_auth:
            get_me()
        self.assertEqual(missing_auth.exception.status_code, 401)

        with self.assertRaises(HTTPException) as bad_login:
            login({"username": "local", "password": "bad"})
        self.assertEqual(bad_login.exception.status_code, 401)

        login_response = login({"username": "local", "password": "local"})
        self.assertFalse(login_response["auth_enabled"])
        data = get_me(f"Bearer {login_response['access_token']}")
        self.assertFalse(data["auth_enabled"])
        self.assertEqual(data["user"]["account_id"], "local")

    def test_login_and_me_with_static_user(self) -> None:
        app = make_app(config=_auth_config(), output_dir="outputs_test_web")
        login = _route_endpoint(app, "/api/auth/login", "POST")
        get_me = _route_endpoint(app, "/api/auth/me", "GET")

        with self.assertRaises(HTTPException) as missing_auth:
            get_me()
        self.assertEqual(missing_auth.exception.status_code, 401)

        with self.assertRaises(HTTPException) as bad_login:
            login({"username": "admin", "password": "bad"})
        self.assertEqual(bad_login.exception.status_code, 401)

        login_response = login({"username": "admin", "password": "secret"})
        self.assertTrue(login_response["auth_enabled"])
        token = login_response["access_token"]
        me = get_me(f"Bearer {token}")
        self.assertEqual(me["user"]["username"], "admin")
        self.assertEqual(me["user"]["account_id"], "local")

    def test_user_cannot_access_other_account_conversation(self) -> None:
        app = make_app(config=_auth_config(), output_dir="outputs_test_web")
        login = _route_endpoint(app, "/api/auth/login", "POST")
        create_conversation = _route_endpoint(app, "/api/llm/conversations", "POST")
        get_conversation = _route_endpoint(app, "/api/llm/conversations/{conversation_id}", "GET")
        admin_token = login({"username": "admin", "password": "secret"})["access_token"]
        other_token = login({"username": "other", "password": "secret"})["access_token"]

        create_response = create_conversation({"title": "private"}, f"Bearer {admin_token}")
        conversation_id = create_response["conversation_id"]

        with self.assertRaises(HTTPException) as denied:
            get_conversation(conversation_id, f"Bearer {other_token}")
        self.assertEqual(denied.exception.status_code, 403)


def _auth_config() -> dict[str, object]:
    password_hash = "sha256:" + hashlib.sha256(b"secret").hexdigest()
    return {
        "auth": {
            "enabled": True,
            "jwt_secret": "test-secret",
            "users": [
                {
                    "username": "admin",
                    "password_hash": password_hash,
                    "account_id": "local",
                    "role": "admin",
                    "display_name": "Admin",
                },
                {
                    "username": "other",
                    "password_hash": password_hash,
                    "account_id": "other",
                    "role": "user",
                    "display_name": "Other",
                },
            ],
        }
    }


def _route_endpoint(app: Any, path: str, method: str) -> Any:
    for route in app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"Route not found: {method} {path}")


if __name__ == "__main__":
    unittest.main()
