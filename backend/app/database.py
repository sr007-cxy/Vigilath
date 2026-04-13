from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./geo_checker.db"
    GOOGLE_CLIENT_ID: str = "test-google-client-id"
    FACEBOOK_APP_ID: str = "test-facebook-app-id"
    FACEBOOK_APP_SECRET: str = "test-facebook-app-secret"
    SECRET_KEY: str = "your-secret-key-for-jwt"
    SMTP_SERVER: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = "your-email@gmail.com"
    SMTP_PASSWORD: str = "your-app-password"
    SENDER_EMAIL: str = "your-email@gmail.com"
    
    class Config:
        env_file = ".env"

settings = Settings()

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()