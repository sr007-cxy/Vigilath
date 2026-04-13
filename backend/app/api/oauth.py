from fastapi import APIRouter
from typing import Optional
from app.models.user import User, Token
from app.services.user_service import user_service
from jose import JWTError, jwt
from datetime import datetime, timedelta
import requests
from app.database import settings
from app.utils.error_handler import AppException

router = APIRouter()

# 第三方登录配置
GOOGLE_CLIENT_ID = settings.GOOGLE_CLIENT_ID
FACEBOOK_APP_ID = settings.FACEBOOK_APP_ID
FACEBOOK_APP_SECRET = settings.FACEBOOK_APP_SECRET
SECRET_KEY = settings.SECRET_KEY

@router.post("/google", response_model=Token)
async def google_login(google_token: str):
    """Google OAuth登录"""
    # 验证Google token
    google_response = requests.get(
        "https://oauth2.googleapis.com/tokeninfo",
        params={"id_token": google_token}
    )
    
    if not google_response.ok:
        raise AppException(status_code=401, message="Invalid Google token")
    
    user_info = google_response.json()
    
    # 验证client_id
    if user_info.get("aud") != GOOGLE_CLIENT_ID:
        raise AppException(status_code=401, message="Invalid Google client ID")
    
    # 查找用户
    user = user_service.get_user_by_email(user_info["email"])
    if not user:
        # 创建新用户
        user = user_service.create_user({
            "email": user_info["email"],
            "name": user_info.get("name", "Google User"),
            "password": ""  # 第三方登录不需要密码
        })
    
    # 生成JWT token
    access_token_expires = timedelta(minutes=30)
    access_token = jwt.encode(
        {"sub": user.email, "exp": datetime.utcnow() + access_token_expires},
        SECRET_KEY,
        algorithm="HS256"
    )
    
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/facebook", response_model=Token)
async def facebook_login(facebook_token: str):
    """Facebook OAuth登录"""
    # 验证Facebook token
    facebook_response = requests.get(
        "https://graph.facebook.com/me",
        params={
            "access_token": facebook_token,
            "fields": "id,email,name"
        }
    )
    
    if not facebook_response.ok:
        raise AppException(status_code=401, message="Invalid Facebook token")
    
    user_info = facebook_response.json()
    
    # 查找用户
    user = user_service.get_user_by_email(user_info["email"])
    if not user:
        # 创建新用户
        user = user_service.create_user({
            "email": user_info["email"],
            "name": user_info.get("name", "Facebook User"),
            "password": ""  # 第三方登录不需要密码
        })
    
    # 生成JWT token
    access_token_expires = timedelta(minutes=30)
    access_token = jwt.encode(
        {"sub": user.email, "exp": datetime.utcnow() + access_token_expires},
        SECRET_KEY,
        algorithm="HS256"
    )
    
    return {"access_token": access_token, "token_type": "bearer"}