from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, SelectField, BooleanField
from wtforms.validators import DataRequired, Length, EqualTo
from flask_wtf.file import FileField, FileAllowed, FileRequired

class LoginForm(FlaskForm):
    username = StringField('ID Học sinh', validators=[DataRequired()])
    password = PasswordField('Mật khẩu', validators=[DataRequired()])
    submit = SubmitField('Đăng nhập')

class UserForm(FlaskForm):
    username = StringField('ID Học sinh', validators=[DataRequired(), Length(min=4, max=80)])
    password = PasswordField('Mật khẩu mặc định', validators=[DataRequired(), Length(min=6)])
    bot_type = SelectField('Loại Bot', choices=[('ai', 'Chatbot Ai'), ('gofai', 'Chatbot GOFAI')], validators=[DataRequired()])
    is_admin = BooleanField('Là Admin?')
    submit = SubmitField('Lưu')

class UploadCSVForm(FlaskForm):
    csv_file = FileField('Chọn file CSV', validators=[
        FileRequired(),
        FileAllowed(['csv'], 'Chỉ chấp nhận file CSV!')
    ])
    submit = SubmitField('Upload và Thêm hàng loạt')

class ChangePasswordForm(FlaskForm):
    current_password = PasswordField('Mật khẩu hiện tại', validators=[DataRequired()])
    new_password = PasswordField('Mật khẩu mới', validators=[
        DataRequired(),
        Length(min=6, message='Mật khẩu phải có ít nhất 6 ký tự.')
    ])
    confirm_password = PasswordField('Xác nhận mật khẩu mới', validators=[
        DataRequired(),
        EqualTo('new_password', message='Mật khẩu xác nhận không khớp.')
    ])
    submit = SubmitField('Đổi mật khẩu')

class ResetPasswordForm(FlaskForm):
    new_password = PasswordField('Mật khẩu mới', validators=[
        DataRequired(),
        Length(min=6, message='Mật khẩu phải có ít nhất 6 ký tự.')
    ])
    submit = SubmitField('Reset Mật khẩu')