from dotenv import load_dotenv
load_dotenv()

import os
import logging
import time
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Google API configuration
CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
SCOPES = [
    'openid',
    'email',
    'profile',
    'https://www.googleapis.com/auth/calendar.readonly',
    'https://www.googleapis.com/auth/calendar.events'
]


# Determinar se estamos em ambiente de produção ou desenvolvimento
def is_production():
    """Check if the application is running in production environment"""
    # Se o REPLIT_DEPLOYMENT_ID estiver presente, estamos em produção
    return bool(os.environ.get("REPLIT_DEPLOYMENT_ID"))

# Definir o URI de redirecionamento correto baseado no ambiente
def get_redirect_uri():
    return os.environ["REDIRECT_URI"]


# Obter o URI de redirecionamento
REDIRECT_URI = get_redirect_uri()





def create_oauth_flow():
    """
    Create OAuth2 flow object for Google authentication
    
    Returns:
        Flow: Google OAuth flow object
    """
    if not CLIENT_ID or not CLIENT_SECRET:
        logger.error("Google OAuth credentials not configured")
        raise ValueError("GOOGLE_OAUTH_CLIENT_ID or GOOGLE_OAUTH_CLIENT_SECRET not found")
    
    # Create flow instance
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI]
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    
    return flow

def get_authorization_url():
    """
    Generate authorization URL for Google OAuth
    
    Returns:
        str: Authorization URL
    """
    try:
        flow = create_oauth_flow()
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )
        
        return authorization_url, state
    except Exception as e:
        logger.error(f"Error generating authorization URL: {str(e)}")
        raise

def get_credentials_from_code(code):
    """
    Exchange authorization code for credentials
    
    Args:
        code (str): Authorization code from Google
        
    Returns:
        dict: Credentials information
    """
    try:
        # Create flow without validating scopes to prevent scope change error
        client_config = {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI]
            }
        }
        
        # Use OAuth2Session directly instead of Flow to avoid scope validation
        from oauthlib.oauth2 import WebApplicationClient
        from requests_oauthlib import OAuth2Session
        
        client = WebApplicationClient(CLIENT_ID)
        oauth = OAuth2Session(CLIENT_ID, redirect_uri=REDIRECT_URI, client=client)
        
        # Fetch the token directly, ignoring scope changes
        token = oauth.fetch_token(
            "https://oauth2.googleapis.com/token",
            code=code,
            client_secret=CLIENT_SECRET
        )
        
        # Create credentials from token
        from google.oauth2.credentials import Credentials
        
        # O escopo pode vir como lista ou como string
        scope = token.get('scope', '')
        if isinstance(scope, str):
            scopes = scope.split()
        else:
            # Já é uma lista, não precisamos fazer split
            scopes = scope
            
        credentials = Credentials(
            token=token.get('access_token'),
            refresh_token=token.get('refresh_token'),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            scopes=scopes
        )
        
        # Return credentials info as dictionary
        return {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes,
            'expiry': credentials.expiry.isoformat() if credentials.expiry else None
        }
    except Exception as e:
        logger.error(f"Error exchanging code for token: {str(e)}")
        raise

def build_service(credentials_data):
    """
    Build Google Calendar API service
    
    Args:
        credentials_data (dict): Credentials information
        
    Returns:
        Service: Google Calendar service
    """
    try:
        expiry = None
        if credentials_data.get('expiry'):
            expiry = datetime.fromisoformat(credentials_data['expiry'])
            
        credentials = Credentials(
            token=credentials_data['token'],
            refresh_token=credentials_data['refresh_token'],
            token_uri=credentials_data['token_uri'],
            client_id=credentials_data['client_id'],
            client_secret=credentials_data['client_secret'],
            scopes=credentials_data['scopes'],
            expiry=expiry
        )
        
        service = build('calendar', 'v3', credentials=credentials)
        return service
    except Exception as e:
        logger.error(f"Error building Google Calendar service: {str(e)}")
        raise

def list_upcoming_events(service, max_results=10, include_recent=True):
    """
    List upcoming calendar events
    
    Args:
        service: Google Calendar service
        max_results (int): Maximum number of events to return
        include_recent (bool): Whether to include recent events from the past week
        
    Returns:
        list: List of upcoming events
    """
    try:
        now = datetime.utcnow()
        
        # Se quiser incluir eventos recentes, pega desde 1 semana atrás
        if include_recent:
            # Obter data de 7 dias atrás
            one_week_ago = (now - timedelta(days=7)).isoformat() + 'Z'
            time_min = one_week_ago
        else:
            # Apenas eventos futuros
            time_min = now.isoformat() + 'Z'
            
        # Especificamos o fuso horário como America/Sao_Paulo para garantir 
        # que os eventos sejam retornados com os horários corretos
        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime',
            timeZone='America/Sao_Paulo'
        ).execute()
        
        events = events_result.get('items', [])
        return events
    except Exception as e:
        logger.error(f"Error fetching calendar events: {str(e)}")
        raise

def create_meeting_event(service, title, description, start_time, end_time, attendees=None):
    """
    Create a new meeting event in Google Calendar
    
    Args:
        service: Google Calendar service
        title (str): Event title
        description (str): Event description
        start_time (datetime): Start time
        end_time (datetime): End time
        attendees (list): List of email addresses for attendees
        
    Returns:
        dict: Created event information
    """
    try:
        # Usamos 'America/Sao_Paulo' como fuso horário padrão para horário brasileiro
        # Isso garante que os horários sejam exibidos corretamente no Google Calendar
        event = {
            'summary': title,
            'description': description,
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': 'America/Sao_Paulo',
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'America/Sao_Paulo',
            },
            # Adicionar configuração para incluir o link do Google Meet automaticamente
            'conferenceData': {
                'createRequest': {
                    'requestId': f'meeting-{int(time.time())}',  # ID único baseado no timestamp
                    'conferenceSolutionKey': {
                        'type': 'hangoutsMeet'
                    }
                }
            }
        }
        
        if attendees:
            event['attendees'] = [{'email': email} for email in attendees]
            
        # Atualizar a chamada API para incluir conferenceDataVersion=1, necessário para criar Google Meet
        event = service.events().insert(
            calendarId='primary', 
            body=event,
            conferenceDataVersion=1  # Isso habilita a criação do link do Meet
        ).execute()
        return event
    except Exception as e:
        logger.error(f"Error creating calendar event: {str(e)}")
        raise