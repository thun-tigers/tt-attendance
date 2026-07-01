import jwt
import requests
from datetime import datetime, timedelta, timezone
from flask import current_app


def generate_jwt(user, hours=None, memberships=None, permissions=None):
    """Create the local session JWT used by tt-attendance."""
    now = datetime.now(timezone.utc)
    ttl_hours = hours if hours is not None else current_app.config.get('JWT_EXPIRY_HOURS', 8)
    payload_memberships = memberships if memberships is not None else user.get('memberships', [])
    payload_permissions = permissions if permissions is not None else user.get('permissions', [])
    payload = {
        'user': {
            'id': user['id'],
            'username': user['username'],
            'role': user.get('role', 'user'),
            'display_name': user.get('display_name') or user['username'],
            'memberships': payload_memberships,
            'permissions': payload_permissions,
        },
        'iat': now,
        'exp': now + timedelta(hours=ttl_hours),
    }
    return jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm='HS256')


def set_jwt_cookie(response, token):
    """Persist the local session JWT in the tt_jwt cookie."""
    response.set_cookie(
        'tt_jwt',
        token,
        httponly=True,
        secure=current_app.config.get('JWT_COOKIE_SECURE', False),
        samesite='Lax',
        max_age=current_app.config.get('JWT_EXPIRY_HOURS', 8) * 3600,
        path='/',
    )
    return response


def clear_jwt_cookie(response):
    """Clear the local session JWT cookie."""
    response.set_cookie('tt_jwt', '', expires=0, path='/')
    return response


def create_sso_token():
    """Create a short-lived SSO token for service-to-service auth."""
    now = datetime.now(timezone.utc)
    expiry_seconds = current_app.config.get('SSO_TOKEN_EXPIRY_SECONDS', 60)
    payload = {
        'iss': current_app.config.get('SSO_EXPECTED_AUDIENCE', 'tt-attendance'),
        'aud': current_app.config.get('SSO_EXPECTED_AUDIENCE', 'tt-attendance'),
        'exp': now + timedelta(seconds=expiry_seconds),
        'iat': now,
    }
    return jwt.encode(payload, current_app.config['SSO_SHARED_SECRET'], algorithm='HS256')


def verify_sso_token(token):
    """Verify an incoming SSO token from another service."""
    try:
        payload = jwt.decode(
            token,
            current_app.config['SSO_SHARED_SECRET'],
            audience=current_app.config['SSO_EXPECTED_AUDIENCE'],
            algorithms=['HS256'],
        )
        return payload
    except (jwt.ExpiredSignatureError, jwt.InvalidAudienceError, jwt.InvalidTokenError):
        return None


def fetch_user_from_auth(user_id):
    """Fetch user info from tt-auth via internal API."""
    auth_url = current_app.config.get('TT_AUTH_INTERNAL_URL', 'http://tt-auth:5000')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    try:
        resp = requests.get(
            f'{auth_url}/api/users/{user_id}',
            headers={'X-TT-Internal-Secret': secret},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json()
    except requests.RequestException:
        pass
    return None


def fetch_trainings_from_agenda():
    """Fetch upcoming trainings from tt-agenda."""
    return fetch_trainings_from_agenda_for_teams()


def fetch_trainings_from_agenda_for_teams(team_codes=None):
    """Fetch upcoming trainings from tt-agenda, optionally filtered by teams."""
    agenda_url = current_app.config.get('TT_AGENDA_INTERNAL_URL', 'http://tt-agenda:5000')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    params = {}
    if team_codes:
        normalized = sorted({
            (code or '').strip().upper()
            for code in team_codes
            if (code or '').strip()
        })
        if normalized:
            params['teams'] = ','.join(normalized)
    try:
        resp = requests.get(
            f'{agenda_url}/api/trainings',
            headers={'X-TT-Internal-Secret': secret, 'Authorization': f'Bearer {create_sso_token()}'},
            params=params or None,
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json().get('trainings', [])
    except requests.RequestException:
        pass
    return []


def fetch_training_occurrence_from_agenda(occurrence_id, team_codes=None):
    """Fetch a single training occurrence from tt-agenda."""
    agenda_url = current_app.config.get('TT_AGENDA_INTERNAL_URL', 'http://tt-agenda:5000')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    params = {}
    if team_codes:
        normalized = sorted({
            (code or '').strip().upper()
            for code in team_codes
            if (code or '').strip()
        })
        if normalized:
            params['teams'] = ','.join(normalized)
    try:
        resp = requests.get(
            f'{agenda_url}/api/trainings/{occurrence_id}',
            headers={'X-TT-Internal-Secret': secret, 'Authorization': f'Bearer {create_sso_token()}'},
            params=params or None,
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json()
    except requests.RequestException:
        pass
    return None


def get_current_user(request):
    """Extract current user from JWT cookie or Authorization header."""
    from flask import session as flask_session

    token = None
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]
    if not token:
        token = request.cookies.get('tt_jwt')

    if not token:
        return None

    try:
        payload = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
        user_data = payload.get('user', payload)
        return {
            'id': user_data.get('id'),
            'username': user_data.get('username'),
            'role': user_data.get('role', 'user'),
            'display_name': user_data.get('display_name') or user_data.get('username'),
            'memberships': user_data.get('memberships') or payload.get('memberships') or [],
            'permissions': user_data.get('permissions') or payload.get('permissions') or [],
            'teams': user_data.get('teams') or payload.get('teams') or [],
            'member_roles': user_data.get('member_roles') or payload.get('member_roles') or [],
        }
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
