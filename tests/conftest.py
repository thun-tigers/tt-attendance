import sys
from pathlib import Path

import pytest

TEST_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

from app import create_app
from app.extensions import db


class TestConfig:
    TESTING = True
    SECRET_KEY = 'test-secret-key'
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    AUTO_CREATE_DB = True
    LOG_LEVEL = 'DEBUG'
    AUTH_BASE_URL = 'http://localhost:8085'
    TT_AUTH_INTERNAL_URL = 'http://tt-auth:5000'
    TT_MEMBERS_INTERNAL_URL = 'http://tt-members:5000'
    TT_AGENDA_INTERNAL_URL = 'http://tt-agenda:5000'
    TT_INFRA_INTERNAL_URL = 'http://tt-infra:5000'
    SSO_SHARED_SECRET = 'test-sso-secret'
    SSO_EXPECTED_AUDIENCE = 'tt-attendance'
    SSO_TOKEN_EXPIRY_SECONDS = 60
    INTERNAL_API_SECRET = 'test-internal-secret'
    RATELIMIT_STORAGE_URI = 'memory://'
    SESSION_COOKIE_SECURE = False


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(TestConfig, 'SQLALCHEMY_DATABASE_URI', f'sqlite:///{tmp_path / "test.db"}')
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()
