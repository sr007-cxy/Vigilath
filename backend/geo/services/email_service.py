"""Resend transactional email client.

All outgoing mail (password reset, consultation acknowledgement, etc.) goes
through Resend's HTTP API. We talk to it with `requests` directly so no new
dependency is needed.

Secrets come from `backend/.env`:
    RESEND_API_KEY=re_xxx
    FROM_EMAIL="GEO Readiness Checker <noreply@vigilath.com>"
    FRONTEND_URL=https://www.vigilath.com
"""

import logging
from typing import Optional

import requests

from geo.database import settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"


class EmailService:
    def __init__(self):
        self.api_key = settings.RESEND_API_KEY
        self.from_email = settings.FROM_EMAIL
        self.frontend_url = settings.FRONTEND_URL.rstrip("/")
        self.sales_notify_email = settings.SALES_NOTIFY_EMAIL

    def _send(
        self,
        to: str,
        subject: str,
        html: str,
        text: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> bool:
        if not self.api_key:
            logger.error("RESEND_API_KEY is not configured; cannot send email to %s", to)
            return False

        payload = {
            "from": self.from_email,
            "to": [to],
            "subject": subject,
            "html": html,
        }
        if text:
            payload["text"] = text
        if reply_to:
            payload["reply_to"] = reply_to

        try:
            resp = requests.post(
                RESEND_API_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
        except requests.RequestException as e:
            logger.exception("Resend request failed for %s: %s", to, e)
            return False

        if resp.status_code >= 400:
            logger.error(
                "Resend rejected message to %s (%s): %s",
                to,
                resp.status_code,
                resp.text,
            )
            return False

        logger.info("Resend accepted message to %s: %s", to, resp.json().get("id"))
        return True

    def send_password_reset_email(self, recipient_email: str, reset_token: str) -> bool:
        reset_link = f"{self.frontend_url}/forgot-password?token={reset_token}"

        html = f"""
        <html>
          <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #1f2937;">
            <h2 style="color: #111827;">Password Reset Request</h2>
            <p>Hello,</p>
            <p>You requested a password reset for your GEO Readiness Checker account. Click the button below to choose a new password:</p>
            <p>
              <a href="{reset_link}"
                 style="display: inline-block; background-color: #2563eb; color: #ffffff; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 600;">
                Reset Password
              </a>
            </p>
            <p>Or copy and paste this URL into your browser:</p>
            <p style="word-break: break-all; color: #4b5563;">{reset_link}</p>
            <p>This link will expire in 15 minutes. If you did not request a reset, you can safely ignore this email.</p>
            <p>— GEO Readiness Checker Team</p>
          </body>
        </html>
        """

        text = (
            "Password Reset Request\n\n"
            "You requested a password reset for your GEO Readiness Checker account.\n"
            f"Open this link to choose a new password: {reset_link}\n\n"
            "This link will expire in 15 minutes. If you did not request a reset, ignore this email.\n\n"
            "— GEO Readiness Checker Team"
        )

        return self._send(
            to=recipient_email,
            subject="Reset your GEO Readiness Checker password",
            html=html,
            text=text,
        )

    def send_consultation_confirmation_email(
        self, recipient_email: str, name: str, message: str
    ) -> bool:
        safe_message = (message or "").replace("\n", "<br>")

        html = f"""
        <html>
          <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #1f2937;">
            <h2 style="color: #111827;">Consultation Request Received</h2>
            <p>Hello {name},</p>
            <p>Thanks for reaching out to the GEO Readiness Checker team. We have received your consultation request and will get back to you shortly.</p>
            <p><strong>Your message:</strong></p>
            <blockquote style="margin: 0; padding: 12px 16px; background: #f3f4f6; border-left: 4px solid #2563eb;">
              {safe_message}
            </blockquote>
            <p>We appreciate your interest and will respond as soon as possible.</p>
            <p>— GEO Readiness Checker Team</p>
          </body>
        </html>
        """

        text = (
            f"Hello {name},\n\n"
            "Thanks for reaching out to the GEO Readiness Checker team. "
            "We have received your consultation request and will get back to you shortly.\n\n"
            "Your message:\n"
            f"{message}\n\n"
            "— GEO Readiness Checker Team"
        )

        return self._send(
            to=recipient_email,
            subject="We received your consultation request",
            html=html,
            text=text,
        )


    def send_sales_notification_email(
        self,
        *,
        kind: str,
        name: str,
        email: str,
        website: str,
        message: str,
        tier_slug: Optional[str] = None,
        submission_id: Optional[int] = None,
    ) -> bool:
        """Internal notification to ops/sales when a new consultation comes in.

        `reply_to` is set to the submitter so sales can hit "Reply" in their
        inbox and reach the customer directly, without copy-pasting.
        """
        safe_message = (message or "").replace("\n", "<br>") or "<em>(no message)</em>"
        kind_label = "Sales Lead" if kind == "sales-lead" else "Contact"
        subject_parts = [f"[{kind_label}"]
        if tier_slug:
            subject_parts.append(f" / {tier_slug}")
        subject_parts.append(f"] {name} <{email}>")
        subject = "".join(subject_parts)

        rows = [
            ("Type", kind_label),
            ("Name", name),
            ("Email", f'<a href="mailto:{email}">{email}</a>'),
            ("Website", f'<a href="{website}" rel="noopener">{website}</a>' if website else "—"),
        ]
        if tier_slug:
            rows.append(("Tier", tier_slug))
        rows.append(("Message", safe_message))
        if submission_id is not None:
            rows.append(("DB id", str(submission_id)))

        table_rows = "".join(
            f'<tr><td style="padding:6px 12px;color:#6b7280;vertical-align:top;white-space:nowrap;">{k}</td>'
            f'<td style="padding:6px 12px;color:#111827;">{v}</td></tr>'
            for k, v in rows
        )
        html = f"""
        <html>
          <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #1f2937;">
            <h2 style="color: #111827; margin-bottom: 8px;">New {kind_label.lower()} submission</h2>
            <p style="color:#6b7280;margin-top:0;">Reply to this email to respond directly to the submitter.</p>
            <table style="border-collapse:collapse;border:1px solid #e5e7eb;">
              {table_rows}
            </table>
          </body>
        </html>
        """

        text_lines = [f"New {kind_label.lower()} submission", ""]
        for k, v in rows:
            # Strip HTML for the text fallback
            plain = v.replace("<br>", "\n").replace("<em>(no message)</em>", "(no message)")
            text_lines.append(f"{k}: {plain}")
        text_lines.append("")
        text_lines.append("Reply to this email to respond directly to the submitter.")

        return self._send(
            to=self.sales_notify_email,
            subject=subject,
            html=html,
            text="\n".join(text_lines),
            reply_to=email,
        )

    def send_sales_lead_confirmation_email(
        self, recipient_email: str, name: str, tier_slug: Optional[str] = None
    ) -> bool:
        """Acknowledgement to the /contact-sales submitter, mirroring the one
        /contact already sends. Keeps both entry points consistent."""
        tier_line = (
            f"<p>Plan of interest: <strong>{tier_slug}</strong></p>" if tier_slug else ""
        )
        html = f"""
        <html>
          <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #1f2937;">
            <h2 style="color: #111827;">We received your sales enquiry</h2>
            <p>Hello {name},</p>
            <p>Thanks for reaching out to GApex sales. We have received your enquiry and a team member will reply within 1 business day.</p>
            {tier_line}
            <p>— GApex Sales Team</p>
          </body>
        </html>
        """

        text = (
            f"Hello {name},\n\n"
            "Thanks for reaching out to GApex sales. We have received your enquiry "
            "and a team member will reply within 1 business day.\n"
        )
        if tier_slug:
            text += f"\nPlan of interest: {tier_slug}\n"
        text += "\n— GApex Sales Team"

        return self._send(
            to=recipient_email,
            subject="We received your GApex sales enquiry",
            html=html,
            text=text,
        )


email_service = EmailService()
