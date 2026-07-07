from flask import Blueprint, current_app, flash, redirect, request, session, url_for

from tt_common.sso import get_auth_login_url, get_auth_logout_url, is_safe_url

from ..authz import normalize_auth_payload
from ..extensions import db
from ..jwt_utils import verify_sso_token
from ..models import User
from ..sso_replay import is_replayed_sso_token

bp = Blueprint('auth', __name__)


def _merge_claims(*sources):
    claims = {}
    for source in sources:
        if isinstance(source, dict):
            claims.update(source)
    return claims


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
        'role_permissions': claims.get('role_permissions') or {},
        'teams': claims.get('teams') or [],
        'member_roles': claims.get('member_roles') or [],
        'claims_json': claims,
    }


@bp.route('/login')
def login():
    return redirect(get_auth_login_url('tt-attendance', request.args.get('next')))


@bp.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    return redirect(get_auth_logout_url())


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

    if is_replayed_sso_token(payload):
        flash('SSO-Token wurde bereits verwendet. Bitte erneut anmelden.', 'danger')
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
    session['role_permissions'] = claims.get('role_permissions') or {}
    session['claims_json'] = user.claims_json

    next_page = request.args.get('next')
    if next_page and is_safe_url(next_page):
        return redirect(next_page)

    return redirect(url_for('attendance.index'))
