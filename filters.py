from datetime import datetime
from flask import Blueprint
from markupsafe import Markup

# Criar blueprint para filtros
filters_bp = Blueprint('filters', __name__)

@filters_bp.app_template_filter('datetime')
def format_datetime(value):
    """Formatar datetime para exibição legível"""
    if isinstance(value, str):
        try:
            # Converter string ISO para objeto datetime
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return value
    
    if isinstance(value, datetime):
        return value.strftime('%d/%m/%Y %H:%M')
    
    return value

@filters_bp.app_template_filter('nl2br')
def nl2br(value):
    """Converter quebras de linha para <br>"""
    if value:
        return Markup(value.replace('\n', '<br>'))