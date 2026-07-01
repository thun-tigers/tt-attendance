from urllib.parse import urljoin, urlparse

from flask import Blueprint, current_app, render_template, request, redirect, url_for, jsonify, flash, make_response
from ..extensions import db
from ..models import Attendance
from ..forms import AttendanceForm
from ..jwt_utils import (
    get_current_user,
    fetch_trainings_from_agenda_for_teams,
    fetch_training_occurrence_from_agenda,
    fetch_user_from_auth,
    create_sso_token,
    verify_sso_token,
    generate_jwt,
    set_jwt_cookie,
)
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


def _is_safe_url(target):
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


def _visible_team_codes(current_user):
    if not current_user:
        return []
    permissions = current_user.get('permissions') or []
    if current_user.get('role') == 'admin' or '*' in permissions:
        return []

    memberships = current_user.get('memberships') or []
    team_codes = []
    for membership in memberships:
        if not isinstance(membership, dict):
            continue
        if membership.get('is_active') is False:
            continue
        team_code = (membership.get('team_code') or '').strip().upper()
        if team_code and team_code not in team_codes:
            team_codes.append(team_code)
    return team_codes


@bp.route('/auth/sso')
def sso_login():
    token = (request.args.get('token') or '').strip()
    if not token:
        flash('SSO-Token fehlt.', 'danger')
        return redirect(url_for('attendance.index'))

    payload = verify_sso_token(token)
    if not payload:
        flash('Ungültiger SSO-Token.', 'danger')
        return redirect(url_for('attendance.index'))

    claims = payload.get('claims') or payload
    username = (payload.get('username') or claims.get('username') or '').strip()
    if not username:
        flash('SSO-Token enthält keinen Benutzernamen.', 'danger')
        return redirect(url_for('attendance.index'))

    local_user = {
        'id': int(payload.get('sub') or claims.get('sub')),
        'username': username,
        'role': payload.get('service_role') or payload.get('role') or payload.get('platform_role') or 'user',
        'display_name': payload.get('display_name') or claims.get('display_name') or username,
        'memberships': payload.get('memberships') or claims.get('memberships') or [],
        'permissions': payload.get('permissions') or claims.get('permissions') or [],
        'teams': payload.get('teams') or claims.get('teams') or [],
        'member_roles': payload.get('member_roles') or claims.get('member_roles') or [],
    }

    next_page = request.args.get('next')
    target = next_page if next_page and _is_safe_url(next_page) else url_for('attendance.index')
    response = make_response(redirect(target))
    set_jwt_cookie(
        response,
        generate_jwt(
            local_user,
            memberships=local_user['memberships'],
            permissions=local_user['permissions'],
        ),
    )
    return response


@bp.route('/')
def index():
    """Main attendance page - show upcoming trainings with 3-button system."""
    current_user = get_current_user(request)
    if not current_user:
        return redirect(f'{current_app.config.get("AUTH_BASE_URL", "http://localhost:8085")}/auth/login?next={request.base_url}')

    # Fetch trainings from tt-agenda
    trainings = fetch_trainings_from_agenda_for_teams(_visible_team_codes(current_user) or None)

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
        })

    return render_template(
        'attendance.html',
        current_user=current_user,
        trainings=trainings_with_status,
        active_tab='attendance',
    )


@bp.route('/coach')
def coach_dashboard():
    """Coach overview of all training attendances."""
    current_user = get_current_user(request)
    if not current_user:
        return redirect(f'{current_app.config.get("AUTH_BASE_URL", "http://localhost:8085")}/auth/login?next={request.base_url}')

    # Check if user has coach role
    is_coach = current_user.get('role') in ('admin', 'coach', 'head_coach')
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
    current_user = get_current_user(request)
    if not current_user or current_user.get('role') not in ('admin', 'coach', 'head_coach'):
        return redirect(url_for('attendance.index'))

    # Fetch training info from agenda
    agenda_url = current_app.config.get('TT_AGENDA_INTERNAL_URL', 'http://tt-agenda:5000')
    training = {}
    try:
        sso_token = create_sso_token()
        resp = requests.get(
            f'{agenda_url}/api/trainings/{occurrence_id}',
            headers={
                'Authorization': f'Bearer {sso_token}',
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

    groups = {'attending': [], 'maybe': [], 'declined': []}
    for a in attendances:
        user_info = fetch_user_from_auth(a.user_id) or {}
        groups.setdefault(a.status, []).append({
            'user_id': a.user_id,
            'display_name': user_info.get('display_name') or user_info.get('username', f'User {a.user_id}'),
            'username': user_info.get('username'),
            'first_name': user_info.get('first_name'),
            'last_name': user_info.get('last_name'),
            'email': user_info.get('email'),
            'status': a.status,
            'reason': a.reason,
            'updated_at': a.updated_at,
        })

    return render_template(
        'coach_training_detail.html',
        current_user=current_user,
        training=training,
        groups=groups,
        is_coach=True,
        active_tab='coach',
    )


@bp.route('/api/trainings/<occurrence_id>/set-status', methods=['POST'])
def set_status_api(occurrence_id):
    """API endpoint for the 3-button system (AJAX)."""
    current_user = get_current_user(request)
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
    all_attendances = Attendance.query.filter_by(training_id=occurrence_id).all()
    summary = {'attending': 0, 'maybe': 0, 'declined': 0}
    for a in all_attendances:
        summary[a.status] = summary.get(a.status, 0) + 1

    return jsonify({
        'status': 'ok',
        'my_status': status,
        'summary': summary,
    })
