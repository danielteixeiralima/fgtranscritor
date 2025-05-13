import os
import logging
from flask import Flask
from models import db, User
from sqlalchemy import text

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def create_app():
    """Create a Flask app for database migration"""
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    
    db.init_app(app)
    return app

def add_google_calendar_columns():
    """Add Google Calendar columns to User table"""
    app = create_app()
    
    with app.app_context():
        try:
            # Check if columns already exist
            inspector = db.inspect(db.engine)
            user_columns = [col['name'] for col in inspector.get_columns('user')]
            
            with db.engine.connect() as conn:
                if 'google_credentials' not in user_columns:
                    logger.info("Adding google_credentials column to User table")
                    conn.execute(text('ALTER TABLE "user" ADD COLUMN google_credentials TEXT'))
                    conn.commit()
                else:
                    logger.info("google_credentials column already exists")
                    
                if 'google_calendar_enabled' not in user_columns:
                    logger.info("Adding google_calendar_enabled column to User table")
                    conn.execute(text('ALTER TABLE "user" ADD COLUMN google_calendar_enabled BOOLEAN DEFAULT FALSE'))
                    conn.commit()
                else:
                    logger.info("google_calendar_enabled column already exists")
                
            logger.info("User table migration completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error during database migration: {str(e)}")
            return False
            
def add_google_calendar_event_column():
    """Add Google Calendar event ID column to Meeting table"""
    app = create_app()
    
    with app.app_context():
        try:
            # Check if columns already exist
            inspector = db.inspect(db.engine)
            meeting_columns = [col['name'] for col in inspector.get_columns('meeting')]
            
            with db.engine.connect() as conn:
                if 'google_calendar_event_id' not in meeting_columns:
                    logger.info("Adding google_calendar_event_id column to Meeting table")
                    conn.execute(text('ALTER TABLE meeting ADD COLUMN google_calendar_event_id VARCHAR(255)'))
                    conn.commit()
                    logger.info("google_calendar_event_id column added successfully")
                else:
                    logger.info("google_calendar_event_id column already exists")
                
            logger.info("Meeting table migration completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error during meeting table migration: {str(e)}")
            return False

if __name__ == "__main__":
    user_success = add_google_calendar_columns()
    meeting_success = add_google_calendar_event_column()
    
    if user_success and meeting_success:
        print("Database migrations completed successfully")
    else:
        print("One or more database migrations failed")