import os
import logging
from flask import Flask
from .config import Config
from .extensions import db, migrate, limiter


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
    logging.basicConfig(level=log_level)

    # Extensions
    db.init_app(app)
    migrate.init_app(app, db)
    limiter.init_app(app)

    # Blueprints
    from .routes.attendance import bp as attendance_bp
    from .routes.api import bp as api_bp

    app.register_blueprint(attendance_bp)
    app.register_blueprint(api_bp)

    # Health endpoint
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'tt-attendance'}

    with app.app_context():
        if app.config.get('AUTO_CREATE_DB', True):
            db.create_all()

    return app