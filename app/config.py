import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY')
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get('SQLALCHEMY_DATABASE_URI')
        or os.environ.get('DATABASE_URL')
        or 'postgresql+psycopg://tt_attendance:tt_attendance_password@tt-postgres-attendance:5432/tt_attendance'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
    AUTO_CREATE_DB = os.environ.get('AUTO_CREATE_DB', 'true').lower() == 'true'
    SESSION_COOKIE_NAME = os.environ.get('SESSION_COOKIE_NAME', 'attendance_session')
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'true').lower() == 'true'
    SESSION_COOKIE_HTTPONLY = os.environ.get('SESSION_COOKIE_HTTPONLY', 'true').lower() == 'true'
    SESSION_COOKIE_SAMESITE = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')

    # SSO / Auth
    AUTH_BASE_URL = os.environ.get('AUTH_BASE_URL', 'http://localhost:8085')
    TT_AUTH_INTERNAL_URL = os.environ.get('TT_AUTH_INTERNAL_URL', 'http://tt-auth:5000')
    TT_AGENDA_INTERNAL_URL = os.environ.get('TT_AGENDA_INTERNAL_URL', 'http://tt-agenda:5000')
    TT_INFRA_INTERNAL_URL = os.environ.get('TT_INFRA_INTERNAL_URL', 'http://tt-infra:5000')
    SSO_SHARED_SECRET = os.environ.get('SSO_SHARED_SECRET') or SECRET_KEY
    SSO_EXPECTED_AUDIENCE = os.environ.get('SSO_EXPECTED_AUDIENCE', 'tt-attendance')
    SSO_TOKEN_EXPIRY_SECONDS = int(os.environ.get('SSO_TOKEN_EXPIRY_SECONDS', 60))
    INTERNAL_API_SECRET = os.environ.get('INTERNAL_API_SECRET') or 'tt-internal-dev-secret-change-me'
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')
