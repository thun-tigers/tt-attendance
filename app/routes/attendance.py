from flask import Blueprint, current_app, render_template, request, redirect, url_for, jsonify
from ..extensions import db
from ..models import Attendance
from ..forms import AttendanceForm
from ..jwt_utils import get_current_user, fetch_trainings_from_agenda, fetch_user_from_auth, create_sso_token
from datetime import datetime, timezone
import requests

bp = Blueprint('attendance', __name__)


@bp.route('/')
def index():
    """Main attendance page - show upcoming trainings with 3-button system."""
    current_user = get_current_user(request)
    if not current_user:
        return redirect(f'{current_app.config.get("AUTH_BASE_URL", "http://localhost:8085")}/auth/login?next={request.base_url}')

    # Fetch trainings from tt-agenda
    trainings = fetch_trainings_from_agenda()

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
            'date': t.get('date'),
            'time': t.get('time'),
            'location': t.get('location'),
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
    agenda_url = current_app.config.get('TT_AGENDA_INTERNAL_URL', 'http://tt-agenda:5000')
    trainings = []
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
            trainings = resp.json().get('trainings', [])
    except requests.RequestException:
        pass

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


@bp.route('/coach/training/<training_id>')
def coach_training_detail(training_id):
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
            f'{agenda_url}/api/trainings/{training_id}',
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
    attendances = Attendance.query.filter_by(training_id=training_id).all()

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


@bp.route('/api/trainings/<training_id}/set-status', methods=['POST'])
def set_status_api(training_id):
    """API endpoint for the 3-button system (AJAX)."""
    current_user = get_current_user(request)
    if not current_user:
        return jsonify({'error': 'unauthorized'}), 401

    data = request.get_json(silent=True) or {}
    status = data.get('status')
    reason = data.get('reason', '').strip() or None

    if status not in ('attending', 'maybe', 'declined'):
        return jsonify({'error': 'invalid_status'}), 400

    if status in ('maybe', 'declined') and not reason:
        return jsonify({'error': 'Bitte gib einen Grund an.'}), 400

    attendance = Attendance.query.filter_by(
        training_id=training_id,
        user_id=current_user['id'],
    ).first()

    if attendance:
        attendance.status = status
        attendance.reason = reason
        attendance.updated_at = datetime.now(timezone.utc)
    else:
        attendance = Attendance(
            training_id=training_id,
            user_id=current_user['id'],
            status=status,
            reason=reason,
        )
        db.session.add(attendance)

    db.session.commit()

    # Get updated summary
    all_attendances = Attendance.query.filter_by(training_id=training_id).all()
    summary = {'attending': 0, 'maybe': 0, 'declined': 0}
    for a in all_attendances:
        summary[a.status] = summary.get(a.status, 0) + 1

    return jsonify({
        'status': 'ok',
        'my_status': status,
        'summary': summary,
    })