# -*- coding: utf-8 -*-
"""鉴权流程测试"""
import os

from fastapi.testclient import TestClient

from tests.web.conftest import TEST_TOKEN


def test_stats_requires_auth(anon_client: TestClient):
    anon_client.cookies.clear()
    resp = anon_client.get("/api/stats")
    assert resp.status_code == 401


def test_login_with_wrong_token(anon_client: TestClient):
    anon_client.cookies.clear()
    resp = anon_client.post("/api/login", json={"token": "wrong-token"})
    assert resp.status_code == 401


def test_login_sets_httponly_cookie(anon_client: TestClient):
    anon_client.cookies.clear()
    resp = anon_client.post("/api/login", json={"token": TEST_TOKEN})
    assert resp.status_code == 200
    set_cookie = resp.headers.get("set-cookie", "")
    assert "web_admin_session=" in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()


def test_index_page_is_public(web_client: TestClient):
    resp = web_client.get("/")
    assert resp.status_code == 200
    assert "检索观测台" in resp.text


def test_without_token_config_returns_503(monkeypatch):
    # 置空而非 pop：load_dotenv(override=False) 不会覆盖已存在的环境变量，
    # 模拟「.env 未配置 token」的场景
    monkeypatch.setenv("WEB_ADMIN_TOKEN", "")
    from src.web import deps
    from src.web.app import create_app

    client = TestClient(create_app())
    with client:
        resp = client.get("/api/stats")
        assert resp.status_code == 503
        resp = client.post("/api/login", json={"token": "any"})
        assert resp.status_code == 503
    # 立即撤销 monkeypatch 并恢复 deps 的全局 token 状态，避免污染后续测试
    monkeypatch.undo()
    deps.load_token()
    assert deps._token_env == TEST_TOKEN
