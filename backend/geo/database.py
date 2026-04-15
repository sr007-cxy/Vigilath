from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./data/geo_checker.db"
    GOOGLE_CLIENT_ID: str = "test-google-client-id"
    FACEBOOK_APP_ID: str = "test-facebook-app-id"
    FACEBOOK_APP_SECRET: str = "test-facebook-app-secret"
    SECRET_KEY: str = "your-secret-key-for-jwt"
    SMTP_SERVER: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = "your-email@gmail.com"
    SMTP_PASSWORD: str = "your-app-password"
    SENDER_EMAIL: str = "your-email@gmail.com"

    # Stripe (used for overseas / English-locale credit card subscriptions)
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_SUCCESS_URL: str = "http://localhost:5173/checkout/success"
    STRIPE_CANCEL_URL: str = "http://localhost:5173/checkout/cancel"

    # AI engine keys (paid detection modes — entity / ai-visibility / citation-check).
    # Read by both the backend `geo` package and the standalone CLI tool spawned
    # as a subprocess; declared here so pydantic-settings won't drop them as
    # `extra` and so callers can use either `settings.OPENAI_API_KEY` or
    # `os.environ.get('OPENAI_API_KEY')` (systemd EnvironmentFile injects them
    # into the process environment).
    OPENAI_API_KEY: str = ""
    PERPLEXITY_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()