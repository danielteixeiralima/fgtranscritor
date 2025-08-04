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
CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
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
    """Get the appropriate redirect URI based on environment"""
    # Primeiro, tenta obter do .env (mais flexível)
    env_redirect = os.environ.get("REDIRECT_URI")
    if env_redirect:
        return env_redirect
    
    # Fallback para detecção automática do ambiente
    if is_production():
        # Em produção, usar o domínio de produção
        return "https://transcritor.replit.app/settings/google_callback"
    else:
        # Em desenvolvimento, usar o domínio de desenvolvimento
        dev_domain = os.environ.get('REPLIT_DEV_DOMAIN')
        return f"https://{dev_domain}/settings/google_callback"

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
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
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
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
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

        # Debug: Log credentials status
        logger.debug(f"Credenciais válidas: {credentials.valid}")
        logger.debug(f"Credenciais expiradas: {credentials.expired}")
        logger.debug(f"Tem refresh token: {bool(credentials.refresh_token)}")
        
        # If expired but has refresh token, try to refresh
        if credentials.expired and credentials.refresh_token:
            logger.debug("Tentando renovar credenciais expiradas")
            from google.auth.transport.requests import Request
            credentials.refresh(Request())
            logger.debug("Credenciais renovadas com sucesso")

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

        # Debug: Log parameters
        logger.debug(f"Buscando eventos - timeMin: {time_min}, maxResults: {max_results}")
        logger.debug(f"Include recent: {include_recent}")

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
        logger.debug(f"API retornou {len(events)} eventos")
        
        # Debug: Log first few events
        for i, event in enumerate(events[:3]):
            logger.debug(f"Evento {i+1}: {event.get('summary', 'Sem título')} - {event.get('start', {}).get('dateTime', 'Sem data')}")
        
        return events
    except Exception as e:
        logger.error(f"Error fetching calendar events: {str(e)}")
        raise

def create_meeting_event(service, title, description, start_time, end_time, attendees=None, recurrence=None):
    """
    Create a new meeting event in Google Calendar

    Args:
        service: Google Calendar service
        title (str): Event title
        description (str): Event description
        start_time (datetime): Start time
        end_time (datetime): End time
        attendees (list): List of email addresses for attendees
        recurrence (dict): Recurrence settings with 'type', 'interval', 'count', 'weekdays'

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

        # Adicionar recorrência se especificada
        if recurrence:
            rrule = _build_rrule(recurrence)
            if rrule:
                event['recurrence'] = [rrule]

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

def _build_rrule(recurrence):
    """
    Build RRULE string for Google Calendar recurrence

    Args:
        recurrence (dict): Recurrence settings with 'type', 'interval', 'count', 'weekdays'

    Returns:
        str: RRULE string following RFC 5545 standard
    """
    if not recurrence or recurrence.get('type') == 'none':
        return None
    
    recurrence_type = recurrence.get('type')
    interval = recurrence.get('interval', 1)
    count = recurrence.get('count', 730)  # Default to 730 (Google Calendar maximum expansion)
    weekdays = recurrence.get('weekdays', [])
    
    # Base RRULE
    rrule_parts = ['RRULE:FREQ=']
    
    if recurrence_type == 'daily':
        rrule_parts.append('DAILY')
    elif recurrence_type == 'weekdays':
        # Diariamente apenas dias úteis (seg-sex)
        rrule_parts.append('DAILY')
        rrule_parts.append(';BYDAY=MO,TU,WE,TH,FR')
    elif recurrence_type == 'weekly':
        rrule_parts.append('WEEKLY')
        # Add specific weekdays if provided
        if weekdays:
            weekday_map = {
                'sunday': 'SU',
                'monday': 'MO', 
                'tuesday': 'TU',
                'wednesday': 'WE',
                'thursday': 'TH',
                'friday': 'FR',
                'saturday': 'SA'
            }
            byday = ','.join([weekday_map[day] for day in weekdays if day in weekday_map])
            if byday:
                rrule_parts.append(f';BYDAY={byday}')
    elif recurrence_type == 'monthly':
        rrule_parts.append('MONTHLY')
    else:
        return None
    
    # Add interval if not 1
    if interval > 1:
        rrule_parts.append(f';INTERVAL={interval}')
    
    # Handle unlimited events - no COUNT or UNTIL parameters for truly unlimited
    if count != 'unlimited':
        # Add count only for limited events
        rrule_parts.append(f';COUNT={count}')
    # For unlimited events, we don't add COUNT or UNTIL - just the basic RRULE
    
    return ''.join(rrule_parts)