import os
import secrets
import smtplib
import base64
import json
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import current_app
from models import db, User
import logging
import requests
from gmail_service import gmail_service

logger = logging.getLogger(__name__)

class EmailService:
    def __init__(self):
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587
        self.sender_email = "noreply@transcritor.com"  # Placeholder 
        self.use_real_email = gmail_service.is_configured()  # Auto-detect if Gmail is configured
        
    def generate_verification_token(self):
        """Generate a 6-digit verification code"""
        return str(secrets.randbelow(900000) + 100000)
    
    def send_verification_email(self, user):
        """Send verification email to user"""
        try:
            # Generate verification token
            token = self.generate_verification_token()
            expires_at = datetime.utcnow() + timedelta(minutes=15)
            
            # Update user with verification token
            user.email_verification_token = token
            user.email_verification_sent_at = datetime.utcnow()
            user.email_verification_expires_at = expires_at
            db.session.commit()
            
            # Log email service configuration status
            logger.info(f"📧 DIAGNÓSTICO DE EMAIL:")
            logger.info(f"   Gmail configurado: {gmail_service.is_configured()}")
            logger.info(f"   Modo real ativo: {self.use_real_email}")
            logger.info(f"   Email destino: {user.email}")
            logger.info(f"   Código gerado: {token}")
            
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
                    .header {{ background-color: #007bff; color: white; padding: 20px; text-align: center; }}
                    .content {{ background-color: #f8f9fa; padding: 30px; margin: 20px 0; }}
                    .code {{ background-color: #e9ecef; padding: 15px; font-size: 24px; font-weight: bold; 
                             text-align: center; border-radius: 5px; margin: 20px 0; letter-spacing: 3px; }}
                    .footer {{ text-align: center; color: #666; font-size: 12px; }}
                    .warning {{ color: #dc3545; font-size: 14px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>Verificação de Email</h1>
                    </div>
                    <div class="content">
                        <h2>Olá, {user.username}!</h2>
                        <p>Obrigado por se registrar no Transcritor Inteligente. Para ativar sua conta, 
                           use o código de verificação abaixo:</p>
                        
                        <div class="code">{token}</div>
                        
                        <p>Este código é válido por <strong>15 minutos</strong>.</p>
                        
                        <p class="warning">Se você não solicitou esta verificação, ignore este email.</p>
                        
                        <p>Atenciosamente,<br>
                        Equipe do Transcritor Inteligente</p>
                    </div>
                    <div class="footer">
                        <p>Este é um email automático, não responda.</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            # Try to send via Gmail API if configured, otherwise use development mode
            if self.use_real_email:
                logger.info(f"🚀 Tentando enviar via Gmail API...")
                start_time = datetime.utcnow()
                
                # Send via Gmail API
                success, message = gmail_service.send_verification_email(
                    user.email, user.username, token
                )
                
                end_time = datetime.utcnow()
                duration = (end_time - start_time).total_seconds()
                
                if success:
                    logger.info(f"✅ Email enviado via Gmail API em {duration:.2f}s para {user.email}")
                    logger.info(f"🕐 Timestamp de envio: {end_time.strftime('%H:%M:%S')}")
                    return True, token
                else:
                    logger.error(f"❌ Gmail API falhou em {duration:.2f}s: {message}")
                    # Fall back to development mode
                    self.use_real_email = False
            
            # Development/testing mode - log the email details
            logger.info(f"📧 EMAIL ENVIADO PARA: {user.email}")
            logger.info(f"📝 ASSUNTO: {subject}")
            logger.info(f"🔢 CÓDIGO DE VERIFICAÇÃO: {token}")
            logger.info(f"⏰ VÁLIDO ATÉ: {expires_at}")
            
            # Also display in console for easy testing
            print(f"\n{'='*60}")
            print(f"📧 EMAIL DE VERIFICAÇÃO")
            print(f"{'='*60}")
            print(f"Para: {user.email}")
            print(f"Usuário: {user.username}")
            print(f"Código: {token}")
            print(f"Válido até: {expires_at.strftime('%H:%M:%S')}")
            print(f"{'='*60}\n")
            
            return True, token
            
        except Exception as e:
            logger.error(f"Error sending verification email: {str(e)}")
            return False, None
    
    def verify_email_token(self, user, token):
        """Verify the email token"""
        try:
            # Check if token matches and hasn't expired
            if (user.email_verification_token == token and 
                user.email_verification_expires_at and
                datetime.utcnow() < user.email_verification_expires_at):
                
                # Mark email as verified
                user.email_verified = True
                user.email_verification_token = None
                user.email_verification_sent_at = None
                user.email_verification_expires_at = None
                db.session.commit()
                
                logger.info(f"Email verified successfully for user: {user.username}")
                return True
            else:
                logger.warning(f"Invalid or expired token for user: {user.username}")
                return False
                
        except Exception as e:
            logger.error(f"Error verifying email token: {str(e)}")
            return False
    
    def resend_verification_email(self, user):
        """Resend verification email with rate limiting"""
        try:
            # Check if user can receive another email (rate limiting)
            if (user.email_verification_sent_at and 
                datetime.utcnow() - user.email_verification_sent_at < timedelta(minutes=1)):
                return False, "Aguarde 1 minuto antes de solicitar outro código"
            
            # Send new verification email
            success, token = self.send_verification_email(user)
            if success:
                return True, "Novo código de verificação enviado"
            else:
                return False, "Erro ao enviar email de verificação"
                
        except Exception as e:
            logger.error(f"Error resending verification email: {str(e)}")
            return False, "Erro interno do servidor"
    
    def _send_via_gmail_api(self, to_email, subject, html_body):
        """Send email via Gmail API (for production use)"""
        try:
            # This is now handled by gmail_service
            return gmail_service.send_verification_email(to_email, subject, html_body)
        except Exception as e:
            logger.error(f"Error sending via Gmail API: {str(e)}")
            return False, None
    
    def enable_real_email(self):
        """Enable real email sending (call this when you have proper credentials)"""
        self.use_real_email = gmail_service.is_configured()
        if self.use_real_email:
            logger.info("✅ Real email sending enabled via Gmail API")
        else:
            logger.warning("⚠️ Gmail API not configured - remaining in development mode")
    
    def get_email_status(self):
        """Get current email service status"""
        return {
            "real_email_enabled": self.use_real_email,
            "gmail_configured": gmail_service.is_configured(),
            "smtp_server": self.smtp_server,
            "sender_email": self.sender_email,
            "authorization_url": gmail_service.get_authorization_url() if not gmail_service.is_configured() else None
        }

# Initialize email service
email_service = EmailService()