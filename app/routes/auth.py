from urllib.parse import urlencode, urljoin, urlparse

from flask import Blueprint, current_app, flash, redirect, request, session, url_for

from ..authz import normalize_auth_payload
from ..extensions import db
from ..jwt_utils import verify_sso_token
from ..models import User

bp = Blueprint('auth', __name__)


def _merge_claims(*sources):
    claims = {}
    for source in sources:
        if isinstance(source, dict):
            claims.update(source)
    return claims


def get_auth_login_url(next_page=None):
    auth_base_url = current_app.config.get('AUTH_BASE_URL', 'http://localhost:8085').rstrip('/')
    query = {'next_service': 'tt-attendance'}
    if next_page:
        query['next'] = next_page
    return f'{auth_base_url}/?{urlencode(query)}'


def get_current_user():
    """Return user dict from session, or None if not authenticated."""
    user_id = session.get('user_id')
    if not user_id:
        return None
    user = db.session.get(User, user_id)
    if not user:
        session.clear()
        return None
    session_claims = session.get('claims_json')
    claims = _merge_claims(user.claims_json or {}, session_claims or {})
    return {
        'id': user.auth_user_id,
        'username': user.username,
        'role': user.service_role,
        'display_name': user.display_name or user.username,
        'memberships': claims.get('memberships') or [],
        'permissions': claims.get('permissions') or [],
        'teams': claims.get('teams') or [],
        'member_roles': claims.get('member_roles') or [],
        'claims_json': claims,
    }


@bp.route('/login')
def login():
    return redirect(get_auth_login_url(request.args.get('next')))


@bp.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    auth_base_url = current_app.config.get('AUTH_BASE_URL', 'http://localhost:8085').rstrip('/')
    return redirect(f'{auth_base_url}/logout')


@bp.route('/auth/sso')
def sso_login():
    token = (request.args.get('token') or '').strip()
    if not token:
        flash('SSO-Token fehlt.', 'danger')
        return redirect(url_for('auth.login'))

    payload = verify_sso_token(token)
    if not payload:
        flash('Ungültiger SSO-Token.', 'danger')
        return redirect(url_for('auth.login'))

    auth = normalize_auth_payload(payload)
    claims = auth['claims']
    auth_user_id = int(claims['sub'])
    username = (claims.get('username') or '').strip()
    if not username:
        flash('SSO-Token enthält keinen Benutzernamen.', 'danger')
        return redirect(url_for('auth.login'))

    user = User.query.filter_by(auth_user_id=auth_user_id).first()
    if not user:
        user = User.query.filter_by(username=username).first()
        if user:
            user.auth_user_id = auth_user_id
        else:
            user = User()
            user.auth_user_id = auth_user_id
            user.username = username
            db.session.add(user)

    user.sync_from_sso_claims(payload)
    db.session.commit()

    session['user_id'] = user.id
    session['username'] = user.username
    session['display_name'] = user.display_name or user.username
    session['service_role'] = user.service_role
    session['platform_role'] = user.platform_role
    session['claims_json'] = user.claims_json

    next_page = request.args.get('next')
    if next_page:
        ref_url = urlparse(request.host_url)
        test_url = urlparse(urljoin(request.host_url, next_page))
        if test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc:
            return redirect(next_page)

    return redirect(url_for('attendance.index'))
