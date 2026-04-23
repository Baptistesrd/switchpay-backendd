import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
import httpx

from backend.db.db_utils import create_user, get_user_by_email
from backend.security.auth import (
    hash_password,
    verify_password,
    create_access_token,
    generate_api_key,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    org_name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class OAuthRequest(BaseModel):
    code: str


def _auth_response(user: dict) -> dict:
    token = create_access_token({
        "sub": user["id"],
        "email": user["email"],
        "org": user["org_name"],
    })
    return {"access_token": token, "api_key": user["api_key"]}


@router.post("/register")
def register(body: RegisterRequest):
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    if get_user_by_email(body.email):
        raise HTTPException(409, "Email already registered")
    user = create_user(
        email=body.email,
        org_name=body.org_name,
        password_hash=hash_password(body.password),
        api_key=generate_api_key(),
    )
    return _auth_response(user)


@router.post("/login")
def login(body: LoginRequest):
    user = get_user_by_email(body.email)
    if not user or not user.get("password_hash"):
        raise HTTPException(401, "Invalid credentials")
    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    return _auth_response(user)


@router.post("/google")
async def google_oauth(body: OAuthRequest):
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": body.code,
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "redirect_uri": os.environ["OAUTH_REDIRECT_URI"],
                "grant_type": "authorization_code",
            },
        )
        if token_res.status_code != 200:
            raise HTTPException(400, "Google token exchange failed")
        tokens = token_res.json()

        profile_res = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        profile = profile_res.json()

    email = profile.get("email")
    if not email:
        raise HTTPException(400, "Could not retrieve email from Google")

    user = get_user_by_email(email)
    if not user:
        user = create_user(
            email=email,
            org_name=profile.get("name", email.split("@")[0]),
            password_hash=None,
            api_key=generate_api_key(),
            provider="google",
        )
    return _auth_response(user)


@router.post("/github")
async def github_oauth(body: OAuthRequest):
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": os.environ["GITHUB_CLIENT_ID"],
                "client_secret": os.environ["GITHUB_CLIENT_SECRET"],
                "code": body.code,
                "redirect_uri": os.environ["OAUTH_REDIRECT_URI"],
            },
            headers={"Accept": "application/json"},
        )
        tokens = token_res.json()
        gh_token = tokens.get("access_token")
        if not gh_token:
            raise HTTPException(400, "GitHub token exchange failed")

        user_res = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {gh_token}"},
        )
        gh_user = user_res.json()
        email = gh_user.get("email")

        if not email:
            emails_res = await client.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"Bearer {gh_token}"},
            )
            emails = emails_res.json()
            email = next((e["email"] for e in emails if e.get("primary")), None)

    if not email:
        raise HTTPException(400, "Could not retrieve email from GitHub")

    user = get_user_by_email(email)
    if not user:
        user = create_user(
            email=email,
            org_name=gh_user.get("login", email.split("@")[0]),
            password_hash=None,
            api_key=generate_api_key(),
            provider="github",
        )
    return _auth_response(user)
