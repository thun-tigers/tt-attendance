"""Verifiziert, dass tt-attendance das geteilte Layout aus tt-common rendert."""
from flask import render_template_string


class FakeUser:
    username = "coach1"
    display_name = "Coach Eins"
    role = "coach"


CHILD = '{% extends "base.html" %}{% block content %}<p id="c">x</p>{% endblock %}'


def test_eingeloggt_rendert_attendance_layout(app):
    with app.test_request_context("/"):
        html = render_template_string(CHILD, current_user=FakeUser())
    assert "/tt-common-static/js/table_enhancements.js" in html
    assert 'id="themeToggle"' in html
    assert "Anmeldung" in html
    assert "Coach" in html  # coach-Rolle sieht Coach-Nav
    assert "/logout" in html
    assert '<p id="c">x</p>' in html
