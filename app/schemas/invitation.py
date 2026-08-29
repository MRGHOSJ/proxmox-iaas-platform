from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator
from datetime import datetime
import re


class InvitationCreate(BaseModel):
    email: EmailStr
    role_id: Optional[int] = None


class InvitationResponse(BaseModel):
    id: int
    email: str
    tenant_id: int
    role_id: Optional[int] = None
    token: str
    invited_by: Optional[int] = None
    is_used: bool
    expires_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class InvitationAccept(BaseModel):
    token: str
    username: str
    password: str
    full_name: Optional[str] = None
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        if len(v) > 128:
            raise ValueError('Password must be less than 128 characters')
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not re.search(r'[a-z]', v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not re.search(r'\d', v):
            raise ValueError('Password must contain at least one digit')
        return v


class TenantCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    
    @field_validator('slug', mode='before')
    @classmethod
    def generate_slug(cls, v):
        if not v:
            return None
        return v.lower().replace(" ", "-")


class UserRegisterWithTenant(BaseModel):
    username: str
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    tenant_name: str
    tenant_slug: Optional[str] = None
    
    @field_validator('password')
    @classmethod
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        if len(v) > 128:
            raise ValueError('Password must be less than 128 characters')
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not re.search(r'[a-z]', v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not re.search(r'\d', v):
            raise ValueError('Password must contain at least one digit')
        return v
    
    @field_validator('tenant_slug', mode='before')
    @classmethod
    def generate_tenant_slug(cls, v):
        if not v:
            return None
        return v.lower().replace(" ", "-")
