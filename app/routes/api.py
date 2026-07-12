from collections import defaultdict
from flask import Blueprint, current_app, jsonify, request
from datetime import datetime, timezone

from ..extensions import db
from ..models import Attendance
from ..attendance_summary import summarize_training_attendance
from ..jwt_utils import fetch_user_from_auth, create_sso_token, fetch_training_occurrence_from_agenda, fetch_past_trainings_from_agenda, fetch_trainings_from_agenda_for_teams
from ..statistics import aggregate, personal_aggregate, player_aggregate
from .auth import get_current_user
import requests

bp = Blueprint('api', __name__, url_prefix='/api')


def _cleanup_cancelled_training(occurrence_id):
    removed = Attendance.query.filter_by(training_id=str(occurrence_id)).delete(synchronize_session=False)
    if removed:
        db.session.commit()
    return removed


def _authorized():
    """Check internal API secret for service-to-service calls."""
    expected = current_app.config.get('INTERNAL_API_SECRET')
    provided = request.headers.get('X-TT-Internal-Secret')
    return bool(expected and provided and provided == expected)


def _error(message, status_code=400):
    return jsonify({'error': message}), status_code


def _statistics_trainings(current_user):
    teams = current_user.get('teams') or []
    past = fetch_past_trainings_from_agenda(teams or None, weeks=min(request.args.get('weeks', type=int) or 52, 520))
    upcoming = fetch_trainings_from_agenda_for_teams(teams or None)
    seen = {str(item.get('id')) for item in past}
    return past + [item for item in upcoming if str(item.get('id')) not in seen]


@bp.route('/me/statistics', methods=['GET'])
def my_statistics():
    current_user = get_current_user()
    if not current_user:
        return _error('authentication_required', 401)
    return jsonify(personal_aggregate(_statistics_trainings(current_user), current_user['id']))


@bp.route('/coach/statistics/overview', methods=['GET'])
def coach_statistics_overview():
    current_user = get_current_user()
    if not current_user or current_user.get('role') not in ('admin', 'coach', 'head_coach'):
        return _error('forbidden', 403)
    return jsonify(aggregate(_statistics_trainings(current_user)))


@bp.route('/coach/statistics/groups', methods=['GET'])
def coach_statistics_groups():
    current_user = get_current_user()
    if not current_user or current_user.get('role') not in ('admin', 'coach', 'head_coach'):
        return _error('forbidden', 403)
    result = aggregate(_statistics_trainings(current_user))
    groups = defaultdict(lambda: {'trainings': 0, 'eligible': 0, 'responded': 0})
    for row in result['trainings']:
        group = row.get('team_code') or 'unknown'
        groups[group]['trainings'] += 1
        groups[group]['eligible'] += row['eligible']
        groups[group]['responded'] += row['responded']
    return jsonify({'groups': groups, **result})


@bp.route('/coach/statistics/players', methods=['GET'])
def coach_statistics_players():
    current_user = get_current_user()
    if not current_user or current_user.get('role') not in ('admin', 'coach', 'head_coach'):
        return _error('forbidden', 403)
    return jsonify({'players': player_aggregate(_statistics_trainings(current_user))})


# ===== Spieler-Endpunkte =====

