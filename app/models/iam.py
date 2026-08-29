from sqlalchemy import Column, Integer, String, ForeignKey, Table, Boolean, DateTime, func, UniqueConstraint
from sqlalchemy.orm import relationship
from app.core.database import Base


permission_role = Table(
    'permission_role',
    Base.metadata,
    Column('permission_id', Integer, ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True),
    Column('role_id', Integer, ForeignKey('iam_roles.id', ondelete='CASCADE'), primary_key=True)
)


class Permission(Base):
    __tablename__ = "permissions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    resource_type = Column(String(50), nullable=False, index=True)
    action = Column(String(50), nullable=False)
    description = Column(String(255), nullable=True)

    roles = relationship("Role", secondary=permission_role, back_populates="permissions")

    __table_args__ = (
        UniqueConstraint('resource_type', 'action', name='uq_permission_resource_action'),
    )

    def __repr__(self):
        return f"<Permission {self.resource_type}:{self.action}>"


class Role(Base):
    __tablename__ = "iam_roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False, index=True)
    description = Column(String(255), nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    is_preset = Column(Boolean, default=False)
    is_system = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="roles")
    permissions = relationship("Permission", secondary=permission_role, back_populates="roles")
    user_roles = relationship("UserRole", back_populates="role")

    __table_args__ = (
        UniqueConstraint('name', 'tenant_id', name='uq_role_name_tenant'),
    )

    def __repr__(self):
        return f"<Role {self.name}>"


class UserRole(Base):
    __tablename__ = "user_roles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    role_id = Column(Integer, ForeignKey("iam_roles.id", ondelete="CASCADE"), nullable=False, index=True)
    granted_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", foreign_keys=[user_id], backref="user_roles")
    tenant = relationship("Tenant", back_populates="user_roles")
    role = relationship("Role", back_populates="user_roles")
    granter = relationship("User", foreign_keys=[granted_by])

    __table_args__ = (
        UniqueConstraint('user_id', 'tenant_id', 'role_id', name='uq_user_tenant_role'),
    )

    def __repr__(self):
        return f"<UserRole user={self.user_id} role={self.role_id} tenant={self.tenant_id}>"
