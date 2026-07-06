import os
import logging
import requests
from flask import Flask, session
from sqlalchemy import inspect, text
from .config import Config
from .extensions import db, migrate, limiter
from .models import User


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    if not app.config.get('SECRET_KEY'):
        if app.debug or app.testing:
            app.logger.warning('SECRET_KEY is not set; running in insecure development mode.')
        else:
            raise RuntimeError('SECRET_KEY must be set in production.')

    # Logging
    log_level = getattr(logging, app.config.get('LOG_LEVEL', 'INFO').upper(), logging.INFO)
    formatter = logging.Formatter('[%(asctime)s +0000] [%(process)d] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Extensions
    db.init_app(app)
    migrate.init_app(app, db)
    limiter.init_app(app)

    # Blueprints
    from .routes.auth import bp as auth_bp
    from .routes.attendance import bp as attendance_bp
    from .routes.api import bp as api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(attendance_bp)
    app.register_blueprint(api_bp)

    # Health endpoint
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'tt-attendance'}

    @app.context_processor
    def inject_template_globals():
        auth_base_url = app.config.get('AUTH_BASE_URL', 'http://localhost:8085').rstrip('/')
        return {
            'auth_base_url': auth_base_url,
            'auth_dashboard_url': f'{auth_base_url}/',
        }

    @app.context_processor
    def inject_pending_messages_count():
        user_id = session.get('user_id')
        auth_user_id = None
        if user_id:
            user = db.session.get(User, user_id)
            auth_user_id = user.auth_user_id if user else None
        return {'pending_messages_count': _fetch_pending_messages_count(app, auth_user_id)}

    with app.app_context():
        if app.config.get('AUTO_CREATE_DB', True):
            db.create_all()
            _ensure_attendance_columns()

    return app


def _fetch_pending_messages_count(app, auth_user_id):
    if not auth_user_id:
        return 0

    members_base = app.config.get('TT_MEMBERS_INTERNAL_URL', 'http://tt-members:5000').rstrip('/')
    secret = app.config.get('INTERNAL_API_SECRET') or app.config.get('SSO_SHARED_SECRET') or app.config.get('SECRET_KEY')
    if not secret:
        return 0

    try:
        response = requests.get(
            f'{members_base}/api/internal/messages/count',
            params={'auth_user_id': auth_user_id},
            headers={'X-TT-Internal-Secret': secret},
            timeout=2,
        )
        if response.status_code != 200:
            return 0
        payload = response.json() or {}
        return max(0, int(payload.get('pending_messages_count') or 0))
    except Exception:
        return 0


def _ensure_attendance_columns():
    inspector = inspect(db.engine)
    if 'attendances' not in inspector.get_table_names():
        return

    columns = {column['name'] for column in inspector.get_columns('attendances')}
    statements = []
    if 'presence_status' not in columns:
        statements.append('ALTER TABLE attendances ADD COLUMN presence_status VARCHAR(16)')
    if 'presence_marked_at' not in columns:
        statements.append('ALTER TABLE attendances ADD COLUMN presence_marked_at TIMESTAMP')

    for statement in statements:
        db.session.execute(text(statement))
    if statements:
        db.session.commit()
