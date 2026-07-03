from urllib.parse import urljoin, urlparse

from flask import Blueprint, current_app, render_template, request, redirect, url_for, jsonify, flash
from ..extensions import db
from ..models import Attendance
from ..attendance_summary import fetch_member_position, fetch_position_groups, summarize_training_attendance
from ..forms import AttendanceForm
from ..jwt_utils import (
    fetch_trainings_from_agenda_for_teams,
    fetch_training_occurrence_from_agenda,
    fetch_user_from_auth,
)
from .auth import get_current_user
from datetime import datetime, timezone
import requests

bp = Blueprint('attendance', __name__)

_WEEKDAY_SHORT = ['MO', 'DI', 'MI', 'DO', 'FR', 'SA', 'SO']


def _format_date_label(date_iso):
    if not date_iso:
        return None
    try:
        dt = datetime.strptime(date_iso, '%Y-%m-%d')
    except ValueError:
        return date_iso
    weekday = _WEEKDAY_SHORT[dt.weekday()]
    return f'{weekday} {dt.strftime("%d.%m.%Y")}'


def _visible_team_codes(current_user):
    if not current_user:
        return []
    permissions = current_user.get('permissions') or []
    if current_user.get('role') == 'admin' or '*' in permissions:
        return []

    claims = current_user.get('claims_json') or {}
    memberships = claims.get('memberships') or current_user.get('memberships') or []
    team_codes = []
    for membership in memberships:
        if not isinstance(membership, dict):
            continue
        if membership.get('is_active') is False:
            continue
        team_code = (membership.get('team_code') or '').strip().upper()
        if team_code and team_code not in team_codes:
            team_codes.append(team_code)

    if team_codes:
        return team_codes

    fallback_codes = []
    for team_code in claims.get('teams') or current_user.get('teams') or []:
        if not isinstance(team_code, str):
            continue
        code = team_code.strip().upper()
        if code and code not in fallback_codes:
            fallback_codes.append(code)
    return fallback_codes


def _is_coach_user(current_user):
    return bool(current_user and current_user.get('role') in ('admin', 'coach', 'head_coach'))


def _status_counts(attendances):
    counts = {'attending': 0, 'maybe': 0, 'declined': 0}
    for attendance in attendances:
        counts[attendance.status] = counts.get(attendance.status, 0) + 1
    return counts


def _presence_counts(attendances):
    return {
        'present': sum(1 for attendance in attendances if attendance.presence_status == 'present'),
        'unexcused': sum(1 for attendance in attendances if attendance.presence_status == 'unexcused'),
    }


def _build_coach_presence_groups(attendances):
    position_groups = fetch_position_groups()
    positions_by_key = {
        group['key']: {
            'key': group['key'],
            'label': group['label'],
            'statuses': {'attending': [], 'maybe': [], 'declined': []},
            'total': 0,
        }
        for group in position_groups
        if group.get('key')
    }
    unknown_key = 'UNKNOWN'

    for attendance in attendances:
        user_info = fetch_user_from_auth(attendance.user_id) or {}
        position = fetch_member_position(attendance.user_id)
        if position not in positions_by_key:
            positions_by_key.setdefault(unknown_key, {
                'key': unknown_key,
                'label': 'Ohne Gruppe',
                'statuses': {'attending': [], 'maybe': [], 'declined': []},
                'total': 0,
            })
            position = unknown_key

        entry = {
            'user_id': attendance.user_id,
            'display_name': user_info.get('display_name') or user_info.get('username', f'User {attendance.user_id}'),
            'username': user_info.get('username'),
            'first_name': user_info.get('first_name'),
            'last_name': user_info.get('last_name'),
            'email': user_info.get('email'),
            'status': attendance.status,
            'presence_status': attendance.presence_status,
            'reason': attendance.reason,
            'updated_at': attendance.updated_at,
        }
        positions_by_key[position]['statuses'].setdefault(attendance.status, []).append(entry)
        positions_by_key[position]['total'] += 1

    return [group for group in positions_by_key.values() if group['total'] > 0]


@bp.route('/')
def index():
    """Main attendance page - show upcoming trainings with 3-button system."""
    current_user = get_current_user()
    if not current_user:
        from flask import session as flask_session
        flask_session['next_after_login'] = request.url
        return redirect(url_for('auth.login', next=request.full_path if request.query_string else request.path))

    # Fetch trainings from tt-agenda
    trainings = fetch_trainings_from_agenda_for_teams(_visible_team_codes(current_user) or None)
    position_groups = fetch_position_groups()

    # Get user's existing attendances
    my_attendances = {
        a.training_id: a
        for a in Attendance.query.filter_by(user_id=current_user['id']).all()
    }

    trainings_with_status = []
    for t in trainings:
        aid = str(t.get('id', ''))
        attendance = my_attendances.get(aid)
        status = attendance.status if attendance else None
        reason = attendance.reason if attendance else None
        trainings_with_status.append({
            'id': aid,
            'title': t.get('title', 'Training'),
            'team_code': t.get('team_code'),
            'date': t.get('date'),
            'date_label': _format_date_label(t.get('date')),
            'time': t.get('time'),
            'start_time': t.get('start_time'),
            'end_time': t.get('end_time'),
            'location': t.get('location'),
            'is_cancelled': bool(t.get('is_cancelled', False)),
            'my_status': status,
            'my_reason': reason,
            'position_summary': summarize_training_attendance(aid, position_groups)['position_summary'],
        })

    return render_template(
        'attendance.html',
        current_user=current_user,
        trainings=trainings_with_status,
        is_coach=_is_coach_user(current_user),
        active_tab='attendance',
    )


