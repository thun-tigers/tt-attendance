from wtforms import Form, SelectField, TextAreaField
from wtforms.validators import DataRequired, Optional, Length


class AttendanceForm(Form):
    status = SelectField(
        'Status',
        choices=[
            ('attending', '👍 Teilnehmen'),
            ('maybe', '❓ Vielleicht'),
            ('declined', '👎 Absage'),
        ],
        validators=[DataRequired()],
    )
    reason = TextAreaField(
        'Grund',
        validators=[Optional(), Length(max=500)],
        description='Bitte Grund angeben bei Absage oder Unsicherheit',
    )