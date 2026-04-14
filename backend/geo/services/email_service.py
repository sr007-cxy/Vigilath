import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class EmailService:
    def __init__(self):
        self.smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        self.smtp_port = int(os.getenv('SMTP_PORT', '587'))
        self.smtp_user = os.getenv('SMTP_USER')
        self.smtp_password = os.getenv('SMTP_PASSWORD')
        self.sender_email = os.getenv('SENDER_EMAIL', self.smtp_user)
    
    def send_password_reset_email(self, recipient_email: str, reset_token: str) -> bool:
        """Send password reset email"""
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = 'Password Reset Request'
            msg['From'] = self.sender_email
            msg['To'] = recipient_email
            
            # Create reset link
            reset_link = f"http://localhost:3000/reset-password?token={reset_token}"
            
            # HTML email body
            html = f"""
            <html>
              <body style="font-family: Arial, sans-serif; line-height: 1.6;">
                <h2>Password Reset Request</h2>
                <p>Hello,</p>
                <p>You requested a password reset for your account. Please click the link below to reset your password:</p>
                <p><a href="{reset_link}" style="background-color: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px;">Reset Password</a></p>
                <p>If you did not request this reset, please ignore this email.</p>
                <p>This link will expire in 15 minutes.</p>
                <p>Best regards,<br>GEO Readiness Checker Team</p>
              </body>
            </html>
            """
            
            # Plain text email body
            text = f"""
            Password Reset Request
            
            Hello,
            
            You requested a password reset for your account. Please use the following link to reset your password:
            {reset_link}
            
            If you did not request this reset, please ignore this email.
            
            This link will expire in 15 minutes.
            
            Best regards,
            GEO Readiness Checker Team
            """
            
            # Attach both versions
            part1 = MIMEText(text, 'plain')
            part2 = MIMEText(html, 'html')
            msg.attach(part1)
            msg.attach(part2)
            
            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
            
            return True
        except Exception as e:
            print(f"Error sending password reset email: {e}")
            return False
    
    def send_consultation_confirmation_email(self, recipient_email: str, name: str, message: str) -> bool:
        """Send consultation confirmation email"""
        try:
            # Create message
            msg = MIMEMultipart('alternative')
            msg['Subject'] = 'Consultation Request Received'
            msg['From'] = self.sender_email
            msg['To'] = recipient_email
            
            # HTML email body
            html = f"""
            <html>
              <body style="font-family: Arial, sans-serif; line-height: 1.6;">
                <h2>Consultation Request Received</h2>
                <p>Hello {name},</p>
                <p>Thank you for reaching out to us. We have received your consultation request and will get back to you shortly.</p>
                <p><strong>Your message:</strong></p>
                <p>{message}</p>
                <p>We appreciate your interest in GEO Readiness Checker and will respond to your inquiry as soon as possible.</p>
                <p>Best regards,<br>GEO Readiness Checker Team</p>
              </body>
            </html>
            """
            
            # Plain text email body
            text = f"""
            Consultation Request Received
            
            Hello {name},
            
            Thank you for reaching out to us. We have received your consultation request and will get back to you shortly.
            
            Your message:
            {message}
            
            We appreciate your interest in GEO Readiness Checker and will respond to your inquiry as soon as possible.
            
            Best regards,
            GEO Readiness Checker Team
            """
            
            # Attach both versions
            part1 = MIMEText(text, 'plain')
            part2 = MIMEText(html, 'html')
            msg.attach(part1)
            msg.attach(part2)
            
            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
            
            return True
        except Exception as e:
            print(f"Error sending consultation confirmation email: {e}")
            return False

# Create singleton instance
email_service = EmailService()