@bp.route('/coach')
def coach_dashboard():
    """Coach overview of all training attendances."""
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for('auth.login'))

    # Check if user has coach role
    is_coach = _is_coach_user(current_user)
    if not is_coach:
        return redirect(url_for('attendance.index'))

    # Fetch trainings with attendance counts
    trainings = fetch_trainings_from_agenda_for_teams(_visible_team_codes(current_user) or None)

    # Build summary per training
    training_summaries = []
    for t in trainings:
        tid = str(t.get('id', ''))
        attendances = Attendance.query.filter_by(training_id=tid).all()
        summary = {'attending': 0, 'maybe': 0, 'declined': 0}
        for a in attendances:
            summary[a.status] = summary.get(a.status, 0) + 1

        training_summaries.append({
            'id': tid,
            'title': t.get('title', 'Training'),
            'team_code': t.get('team_code'),
            'date': t.get('date'),
            'time': t.get('time'),
            'summary': summary,
            'total': len(attendances),
        })

    return render_template(
        'coach_dashboard.html',
        current_user=current_user,
        trainings=training_summaries,
        is_coach=is_coach,
        active_tab='coach',
    )


@bp.route('/coach/training/<occurrence_id>')
def coach_training_detail(occurrence_id):
    """Detailed view of one training's attendance for coaches."""
    current_user = get_current_user()
    if not _is_coach_user(current_user):
        return redirect(url_for('attendance.index'))

    # Fetch training info from agenda
    agenda_url = current_app.config.get('TT_AGENDA_INTERNAL_URL', 'http://tt-agenda:5000')
    training = {}
    try:
        from ..jwt_utils import create_sso_token
        resp = requests.get(
            f'{agenda_url}/api/trainings/{occurrence_id}',
            headers={
                'Authorization': f'Bearer {create_sso_token()}',
                'X-TT-Internal-Secret': current_app.config.get('INTERNAL_API_SECRET'),
            },
            timeout=5,
        )
        if resp.status_code == 200:
            training = resp.json()
    except requests.RequestException:
        pass

    # Get attendances with user details
    attendances = Attendance.query.filter_by(training_id=occurrence_id).all()

    position_groups = _build_coach_presence_groups(attendances)
    groups = {'attending': [], 'maybe': [], 'declined': []}
    for group in position_groups:
        for status, participants in group['statuses'].items():
            groups.setdefault(status, []).extend(participants)

    return render_template(
        'coach_training_detail.html',
        current_user=current_user,
        training=training,
        occurrence_id=occurrence_id,
        groups=groups,
        position_groups=position_groups,
        summary=_status_counts(attendances),
        presence_summary=_presence_counts(attendances),
        is_coach=True,
        active_tab='coach',
    )


@bp.route('/api/trainings/<occurrence_id>/presence', methods=['POST'])
def set_presence_api(occurrence_id):
    current_user = get_current_user()
    if not _is_coach_user(current_user):
        return jsonify({'error': 'forbidden'}), 403

    data = request.get_json(silent=True) or {}
    user_id = data.get('user_id')
    attendance_status = data.get('attendance_status')
    if attendance_status not in ('attending', 'declined', 'unexcused'):
        return jsonify({'error': 'invalid_attendance_status'}), 400

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid_user_id'}), 400

    attendance = Attendance.query.filter_by(training_id=occurrence_id, user_id=user_id).first()
    if not attendance:
        return jsonify({'error': 'attendance_not_found'}), 404

    attendance.status = 'declined' if attendance_status == 'unexcused' else attendance_status
    if attendance_status == 'attending':
        attendance.presence_status = 'present'
    elif attendance_status == 'unexcused':
        attendance.presence_status = 'unexcused'
    else:
        attendance.presence_status = None
    attendance.presence_marked_at = datetime.now(timezone.utc) if attendance.presence_status else None
    attendance.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    attendances = Attendance.query.filter_by(training_id=occurrence_id).all()
    return jsonify({
        'status': 'ok',
        'user_id': user_id,
        'attendance_status': attendance.status,
        'presence_status': attendance.presence_status,
        'summary': _status_counts(attendances),
        'presence_summary': _presence_counts(attendances),
    })


@bp.route('/api/trainings/<occurrence_id>/set-status', methods=['POST'])
def set_status_api(occurrence_id):
    """API endpoint for the 3-button system (AJAX)."""
    current_user = get_current_user()
    if not current_user:
        return jsonify({'error': 'unauthorized'}), 401

    training = fetch_training_occurrence_from_agenda(occurrence_id)
    if training and training.get('is_cancelled'):
        return jsonify({'error': 'cancelled_training'}), 409

    data = request.get_json(silent=True) or {}
    status = data.get('status')
    raw_reason = data.get('reason')
    reason = (raw_reason.strip() if isinstance(raw_reason, str) else None) or None

    if status not in ('attending', 'maybe', 'declined'):
        return jsonify({'error': 'invalid_status'}), 400

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

    # Get updated summary
    training_summary = summarize_training_attendance(occurrence_id)

    return jsonify({
        'status': 'ok',
        'my_status': status,
        'summary': training_summary['summary'],
        'position_summary': training_summary['position_summary'],
    })
