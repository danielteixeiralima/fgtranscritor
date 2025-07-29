import os
import base64
import logging
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Carrega variáveis de ambiente do arquivo .env
load_dotenv()

logger = logging.getLogger(__name__)

class GmailService:
    """
    Gmail API service for sending verification emails using OAuth2,
    com todas as credenciais lidas do .env
    """

    def __init__(self):
        # Lê todas as credenciais e configurações do .env
        self.client_id       = os.environ.get('GMAIL_CLIENT_ID')
        self.client_secret   = os.environ.get('GMAIL_CLIENT_SECRET')
        self.refresh_token   = os.environ.get('GMAIL_REFRESH_TOKEN')
        self.sender_email    = os.environ.get('GMAIL_SENDER_EMAIL')
        self.scopes          = ['https://www.googleapis.com/auth/gmail.send']

        if not self.is_configured():
            logger.error("GmailService não está configurado corretamente. "
                         "Verifique as variáveis de ambiente no .env")

    def _get_gmail_service(self):
        """Cria e retorna o serviço autenticado do Gmail."""
        try:
            creds_info = {
                'client_id':     self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.refresh_token,
                'token_uri':     'https://oauth2.googleapis.com/token',
                'type':          'authorized_user'
            }
            creds = Credentials.from_authorized_user_info(creds_info, self.scopes)
            if creds.expired:
                creds.refresh(Request())
            return build('gmail', 'v1', credentials=creds)
        except Exception as e:
            logger.error(f"Erro criando serviço Gmail: {e}")
            return None

    def send_verification_email(self, to_email: str, username: str, verification_code: str):
        """
        Envia email de verificação usando Gmail API.
        Retorna (sucesso: bool, mensagem: str).
        """
        try:
            service = self._get_gmail_service()
            if not service:
                return False, "Gmail service não disponível"

            subject = "Verificação de Email - Transcritor Inteligente"
            html_body = f"""\
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; color: #333; line-height: 1.6; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: #007bff; color: #fff; padding: 20px; text-align: center; border-radius: 5px 5px 0 0; }}
        .content {{ background: #f8f9fa; padding: 30px; border-radius: 0 0 5px 5px; }}
        .code {{ background: #e9ecef; padding: 15px; font-size: 24px; font-weight: bold;
                 text-align: center; border-radius: 5px; letter-spacing: 3px; color: #007bff; margin: 20px 0; }}
        .footer {{ text-align: center; color: #666; font-size: 12px; margin-top: 20px; }}
        .warning {{ background: #f8d7da; color: #dc3545; padding: 10px; border-radius: 3px; }}
        .info {{ background: #d1ecf1; color: #0c5460; padding: 10px; border-radius: 3px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔐 Verificação de Email</h1>
        </div>
        <div class="content">
            <h2>Olá, {username}!</h2>
            <p>Obrigado por se registrar no <strong>Transcritor Inteligente</strong>. Para ativar sua conta, use o código de verificação abaixo:</p>
            <div class="code">{verification_code}</div>
            <div class="info">
                <p><strong>⏰ Importante:</strong> Este código é válido por apenas <strong>15 minutos</strong>.</p>
            </div>
            <p>Se você não conseguir inserir o código, solicite um novo na página de verificação.</p>
            <div class="warning">
                <p><strong>⚠️ Atenção:</strong> Se você não solicitou esta verificação, ignore este email. Sua conta não será criada sem a verificação.</p>
            </div>
            <p>Atenciosamente,<br><strong>Equipe do Transcritor Inteligente</strong></p>
        </div>
        <div class="footer">
            <p>Este é um email automático, não responda a este email.</p>
            <p>© 2025 Transcritor Inteligente - Todos os direitos reservados</p>
        </div>
    </div>
</body>
</html>
"""
            message = MIMEMultipart('alternative')
            message['to']      = to_email
            message['from']    = self.sender_email
            message['subject'] = subject
            message['Message-ID'] = f"<{datetime.now().timestamp()}@transcritor-inteligente>"
            message['Date']      = datetime.now().strftime('%a, %d %b %Y %H:%M:%S %z')

            message.attach(MIMEText(html_body, 'html'))
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
            result = service.users().messages().send(userId='me', body={'raw': raw}).execute()

            logger.info(f"Email enviado para {to_email} — ID: {result['id']}")
            return True, "Email enviado com sucesso"
        except HttpError as e:
            logger.error(f"Erro API Gmail: {e}")
            return False, f"Erro da API Gmail: {e}"
        except Exception as e:
            logger.error(f"Erro ao enviar email: {e}")
            return False, f"Erro ao enviar email: {e}"

    def is_configured(self) -> bool:
        """Verifica se todas as variáveis necessárias estão definidas."""
        return all([
            self.client_id,
            self.client_secret,
            self.refresh_token,
            self.sender_email
        ])

    def get_authorization_url(self) -> str | None:
        """
        Gera URL de autorização OAuth2 para setup inicial.
        Útil para obter novo refresh token.
        """
        try:
            domain = os.environ.get('REPLIT_DEV_DOMAIN')
            if not domain:
                raise ValueError("REPLIT_DEV_DOMAIN não definido no .env")

            redirect_uri = f"https://{domain}/auth/google/callback"
            flow = Flow.from_client_config(
                {
                    "web": {
                        "client_id":     self.client_id,
                        "client_secret": self.client_secret,
                        "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                        "token_uri":     "https://oauth2.googleapis.com/token",
                        "redirect_uris":[redirect_uri]
                    }
                },
                scopes=self.scopes
            )
            flow.redirect_uri = redirect_uri
            auth_url, _ = flow.authorization_url(
                access_type='offline',
                include_granted_scopes='true',
                prompt='consent'
            )
            return auth_url
        except Exception as e:
            logger.error(f"Erro ao obter URL de autorização: {e}")
            return None

# Instancia o serviço
gmail_service = GmailService()
 