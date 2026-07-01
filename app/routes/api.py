from flask import Blueprint, current_app, jsonify, request
from datetime import datetime, timezone

from ..extensions import db
from ..models import Attendance
from ..jwt_utils import get_current_user, fetch_user_from_auth, create_sso_token, fetch_training_occurrence_from_agenda
import requests

bp = Blueprint('api', __name__, url_prefix='/api')


def _authorized():
    """Check internal API secret for service-to-service calls."""
    expected = current_app.config.get('INTERNAL_API_SECRET')
    provided = request.headers.get('X-TT-Internal-Secret')
    return bool(expected and provided and provided == expected)


def _error(message, status_code=400):
    return jsonify({'error': message}), status_code


def _fetch_active_member_count(team_code):
    team_code = (team_code or '').strip().upper()
    if not team_code:
        return None

    auth_url = current_app.config.get('TT_AUTH_INTERNAL_URL', 'http://tt-auth:5000').rstrip('/')
    secret = current_app.config.get('INTERNAL_API_SECRET')
    try:
        response = requests.get(
            f'{auth_url}/api/internal/teams/{team_code}/active-member-count',
            headers={'X-TT-Internal-Secret': secret},
            timeout=5,
        )
        if response.status_code == 200:
            payload = response.json() or {}
            return int(payload.get('active_member_count', 0))
    except (requests.RequestException, TypeError, ValueError):
        pass
    return None


def _build_summary(occurrence_id, training=None):
    attendances = Attendance.query.filter_by(training_id=occurrence_id).all()
    summary = {'attending': 0, 'maybe': 0, 'declined': 0, 'open': 0}

    for attendance in attendances:
        summary[attendance.status] = summary.get(attendance.status, 0) + 1

    team_code = (training or {}).get('team_code')
    expected_count = _fetch_active_member_count(team_code)
    if expected_count is not None:
        responded = summary['attending'] + summary['maybe'] + summary['declined']
        summary['open'] = max(expected_count - responded, 0)

    return summary


# ===== Spieler-Endpunkte =====

@bp.route('/trainings/<occurrence_id>/attendance', methods=['GET', 'POST'])
def handle_attendance(occurrence_id):
    """Set or get attendance status for a specific training occurrence."""
    current_user = get_current_user(request)

    if request.method == 'GET':
        training = fetch_training_occurrence_from_agenda(occurrence_id)
        summary = _build_summary(occurrence_id, training)

        # Fetch user details from tt-auth
        participants = []
        attendances = Attendance.query.filter_by(training_id=occurrence_id).all()
        for a in attendances:
            user_info = fetch_user_from_auth(a.user_id) or {'id': a.user_id, 'username': f'User {a.user_id}'}
            participants.append({
                'user_id': a.user_id,
                'username': user_info.get('username'),
                'display_name': user_info.get('display_name'),
                'status': a.status,
                'reason': a.reason,
                'updated_at': a.updated_at.isoformat() if a.updated_at else None,
            })

        # Current user's status
        my_status = None
        if current_user:
            my_entry = Attendance.query.filter_by(
                training_id=occurrence_id,
                user_id=current_user['id'],
            ).first()
            if my_entry:
                my_status = my_entry.status

        return jsonify({
            'training_id': occurrence_id,
            'summary': summary,
            'participants': participants,
            'my_status': my_status,
        })

    # POST: Set attendance status
    if not current_user:
        return _error('authentication_required', 401)

    training = fetch_training_occurrence_from_agenda(occurrence_id)
    if training and training.get('is_cancelled'):
        return _error('cancelled_training', 409)

    data = request.get_json(silent=True) or {}
    status = data.get('status')
    raw_reason = data.get('reason')
    reason = (raw_reason.strip() if isinstance(raw_reason, str) else None) or None

    if status not in ('attending', 'maybe', 'declined'):
        return _error('invalid_status', 400)

    # Upsert
    attendance = Attendance.query.filter_by(
        training_id=occurrence_id,
        user_id=current_user['id'],
    ).first()

    if attendance:
        attendance.status = status
        attendance.reason = reason
        attendance.updated_at = datetime.now(timezone.utc)
    else:
        attendance = Attendance(
            training_id=occurrence_id,
            user_id=current_user['id'],
            status=status,
            reason=reason,
        )
        db.session.add(attendance)

    db.session.commit()
    training = fetch_training_occurrence_from_agenda(occurrence_id)
    summary = _build_summary(occurrence_id, training)
    return jsonify({'status': 'ok', 'attendance': attendance.to_dict(), 'summary': summary}), 201


