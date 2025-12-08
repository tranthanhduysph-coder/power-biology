from . import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

class User(UserMixin, db.Model):
    __tablename__ = 'user' # Đặt tên bảng rõ ràng
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True)
    password_hash = db.Column(db.String(128))
    bot_type = db.Column(db.String(20), default='gofai')
    is_admin = db.Column(db.Boolean, default=False)
    
    # Các trường phục vụ Chatbot & Session
    current_session_id = db.Column(db.String(50), nullable=True)
    current_thread_id = db.Column(db.String(100), nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    session_id = db.Column(db.String(50))
    sender = db.Column(db.String(20))
    content = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)

class VariableLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    session_id = db.Column(db.String(50))
    variable_name = db.Column(db.String(100))
    variable_value = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
