from urllib.parse import urlparse
import re

def validate_url(url: str) -> bool:
    """Validate URL format"""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

def sanitize_url(url: str) -> str:
    """Sanitize URL to prevent injection attacks"""
    # Remove any potentially malicious characters
    sanitized = re.sub(r'[^a-zA-Z0-9:/\-._?&=]', '', url)
    # Ensure URL has a scheme
    if not sanitized.startswith(('http://', 'https://')):
        sanitized = 'https://' + sanitized
    return sanitized

def validate_email(email: str) -> bool:
    """Validate email format"""
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(email_regex, email) is not None

def sanitize_string(input_str: str) -> str:
    """Sanitize string to prevent XSS attacks"""
    # Remove HTML tags
    sanitized = re.sub(r'<[^>]+>', '', input_str)
    # Escape special characters
    sanitized = sanitized.replace('&', '&amp;')
    sanitized = sanitized.replace('<', '&lt;')
    sanitized = sanitized.replace('>', '&gt;')
    sanitized = sanitized.replace('"', '&quot;')
    sanitized = sanitized.replace("'", '&#39;')
    return sanitized