@bp.route('/me/attendances', methods=['GET'])
def my_attendances():
    """Get all attendances for the current user."""
    current_user = get_current_user(request)
    if not current_user:
        return _error('authentication_required', 401)

    attendances = Attendance.query.filter_by(
        user_id=current_user['id'],
    ).order_by(Attendance.updated_at.desc()).all()

    # Fetch training info from tt-agenda
    agenda_url = current_app.config.get('TT_AGENDA_INTERNAL_URL', 'http://tt-agenda:5000')
    trainings = {}
    try:
        sso_token = create_sso_token()
        resp = requests.get(
            f'{agenda_url}/api/trainings',
            headers={
                'Authorization': f'Bearer {sso_token}',
                'X-TT-Internal-Secret': current_app.config.get('INTERNAL_API_SECRET'),
            },
            timeout=5,
        )
        if resp.status_code == 200:
            for t in resp.json().get('trainings', []):
                trainings[t['id']] = t
    except requests.RequestException:
        pass

    result = []
    for a in attendances:
        training = trainings.get(a.training_id, {})
        result.append({
            'attendance_id': a.id,
            'training_id': a.training_id,
            'training_title': training.get('title', 'Unbekanntes Training'),
            'training_date': training.get('date'),
            'status': a.status,
            'reason': a.reason,
            'updated_at': a.updated_at.isoformat() if a.updated_at else None,
        })

    return jsonify({'attendances': result})


# ===== Coach-Endpunkte =====

@bp.route('/coach/trainings/<occurrence_id>', methods=['GET'])
def coach_training_detail(occurrence_id):
    """Coach view: detailed attendance list for a training."""
    current_user = get_current_user(request)
    if not current_user or current_user.get('role') not in ('admin', 'coach', 'head_coach', 'team_betreuer'):
        return _error('forbidden', 403)

    attendances = Attendance.query.filter_by(training_id=occurrence_id).all()
    summary = {'attending': 0, 'maybe': 0, 'declined': 0}

    groups = {'attending': [], 'maybe': [], 'declined': []}
    for a in attendances:
        summary[a.status] = summary.get(a.status, 0) + 1
        user_info = fetch_user_from_auth(a.user_id) or {'id': a.user_id, 'username': f'User {a.user_id}'}
        entry = {
            'user_id': a.user_id,
            'username': user_info.get('username'),
            'display_name': user_info.get('display_name'),
            'first_name': user_info.get('first_name'),
            'last_name': user_info.get('last_name'),
            'email': user_info.get('email'),
            'status': a.status,
            'reason': a.reason,
            'updated_at': a.updated_at.isoformat() if a.updated_at else None,
        }
        groups.setdefault(a.status, []).append(entry)

    return jsonify({
        'training_id': occurrence_id,
        'summary': summary,
        'groups': groups,
        'total': len(attendances),
    })


@bp.route('/coach/summary', methods=['GET'])
def coach_summary():
    """Get attendance summary across all upcoming trainings."""
    current_user = get_current_user(request)
    if not current_user or current_user.get('role') not in ('admin', 'coach', 'head_coach', 'team_betreuer'):
        return _error('forbidden', 403)

    # Get all trainings from tt-agenda
    agenda_url = current_app.config.get('TT_AGENDA_INTERNAL_URL', 'http://tt-agenda:5000')
    try:
        sso_token = create_sso_token()
        resp = requests.get(
            f'{agenda_url}/api/trainings',
            headers={
                'Authorization': f'Bearer {sso_token}',
                'X-TT-Internal-Secret': current_app.config.get('INTERNAL_API_SECRET'),
            },
            timeout=5,
        )
        if resp.status_code != 200:
            return _error('failed_to_fetch_trainings', 502)
        trainings = resp.json().get('trainings', [])
    except requests.RequestException:
        return _error('failed_to_connect_to_agenda', 502)

    result = []
    for training in trainings:
        tid = training.get('id')
        attendances = Attendance.query.filter_by(training_id=tid).all()
        summary = {'attending': 0, 'maybe': 0, 'declined': 0}
        for a in attendances:
            summary[a.status] = summary.get(a.status, 0) + 1

        result.append({
            'training_id': tid,
            'training_title': training.get('title'),
            'training_date': training.get('date'),
            'summary': summary,
            'total_responded': len(attendances),
        })

    return jsonify({'trainings': result})


# ===== Service-to-Service API (für tt-agenda Integration) =====

@bp.route('/internal/training/<occurrence_id>/counts', methods=['GET'])
def internal_training_counts(occurrence_id):
    """Return attendance counts for a training occurrence (used by tt-agenda)."""
    if not _authorized():
        return _error('unauthorized', 401)

    attendances = Attendance.query.filter_by(training_id=occurrence_id).all()
    summary = {'attending': 0, 'maybe': 0, 'declined': 0}
    for a in attendances:
        summary[a.status] = summary.get(a.status, 0) + 1

    return jsonify({
        'training_id': occurrence_id,
        'summary': summary,
        'total': len(attendances),
    })


@bp.route('/internal/users/<int:user_id>/attendances', methods=['GET'])
def internal_user_attendances(user_id):
    """Get all attendances for a user (used by other services)."""
    if not _authorized():
        return _error('unauthorized', 401)

    attendances = Attendance.query.filter_by(user_id=user_id).all()
    return jsonify({
        'attendances': [a.to_dict() for a in attendances],
    })
