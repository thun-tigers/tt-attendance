import jwt
import requests
from datetime import datetime, timedelta, timezone
from flask import current_app


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


def fetch_trainings_from_agenda_for_teams(team_codes=None, limit=None):
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
    if limit is not None:
        params['limit'] = str(int(limit))
    try:
        resp = requests.get(
            f'{agenda_url}/api/trainings',
            headers={'X-TT-Internal-Secret': secret, 'Authorization': f'Bearer {create_sso_token()}'},
            params=params or None,
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json().get('trainings', [])
        current_app.logger.warning(
            'tt-agenda trainings fetch failed with status %s for teams=%s',
            resp.status_code,
            params.get('teams') if params else None,
        )
    except requests.RequestException:
        current_app.logger.warning(
            'tt-agenda trainings fetch failed for teams=%s',
            params.get('teams') if params else None,
            exc_info=True,
        )
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
        current_app.logger.warning(
            'tt-agenda training detail fetch failed with status %s for occurrence_id=%s teams=%s',
            resp.status_code,
            occurrence_id,
            params.get('teams') if params else None,
        )
    except requests.RequestException:
        current_app.logger.warning(
            'tt-agenda training detail fetch failed for occurrence_id=%s teams=%s',
            occurrence_id,
            params.get('teams') if params else None,
            exc_info=True,
        )
    return None
