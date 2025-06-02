from datetime import datetime
import json
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# Initialize SQLAlchemy
db = SQLAlchemy()

class User(UserMixin, db.Model):
    """User model for authentication"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # **Novo campo de admin**
    admin = db.Column(db.Boolean, default=False, nullable=False)

    meetings = db.relationship('Meeting', backref='user', lazy='dynamic', cascade='all, delete-orphan')
    
    google_credentials = db.Column(db.Text, nullable=True)
    google_calendar_enabled = db.Column(db.Boolean, default=False)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    @property
    def is_admin(self):
        """Permite usar current_user.is_admin"""
        return self.admin

    def set_google_credentials(self, credentials_dict):
        if credentials_dict:
            self.google_credentials = json.dumps(credentials_dict)
            self.google_calendar_enabled = True
        else:
            self.google_credentials = None
            self.google_calendar_enabled = False
    
    def get_google_credentials(self):
        if self.google_credentials:
            return json.loads(self.google_credentials)
        return None
    
    def __repr__(self):
        return f'<User {self.username}>'


class Meeting(db.Model):
    """Meeting model for storing meeting analysis"""
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(500), nullable=False)  # Aumentado de 120 para 500 caracteres
    agenda = db.Column(db.Text, nullable=False)
    transcription = db.Column(db.Text, nullable=True)
    language = db.Column(db.String(10), default='auto')  # Auto-detected language
    alignment_score = db.Column(db.Float)
    meeting_date = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    results_json = db.Column(db.Text)  # Store the full analysis results as JSON
    audio_url = db.Column(db.String(5000), nullable=True)
    video_url = db.Column(db.String(5000), nullable=True)
    
    # Google Calendar integration
    google_calendar_event_id = db.Column(db.String(255), nullable=True)  # ID do evento no Google Calendar
    fireflies_transcript_id = db.Column(db.String(255), nullable=True)
    
    @property
    def results(self):
        """Convert JSON string to dictionary"""
        if self.results_json:
            return json.loads(self.results_json)
        return {}
    
    @results.setter
    def results(self, value):
        """Convert dictionary to JSON string"""
        self.results_json = json.dumps(value)
        # Also update the alignment score from the results
        if value and 'alignment_score' in value:
            self.alignment_score = float(value['alignment_score'])
    
    def __repr__(self):
        return f'<Meeting {self.title}>'