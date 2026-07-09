import requests
from flask import current_app

from .models import Attendance


STATUS_KEYS = ('attending', 'maybe', 'declined')


def fetch_position_groups():
    """Load active position groups from tt-infra for attendance badges."""
    infra_base = (current_app.config.get('TT_INFRA_INTERNAL_URL') or 'http://tt-infra:5000').rstrip('/')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    fallback = [
        {'key': 'OL', 'label': 'OL'},
        {'key': 'DL', 'label': 'DL'},
        {'key': 'LB', 'label': 'LB'},
        {'key': 'RB', 'label': 'RB'},
        {'key': 'DB', 'label': 'DB'},
        {'key': 'TE', 'label': 'TE'},
        {'key': 'WR', 'label': 'WR'},
        {'key': 'QB', 'label': 'QB'},
    ]
    if not secret:
        return fallback

    try:
        response = requests.get(
            f'{infra_base}/api/master-data/positions',
            headers={'X-TT-Internal-Secret': secret},
            timeout=3,
        )
        if response.status_code >= 400:
            current_app.logger.warning('tt-infra positions fetch failed: %s %s', response.status_code, response.text)
            return fallback
        payload = response.json() or {}
    except requests.RequestException as exc:
        current_app.logger.warning('tt-infra positions fetch failed: %s', exc)
        return fallback

    positions = []
    for item in payload.get('positions') or []:
        key = (item.get('key') or '').strip().upper()
        label = (item.get('label') or key).strip()
        if key:
            positions.append({'key': key, 'label': label or key})
    return positions or fallback


def fetch_member_position(auth_user_id):
    members_base = (current_app.config.get('TT_MEMBERS_INTERNAL_URL') or 'http://tt-members:5000').rstrip('/')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    if not secret:
        return None

    try:
        response = requests.get(
            f'{members_base}/api/internal/users/{auth_user_id}',
            headers={'X-TT-Internal-Secret': secret},
            timeout=3,
        )
        if response.status_code != 200:
            return None
        payload = response.json() or {}
    except requests.RequestException as exc:
        current_app.logger.warning('tt-members profile fetch failed for user_id=%s: %s', auth_user_id, exc)
        return None

    user = payload.get('user') or {}
    position = user.get('position')
    return position.strip().upper() if isinstance(position, str) and position.strip() else None


def build_attendance_summary(attendances):
    summary = {key: 0 for key in STATUS_KEYS}
    for attendance in attendances:
        summary[attendance.status] = summary.get(attendance.status, 0) + 1
    return summary


def build_position_summary(attendances, position_groups=None):
    """Count only confirmed attendees per position group."""
    groups = position_groups or fetch_position_groups()
    by_key = {
        group['key']: {'key': group['key'], 'label': group['label'], 'attending': 0}
        for group in groups
        if group.get('key')
    }

    unknown_count = 0
    for attendance in attendances:
        if attendance.status != 'attending':
            continue
        position = fetch_member_position(attendance.user_id)
        if position and position in by_key:
            by_key[position]['attending'] += 1
        else:
            unknown_count += 1

    result = list(by_key.values())
    if unknown_count:
        result.append({'key': 'UNKNOWN', 'label': 'Ohne Gruppe', 'attending': unknown_count})
    return result


def summarize_training_attendance(training_id, position_groups=None):
    attendances = Attendance.query.filter_by(training_id=training_id).all()
    return {
        'attendances': attendances,
        'summary': build_attendance_summary(attendances),
        'position_summary': build_position_summary(attendances, position_groups),
    }
