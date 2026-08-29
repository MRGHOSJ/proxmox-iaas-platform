import logging
import secrets
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.user import User
from app.models.tenant import Tenant
from app.models.iam import Role, UserRole
from app.models.invitation import Invitation
from app.core.audit import log_audit_event, AUDIT_ACTIONS
from app.core.security import hash_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invitations", tags=["Invitations"])


class InvitationValidateResponse(BaseModel):
    valid: bool
    email: str
    tenant_name: Optional[str] = None
    tenant_id: int
    role_id: Optional[int] = None
    role_name: str
    expires_at: datetime
    is_existing_user: bool


class InvitationAcceptRequest(BaseModel):
    token: str
    username: Optional[str] = None
    password: str
    full_name: Optional[str] = None


class InvitationAcceptResponse(BaseModel):
    success: bool
    message: str
    is_existing_user: bool
    tenant_id: int
    username: str


@router.get("/validate/{token}", response_model=InvitationValidateResponse)
def validate_invitation(
    token: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Validate an invitation token without using it. Public endpoint.
    """
    invitation = db.query(Invitation).filter(
        Invitation.token == token,
        Invitation.is_used == False
    ).first()
    
    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid invitation token"
        )
    
    if invitation.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation has expired"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == invitation.tenant_id).first()
    role = None
    if invitation.role_id:
        role = db.query(Role).filter(Role.id == invitation.role_id).first()
    
    existing_user = db.query(User).filter(User.email == invitation.email).first()
    is_existing_user = existing_user is not None
    
    return InvitationValidateResponse(
        valid=True,
        email=invitation.email,
        tenant_name=tenant.name if tenant else None,
        tenant_id=invitation.tenant_id,
        role_id=invitation.role_id,
        role_name=role.name if role else "Viewer",
        expires_at=invitation.expires_at,
        is_existing_user=is_existing_user
    )


@router.get("/{token}", response_model=InvitationValidateResponse)
def get_invitation(
    token: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Get invitation details by token. Public endpoint.
    Alias for /validate/{token}.
    """
    invitation = db.query(Invitation).filter(
        Invitation.token == token,
        Invitation.is_used == False
    ).first()
    
    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid invitation token"
        )
    
    if invitation.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation has expired"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == invitation.tenant_id).first()
    role = None
    if invitation.role_id:
        role = db.query(Role).filter(Role.id == invitation.role_id).first()
    
    existing_user = db.query(User).filter(User.email == invitation.email).first()
    is_existing_user = existing_user is not None
    
    return InvitationValidateResponse(
        valid=True,
        email=invitation.email,
        tenant_name=tenant.name if tenant else None,
        tenant_id=invitation.tenant_id,
        role_id=invitation.role_id,
        role_name=role.name if role else "Viewer",
        expires_at=invitation.expires_at,
        is_existing_user=is_existing_user
    )


@router.post("/accept", response_model=InvitationAcceptResponse)
def accept_invitation(
    data: InvitationAcceptRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Accept an invitation. Creates new account or adds existing user to tenant. Public endpoint.
    """
    request_id = request.headers.get("X-Request-ID", secrets.token_urlsafe(16))
    
    invitation = db.query(Invitation).filter(
        Invitation.token == data.token,
        Invitation.is_used == False
    ).first()
    
    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired invitation"
        )
    
    if invitation.expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation has expired"
        )
    
    existing_user = db.query(User).filter(User.email == invitation.email).first()
    
    if existing_user:
        # Check if user already has access to this tenant
        existing_user_role = db.query(UserRole).filter(
            UserRole.user_id == existing_user.id,
            UserRole.tenant_id == invitation.tenant_id
        ).first()
        
        if existing_user_role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You already have access to this tenant"
            )
        
        # Add existing user to the new tenant
        if invitation.role_id:
            user_role = UserRole(
                user_id=existing_user.id,
                tenant_id=invitation.tenant_id,
                role_id=invitation.role_id,
                granted_by=invitation.invited_by
            )
            db.add(user_role)
        else:
            # Assign viewer role
            from app.core.iam.seed import assign_viewer_role
            assign_viewer_role(db, existing_user, invitation.tenant_id)
        
        invitation.is_used = True
        db.commit()
        
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["INVITE_ACCEPT"],
            target_type="invitation",
            actor_id=existing_user.id,
            actor_username=existing_user.username,
            details=f"Existing user accepted invitation to tenant {invitation.tenant_id}",
            request_id=request_id,
            tenant_id=invitation.tenant_id
        )
        
        logger.info(f"Existing user {existing_user.username} added to tenant {invitation.tenant_id} via invitation (request_id={request_id})")
        return InvitationAcceptResponse(
            success=True,
            message="You have been added to the tenant",
            is_existing_user=True,
            tenant_id=invitation.tenant_id,
            username=existing_user.username
        )
    
    # New user - create account
    if not data.username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username is required for new users"
        )
    
    existing_username = db.query(User).filter(User.username == data.username).first()
    if existing_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )
    
    try:
        hashed_password = hash_password(data.password)
        
        new_user = User(
            username=data.username,
            email=invitation.email,
            full_name=data.full_name,
            hashed_password=hashed_password,
            tenant_id=invitation.tenant_id
        )
        db.add(new_user)
        db.flush()
        
        if invitation.role_id:
            user_role = UserRole(
                user_id=new_user.id,
                tenant_id=invitation.tenant_id,
                role_id=invitation.role_id,
                granted_by=invitation.invited_by
            )
            db.add(user_role)
        else:
            from app.core.iam.seed import assign_viewer_role
            assign_viewer_role(db, new_user, invitation.tenant_id)
        
        invitation.is_used = True
        db.commit()
        db.refresh(new_user)
        
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["INVITE_ACCEPT"],
            target_type="invitation",
            actor_id=new_user.id,
            actor_username=new_user.username,
            details=f"New user accepted invitation to tenant {invitation.tenant_id}",
            request_id=request_id,
            tenant_id=invitation.tenant_id
        )
        
        logger.info(f"User {data.username} accepted invitation to tenant {invitation.tenant_id} (request_id={request_id})")
        return InvitationAcceptResponse(
            success=True,
            message="Account created successfully",
            is_existing_user=False,
            tenant_id=invitation.tenant_id,
            username=new_user.username
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Accept invitation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create account"
        )
