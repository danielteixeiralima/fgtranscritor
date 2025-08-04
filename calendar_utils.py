from uuid import uuid4
import json
import logging

from google_calendar_integration import (
    get_authorization_url,
    get_credentials_from_code,
    build_service as _build_service,
    list_upcoming_events,
    get_redirect_uri
)

logger = logging.getLogger(__name__)


def get_google_auth_url():
    """Wrapper for get_authorization_url"""
    return get_authorization_url()


def exchange_code_for_credentials(code):
    """Wrapper for get_credentials_from_code"""
    return get_credentials_from_code(code)


def build_calendar_service(credentials):
    """Wrapper for build_service"""
    return _build_service(credentials)


def get_calendar_events(service, max_results=50, include_recent=True):
    """Wrapper for list_upcoming_events"""
    return list_upcoming_events(service, max_results, include_recent)


def get_calendar_redirect_uri():
    """Wrapper for get_redirect_uri"""
    return get_redirect_uri()


def create_calendar_event(
    service,
    title: str,
    description: str,
    start_time,
    end_time,
    attendees: list = None,
    recurrence: dict = None
):
    """Cria evento no Google Calendar, com suporte a recorrência diária (dias úteis) ou semanal (dia específico)"""
    # Monta o corpo básico do evento
    event_body = {
        'summary': title,
        'description': description,
        'start': {
            'dateTime': start_time.isoformat(),
            'timeZone': 'America/Sao_Paulo'
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': 'America/Sao_Paulo'
        },
        'attendees': [{'email': e} for e in (attendees or [])],
        'conferenceDataVersion': 1
    }

    # Adiciona regra de recorrência se especificada
    if recurrence:
        freq     = recurrence.get('type', 'DAILY').upper()
        interval = recurrence.get('interval', 1)
        count    = recurrence.get('count')
        weekdays = recurrence.get('weekdays', [])  # pode ser lista de dias ou vazia

        # Monta o RRULE com FREQ, INTERVAL, BYDAY e COUNT
        rule = f"RRULE:FREQ={freq};INTERVAL={interval}"
        if weekdays:
            rule += f";BYDAY={','.join(weekdays)}"
        if count and count != 'unlimited':
            rule += f";COUNT={count}"
        event_body['recurrence'] = [rule]

    # Log do payload para depuração
    logger.debug(">> EVENT PAYLOAD: %s", json.dumps(event_body, indent=2, ensure_ascii=False))

    # Gera requestId único para conferência
    conference = {'createRequest': {'requestId': uuid4().hex}}
    event_body['conferenceData'] = conference

    # Chama a API
    created = service.events().insert(
        calendarId='primary',
        body=event_body,
        conferenceDataVersion=1
    ).execute()

    return created
