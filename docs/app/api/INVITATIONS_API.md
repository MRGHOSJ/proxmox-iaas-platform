# Invitations API Documentation

This module handles tenant user invitations for inviting external users to join a tenant organization.

## Table of Contents
- [Invitation Model](#invitation-model)
- [Endpoints](#endpoints)

---

## Invitation Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique identifier |
| `email` | string | Invited email address |
| `tenant_id` | integer | Inviting tenant |
| `role_id` | integer | Role to assign |
| `token` | string | Unique invitation token |
| `is_used` | boolean | Whether invitation was accepted |
| `expires_at` | datetime | Expiration timestamp |
| `created_at` | datetime | Creation timestamp |
| `created_by` | integer | User who created invitation |

---

## Authorization

| Action | Required Permission |
|--------|-----------------|
| Create invitation | `tenant:admin` |
| List invitations | `tenant:admin` |
| Revoke invitation | `tenant:admin` |
| Validate/Accept | None (public with token) |

---

## Endpoints

### 1. Validate Invitation

Validates an invitation token without using it.

**Endpoint:** `GET /invitations/validate/{token}`  
**Authorization:** Public (no auth required)

**Success Response:** `200 OK`
```json
{
  "valid": true,
  "email": "john@example.com",
  "tenant_name": "Acme Corp",
  "tenant_id": 1,
  "role_id": 2,
  "role_name": "vm_operator",
  "expires_at": "2026-04-30T12:00:00Z",
  "is_existing_user": false
}
```

---

### 2. Accept Invitation

Accepts an invitation and creates user account.

**Endpoint:** `POST /invitations/accept`  
**Authorization:** Public (no auth required)

**Request Body:**
```json
{
  "token": "invitation-token-abc123",
  "username": "newuser",
  "password": "securepassword123",
  "full_name": "John Doe"
}
```

**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `token` | string | Yes | Invitation token |
| `username` | string | Yes* | *Required if new user |
| `password` | string | Yes | Account password |
| `full_name` | string | No | Full name |

**Success Response:** `200 OK`
```json
{
  "success": true,
  "message": "Invitation accepted successfully",
  "is_existing_user": false,
  "tenant_id": 1,
  "username": "newuser"
}
```

---

### 3. Create Invitation

Creates a new invitation.

**Endpoint:** `POST /invitations/`  
**Authorization:** Tenant Admin

**Request Body:**
```json
{
  "email": "john@example.com",
  "role_id": 2,
  "expires_in_days": 7
}
```

**Parameters:**
| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `email` | string | Yes | Email to invite |
| `role_id` | integer | Yes | Role to assign |
| `expires_in_days` | integer | 7 | Days until expiry |

**Success Response:** `201 Created`
```json
{
  "id": 1,
  "email": "john@example.com",
  "tenant_id": 1,
  "role_id": 2,
  "token": "inv-abc123def456",
  "expires_at": "2026-04-30T12:00:00Z"
}
```

---

### 4. List Invitations

Lists all invitations for a tenant.

**Endpoint:** `GET /invitations/`  
**Authorization:** Tenant Admin

**Success Response:** `200 OK`
```json
[
  {
    "id": 1,
    "email": "john@example.com",
    "role_name": "vm_operator",
    "is_used": false,
    "expires_at": "2026-04-30T12:00:00Z",
    "created_at": "2026-04-23T12:00:00Z"
  }
]
```

---

### 5. Revoke Invitation

Revokes an invitation.

**Endpoint:** `DELETE /invitations/{invitation_id}`  
**Authorization:** Tenant Admin

**Success Response:** `204 No Content`

---

## Invitation Flow

### Flow for New Users
1. Tenant admin creates invitation
2. System generates unique token
3. Invitation email sent to user (external)
4. User clicks link with token
5. User validates invitation (sees tenant info)
6. User creates account and accepts
7. User gains role in tenant

### Flow for Existing Users
1. Tenant admin creates invitation for existing user email
2. System finds existing user in tenant
3. User validates invitation
4. User accepts (no account creation needed)
5. User gains additional role in tenant

---

## Error Handling

### Common Errors

**1. Invalid Token**
```json
{
  "detail": "Invalid or expired invitation token"
}
```
Status: `400 Bad Request`

**2. Token Already Used**
```json
{
  "detail": "Invitation has already been used"
}
```
Status: `400 Bad Request`

**3. Token Expired**
```json
{
  "detail": "Invitation has expired"
}
```
Status: `400 Bad Request`

**4. Email Already in Tenant**
```json
{
  "detail": "User with this email already exists in tenant"
}
```
Status: `400 Bad Request`

**5. Permission Denied**
```json
{
  "detail": "Tenant admin access required"
}
```
Status: `403 Forbidden`