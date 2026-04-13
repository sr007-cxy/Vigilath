from fastapi import APIRouter
from pydantic import BaseModel
from app.services.email_service import email_service
from app.utils.error_handler import AppException

router = APIRouter()

class ContactRequest(BaseModel):
    name: str
    email: str
    website: str
    message: str

@router.post("/contact")
async def contact(contact_request: ContactRequest):
    """Handle contact form submission and send confirmation email"""
    # Send consultation confirmation email
    email_sent = email_service.send_consultation_confirmation_email(
        contact_request.email,
        contact_request.name,
        contact_request.message
    )
    
    if not email_sent:
        raise AppException(status_code=500, message="Failed to send confirmation email")
    
    return {"message": "Consultation request received. A confirmation email has been sent."}
