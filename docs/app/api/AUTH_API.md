# Authentication & User Management API Documentation

This module handles user registration, login, session management using JWT, and comprehensive user management capabilities.

## Table of Contents
- [Endpoints](#endpoints)
- [User Management](#user-management)
- [Security Flow](#security-flow)
- [Email Domain Validation](#email-domain-validation)
- [Logging](#logging)

---

## Endpoints

### 1. Register User (with Tenant)

Registers a new user along with a new tenant organization. This is the primary registration endpoint.

**Endpoint:** `POST /auth/register`  
**Status Code:** `201 Created`

**Security:** Rate limited. Requires email domain whitelist (if configured).

**Request Body:**
```json
{
  "username": "johndoe",
  "email": "john@acmecorp.com",
  "full_name": "John Doe",
  "password": "securepassword123",
  "tenant_name": "Acme Corp",
  "tenant_slug": "acme-corp"
}
```

**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `username` | string | Yes | Unique username |
| `email` | string | Yes | Unique email address |
| `full_name` | string | No | User's full name |
| `password` | string | Yes | Plain text password |
| `tenant_name` | Yes | Organization name |
| `tenant_slug` | No | URL-friendly slug (auto-generated if not provided) |

**Logic Flow:**
1. Check if registration is enabled (`ALLOW_REGISTRATION`)
2. Validate email domain against allowed list
3. Check username/email uniqueness
4. Check tenant name/slug uniqueness
5. Create new Tenant (pending verification)
6. Create new User with tenant assignment
7. Assign tenant_admin role to user
8. Return created user

**Success Response:**
```json
{
  "id": 1,
  "username": "johndoe",
  "email": "john@acmecorp.com",
  "full_name": "John Doe",
  "role": "tenant_admin",
  "is_active": true,
  "tenant_id": 1
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | Username, email, or tenant name already exists |
| `403 Forbidden` | Public registration disabled |
| `429 Too Many Requests` | Rate limit exceeded |

---

### 2. Login

Authenticates a user and returns a JWT access token.

**Endpoint:** `POST /auth/login`  
**Status Code:** `200 OK`

**Security:** Rate limited to prevent brute-force attacks.

**Request Body:**
```json
{
  "username": "johndoe",
  "password": "securepassword123"
}
```

**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `username` | string | Yes | The username |
| `password` | string | Yes | The password |

**Success Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

**JWT Token Payload:**
```json
{
  "sub": "1",
  "tenant_id": 1,
  "exp": 1234567890,
  "iat": 1234567890,
  "jti": "unique-token-id"
}
```

**Logic Flow:**
1. Verify rate limit
2. Look up user by username
3. Verify password
4. Check user is active
5. Check tenant is verified (or user is super_admin)
6. Create JWT with user_id and tenant_id
7. Log audit event

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `401 Unauthorized` | Incorrect username or password |
| `403 Forbidden` | User inactive or tenant not verified |
| `429 Too Many Requests` | Rate limit exceeded |

---

### 3. Logout

Invalidates the current access token by adding it to the blacklist.

**Endpoint:** `POST /auth/logout`  
**Status Code:** `204 No Content`

**Authorization:** Bearer Token Required

**Headers:**
```
Authorization: Bearer <access_token>
```

**Logic Flow:**
1. Extract token from header
2. Decode JWT to get `jti` and `exp`
3. Add `jti` to Redis blacklist
4. Log audit event

---

### 4. Get Current User (Me)

Retrieves the profile of the authenticated user including IAM roles.

**Endpoint:** `GET /auth/me`  
**Status Code:** `200 OK`

**Authorization:** Bearer Token Required

**Success Response:**
```json
{
  "id": 1,
  "username": "johndoe",
  "email": "john@acmecorp.com",
  "full_name": "John Doe",
  "is_active": true,
  "tenant_id": 1,
  "iam_roles": [
    {
      "id": 1,
      "name": "tenant_admin",
      "description": "Tenant Administrator",
      "is_system": false,
      "is_preset": true,
      "tenant_id": 1,
      "permissions": ["vm:create", "vm:delete", "vm:read", "vm:update", "network:create", "network:read"]
    }
  ]
}
```

---

### 5. Update Profile

Updates the authenticated user's profile.

**Endpoint:** `PATCH /auth/me`  
**Status Code:** `200 OK`

**Authorization:** Bearer Token Required

**Request Body:**
```json
{
  "email": "john.new@acmecorp.com",
  "full_name": "John Smith"
}
```

**Updatable Fields:**
- `email` - Must be unique
- `full_name`

**Success Response:** Returns updated user object.

---

### 6. Change Password

Changes the authenticated user's password.

**Endpoint:** `POST /auth/me/change-password`  
**Status Code:** `204 No Content`

**Authorization:** Bearer Token Required

**Request Body:**
```json
{
  "current_password": "oldpassword123",
  "new_password": "newpassword456"
}
```

**Logic Flow:**
1. Verify current password
2. Hash new password with bcrypt
3. Update user record
4. Log audit event

---

## User Management (Admin)

All user management endpoints require **Super Admin** role.

### 7. List All Users

**Endpoint:** `GET /tenants/users`  
**Authorization:** Super Admin only

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `skip` | integer | `0` | Pagination offset |
| `limit` | integer | `20` | Results limit (1-100) |
| `search` | string | `null` | Search by username/email/full_name |
| `tenant_id` | integer | `null` | Filter by tenant |
| `is_active` | boolean | `null` | Filter by active status |

**Success Response:** `200 OK`
```json
{
  "total": 15,
  "users": [
    {
      "id": 1,
      "username": "admin",
      "email": "admin@cloud.local",
      "full_name": "Default Administrator",
      "is_active": true,
      "tenant_id": 1,
      "tenant_name": "System",
      "roles": ["super_admin"],
      "tenant_memberships": [],
      "is_super_admin": true
    }
  ]
}
```

---

### 8. Ban/Unban User

Bans or unbans a user.

**Endpoint:** `PATCH /tenants/users/{user_id}/ban`  
**Authorization:** Super Admin only

**Success Response:** `200 OK`
```json
{
  "id": 5,
  "username": "baduser",
  "is_active": false,
  "message": "User banned successfully"
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | Cannot ban a super admin |
| `404 Not Found` | User not found |

---

### 9. Get Tenant Users

Gets all users in a specific tenant.

**Endpoint:** `GET /tenants/{tenant_id}/users`  
**Authorization:** Super Admin only

**Success Response:** `200 OK`
```json
[
  {
    "id": 1,
    "username": "johndoe",
    "email": "john@acmecorp.com",
    "full_name": "John Doe",
    "is_active": true,
    "created_at": "2026-04-23T10:00:00Z",
    "roles": ["tenant_admin"]
  }
]
```

---

## Security Flow

### Password Security
- Passwords are **never** stored in plain text
- All passwords are hashed using **bcrypt**
- Password verification uses constant-time comparison

### Token Strategy
- **Type:** JWT (JSON Web Token)
- **Scheme:** OAuth2 Password Bearer
- **Header:** `Authorization: Bearer <token>`

**Token Claims:**
| Claim | Description |
|-------|-------------|
| `sub` | User's database ID |
| `tenant_id` | User's primary tenant |
| `exp` | Expiration timestamp |
| `iat` | Issued at timestamp |
| `jti` | Unique token ID for blacklisting |

### Token Blacklisting
- Upon logout, token's `jti` is stored in Redis blacklist
- Blacklisted tokens are rejected before natural expiration
- Blacklist entry auto-expires after token's `exp`

### Email Domain Validation
- If `ALLOWED_EMAIL_DOMAINS` is configured, only those domains are allowed
- Registration rejected for unauthorized domains

### Rate Limiting
- Login/Registration endpoints are rate limited
- Default: 5 requests per 60 seconds
- Use Redis for distributed rate limiting in production

---

## Email Domain Validation

### Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ALLOWED_EMAIL_DOMAINS` | string | `null` | Comma-separated allowed domains |

**Example:**
```
ALLOWED_EMAIL_DOMAINS=acmecorp.com,example.com
```

### Behavior
- If not configured, any email domain is allowed
- If configured, only listed domains permitted
- Check is case-insensitive

### Error Response
```json
{
  "detail": "Email domain 'gmail.com' is not allowed. Allowed domains: acmecorp.com, example.com"
}
```
Status: `400 Bad Request`

---

## Role-Based Access Control (IAM)

### Super Admin Role
- System-wide access (tenant_id=NULL in UserRole)
- Can access all tenants
- Cannot access tenant VM logs (privacy protection)
- Granted via IAM seed, not tenant roles

### Tenant Roles
| Role | Description | Permissions |
|------|-------------|-------------|
| `tenant_admin` | Tenant administrator | All VM/network/firewall operations |
| `vm_operator` | VM operator | Create/manage own VMs |
| `viewer` | Read-only | View resources only |

### Permission Matrix

| Endpoint | Super Admin | Tenant Admin | VM Operator | Viewer |
|----------|:----------:|:-----------:|:-----------:|:------:|
| `POST /auth/register` | ✅ | ✅ | ✅ | ✅ |
| `POST /auth/login` | ✅ | ✅ | ✅ | ✅ |
| `POST /auth/logout` | ✅ | ✅ | ✅ | ✅ |
| `GET /auth/me` | ✅ | ✅ | ✅ | ✅ |
| `PATCH /auth/me` | ✅ | ✅ | ✅ | ❌ |
| `GET /tenants/users` | ✅ | ❌ | ❌ | ❌ |
| `PATCH /tenants/users/{id}/ban` | ✅ | ❌ | ❌ | ❌ |

---

## Logging

### Authentication Events
- Registration attempts (success/failure)
- Login attempts (success/failure)
- Login failures (incorrect password, user inactive, tenant not verified)
- Logouts (token blacklisting)

### Audit Events
- User creation
- Role changes
- Account activation/deactivation
- Password changes
- Profile updates

### Log Format
```
INFO: Registration attempt for username: johndoe (request_id=abc123)
INFO: User registered successfully with new tenant: acme-corp (ID: 5)
WARNING: Login failed: Tenant not verified for 'johndoe' (request_id=abc124)
INFO: User johndoe logged out (request_id=abc125)
```

---

## Error Handling

### Common Errors

**1. Registration Disabled**
```json
{
  "detail": "Public registration is disabled. Contact an administrator."
}
```
Status: `403 Forbidden`

**2. Email Domain Not Allowed**
```json
{
  "detail": "Email domain 'gmail.com' is not allowed. Allowed domains: acmecorp.com"
}
```
Status: `400 Bad Request`

**3. Incorrect Credentials**
```json
{
  "detail": "Incorrect username or password",
  "headers": {"WWW-Authenticate": "Bearer"}
}
```
Status: `401 Unauthorized`

**4. User Inactive**
```json
{
  "detail": "Inactive user"
}
```
Status: `403 Forbidden`

**5. Tenant Not Verified**
```json
{
  "detail": "Your tenant is pending verification. Contact an administrator."
}
```
Status: `403 Forbidden`

**6. Super Admin Required**
```json
{
  "detail": "Super admin access required"
}
```
Status: `403 Forbidden`

**7. Cannot Ban Super Admin**
```json
{
  "detail": "Cannot ban a super admin"
}
```
Status: `400 Bad Request`