@bp.route('/trainings/<occurrence_id>/attendance', methods=['GET', 'POST'])
def handle_attendance(occurrence_id):
    """Set or get attendance status for a specific training occurrence."""
    current_user = get_current_user()
    training = fetch_training_occurrence_from_agenda(occurrence_id)
    summary_only = (request.args.get('summary_only') or '').strip().lower() in {'1', 'true', 'yes'}
    if training and training.get('is_cancelled'):
        _cleanup_cancelled_training(occurrence_id)
        if request.method == 'GET':
            return jsonify({
                'training_id': occurrence_id,
                'summary': {'attending': 0, 'maybe': 0, 'declined': 0},
                'position_summary': {},
                'participants': [],
                'my_status': None,
                'is_cancelled': True,
            })
        return _error('cancelled_training', 409)

    if request.method == 'GET':
        # Get all attendances for this training
        training_summary = summarize_training_attendance(occurrence_id)
        attendances = training_summary['attendances']
        summary = training_summary['summary']

        # Current user's status
        my_status = None
        if current_user:
            my_entry = Attendance.query.filter_by(
                training_id=occurrence_id,
                user_id=current_user['id'],
            ).first()
            if my_entry:
                my_status = my_entry.status

        payload = {
            'training_id': occurrence_id,
            'summary': summary,
            'position_summary': training_summary['position_summary'],
            'my_status': my_status,
        }

        if not summary_only:
            # Fetch user details from tt-auth only when the full participant list is requested.
            participants = []
            for a in attendances:
                summary[a.status] = summary.get(a.status, 0) + 1
                user_info = fetch_user_from_auth(a.user_id) or {'id': a.user_id, 'username': f'User {a.user_id}'}
                participants.append({
                    'user_id': a.user_id,
                    'username': user_info.get('username'),
                    'display_name': user_info.get('display_name'),
                    'status': a.status,
                    'reason': a.reason,
                    'updated_at': a.updated_at.isoformat() if a.updated_at else None,
                })
            payload['participants'] = participants

        return jsonify(payload)

    # POST: Set attendance status
    if not current_user:
        return _error('authentication_required', 401)

    data = request.get_json(silent=True) or {}
    status = data.get('status')
    raw_reason = data.get('reason')
    reason = (raw_reason.strip() if isinstance(raw_reason, str) else None) or None

    if status not in ('attending', 'maybe', 'declined'):
        return _error('invalid_status', 400)
    if status in ('maybe', 'declined') and not reason:
        return _error('reason_required', 400)

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
    return jsonify({'status': 'ok', 'attendance': attendance.to_dict()}), 201


@bp.route('/me/attendances', methods=['GET'])
def my_attendances():
    """Get all attendances for the current user."""
    current_user = get_current_user()
    if not current_user:
        return _error('authentication_required', 401)

    attendances = Attendance.query.filter_by(
        user_id=current_user['id'],
    ).order_by(Attendance.updated_at.desc()).all()

    # Fetch training info from tt-agenda
    agenda_url = current_app.config.get('TT_AGENDA_INTERNAL_URL') or 'http://tt-agenda:5000'
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
    current_user = get_current_user()
    if not current_user or current_user.get('role') not in ('admin', 'coach', 'head_coach'):
        return _error('forbidden', 403)

    training = fetch_training_occurrence_from_agenda(occurrence_id)
    if training and training.get('is_cancelled'):
        _cleanup_cancelled_training(occurrence_id)
        return jsonify({
            'training_id': occurrence_id,
            'summary': {'attending': 0, 'maybe': 0, 'declined': 0},
            'groups': {'attending': [], 'maybe': [], 'declined': []},
            'total': 0,
            'is_cancelled': True,
        })

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
    current_user = get_current_user()
    if not current_user or current_user.get('role') not in ('admin', 'coach', 'head_coach'):
        return _error('forbidden', 403)

    # Get all trainings from tt-agenda
    agenda_url = current_app.config.get('TT_AGENDA_INTERNAL_URL') or 'http://tt-agenda:5000'
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
        if training.get('is_cancelled'):
            _cleanup_cancelled_training(tid)
            result.append({
                'training_id': tid,
                'training_title': training.get('title'),
                'training_date': training.get('date'),
                'summary': {'attending': 0, 'maybe': 0, 'declined': 0},
                'total_responded': 0,
            })
            continue

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

    training = fetch_training_occurrence_from_agenda(occurrence_id)
    if training and training.get('is_cancelled'):
        _cleanup_cancelled_training(occurrence_id)
        return jsonify({
            'training_id': occurrence_id,
            'summary': {'attending': 0, 'maybe': 0, 'declined': 0},
            'total': 0,
        })

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
