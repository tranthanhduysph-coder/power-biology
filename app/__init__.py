from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
import os

db = SQLAlchemy()
login_manager = LoginManager()

def create_app():
    app = Flask(__name__)
    
    app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'a_default_secret_key_for_local_development')
    db_path = os.path.join('/var/data', 'database.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'
    login_manager.login_message_category = "info"

    from . import models

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(models.User, int(user_id))

    from .routes import main as main_blueprint
    app.register_blueprint(main_blueprint)

    with app.app_context():
        db.create_all()
        if not models.User.query.filter_by(username='admin').first():
            print(">>> Creating admin account...")
            admin_user = models.User(username='admin', bot_type='ai', is_admin=True)
            admin_user.set_password('admin123')
            db.session.add(admin_user)
            db.session.commit()
            print(">>> Admin account created successfully.")
            
    return app