import os
import base64
import json
import logging
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

class GmailService:
    """
    Gmail API service for sending verification emails using OAuth2
    """
    
    def __init__(self):
        # Use the correct client credentials from environment
        self.client_id = "742753873371-60jntmoqfcqfsipuhbg8en6e4cv7tqso.apps.googleusercontent.com"
        self.client_secret = "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"
        self.refresh_token = os.environ.get('GMAIL_REFRESH_TOKEN', '1//06Ye2NvhHBVF6CgYIARAAGAYSNwF-L9IrwRDG7Nqh807wMqae8_NJpgNRPmlnV9lOS09-ph_OUpVn4hcM7QQojqW9GpckOQ4M2FE')
        self.sender_email = os.environ.get('GMAIL_SENDER_EMAIL', 'Transcritor Inteligente <renan@inovailab.com>')
        self.scopes = ['https://www.googleapis.com/auth/gmail.send'] 
        
    def _get_gmail_service(self):
        """Get authenticated Gmail service"""
        try:
            # Create credentials from environment variables
            creds_info = {
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.refresh_token,
                'token_uri': 'https://oauth2.googleapis.com/token',
                'type': 'authorized_user'
            }
            
            creds = Credentials.from_authorized_user_info(creds_info, self.scopes)
            
            # Refresh token if needed
            if creds.expired:
                creds.refresh(Request())
            
            # Build Gmail service
            service = build('gmail', 'v1', credentials=creds)
            return service
            
        except Exception as e:
            logger.error(f"Error creating Gmail service: {str(e)}")
            return None
    
    def send_verification_email(self, to_email, username, verification_code):
        """
        Send verification email using Gmail API
        """
        try:
            service = self._get_gmail_service()
            if not service:
                return False, "Gmail service not available"
            
            # Create email content
            subject = "Verificação de Email - Transcritor Inteligente"
            html_body = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                    .header {{ background-color: #007bff; color: white; padding: 20px; text-align: center; border-radius: 5px 5px 0 0; }}
                    .content {{ background-color: #f8f9fa; padding: 30px; margin: 0; border-radius: 0 0 5px 5px; }}
                    .code {{ background-color: #e9ecef; padding: 15px; font-size: 24px; font-weight: bold; 
                             text-align: center; border-radius: 5px; margin: 20px 0; letter-spacing: 3px; color: #007bff; }}
                    .footer {{ text-align: center; color: #666; font-size: 12px; margin-top: 20px; }}
                    .warning {{ color: #dc3545; font-size: 14px; background-color: #f8d7da; padding: 10px; border-radius: 3px; }}
                    .info {{ color: #0c5460; background-color: #d1ecf1; padding: 10px; border-radius: 3px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>🔐 Verificação de Email</h1>
                    </div>
                    <div class="content">
                        <h2>Olá, {username}!</h2>
                        <p>Obrigado por se registrar no <strong>Transcritor Inteligente</strong>. Para ativar sua conta, 
                           use o código de verificação abaixo:</p>
                        
                        <div class="code">{verification_code}</div>
                        
                        <div class="info">
                            <p><strong>⏰ Importante:</strong> Este código é válido por apenas <strong>15 minutos</strong>.</p>
                        </div>
                        
                        <p>Se você não conseguir inserir o código, você pode solicitar um novo código na página de verificação.</p>
                        
                        <div class="warning">
                            <p><strong>⚠️ Atenção:</strong> Se você não solicitou esta verificação, ignore este email. Sua conta não será criada sem a verificação.</p>
                        </div>
                        
                        <p>Atenciosamente,<br>
                        <strong>Equipe do Transcritor Inteligente</strong></p>
                    </div>
                    <div class="footer">
                        <p>Este é um email automático, não responda a este email.</p>
                        <p>© 2025 Transcritor Inteligente - Todos os direitos reservados</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            # Create message with optimized headers for faster delivery
            message = MIMEMultipart('alternative')
            message['to'] = to_email
            message['from'] = self.sender_email
            message['subject'] = subject
            
            # Add standard headers (removing problematic priority headers)
            message['Message-ID'] = f"<{datetime.now().timestamp()}@transcritor-inteligente>"
            message['Date'] = datetime.now().strftime('%a, %d %b %Y %H:%M:%S %z')
            
            # Add HTML content
            html_part = MIMEText(html_body, 'html')
            message.attach(html_part)
            
            # Encode message
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            
            # Send message
            result = service.users().messages().send(
                userId='me',
                body={'raw': raw_message}
            ).execute()
            
            logger.info(f"✅ Email sent successfully to {to_email} - Message ID: {result['id']}")
            return True, "Email enviado com sucesso"
            
        except HttpError as e:
            logger.error(f"Gmail API error: {str(e)}")
            return False, f"Erro da API Gmail: {str(e)}"
        except Exception as e:
            logger.error(f"Error sending email: {str(e)}")
            return False, f"Erro ao enviar email: {str(e)}"
    
    def is_configured(self):
        """Check if Gmail service is properly configured"""
        return all([
            self.client_id,
            self.client_secret,
            self.refresh_token
        ])
    
    def get_authorization_url(self):
        """Get OAuth2 authorization URL for initial setup"""
        try:
            # Get current domain from environment
            domain = os.environ.get('REPLIT_DEV_DOMAIN')
            redirect_uri = f"https://{domain}/auth/google/callback"
            
            flow = Flow.from_client_config(
                {
                    "web": {
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": [redirect_uri]
                    }
                },
                scopes=self.scopes
            )
            
            flow.redirect_uri = redirect_uri
            
            auth_url, _ = flow.authorization_url(
                access_type='offline',
                include_granted_scopes='true',
                prompt='consent'  # Force consent to get refresh token
            )
            
            return auth_url
            
        except Exception as e:
            logger.error(f"Error getting authorization URL: {str(e)}")
            return None

# Initialize Gmail service
gmail_service = GmailService()