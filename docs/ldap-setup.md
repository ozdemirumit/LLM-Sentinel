# LDAP / Active Directory Setup

## Prerequisites

- LDAP/AD server accessible from the proxy
- Service account with read access to user/group objects
- Security groups for admin and operator roles

## Configuration (.env)

```
LDAP_ENABLED=true
LDAP_SERVER=ldap://dc.example.com:389
LDAP_BASE_DN=DC=example,DC=com
LDAP_BIND_DN=CN=svc-llmsentinel,OU=ServiceAccounts,DC=example,DC=com
LDAP_BIND_PASSWORD=vault://secret/ldap-bind-password
LDAP_USER_FILTER=(sAMAccountName={username})
LDAP_GROUP_FILTER=(member={dn})
LDAP_ADMIN_GROUP=CN=LLM-Sentinel-Admins,OU=Groups,DC=example,DC=com
LDAP_OPERATOR_GROUP=CN=LLM-Sentinel-Operators,OU=Groups,DC=example,DC=com
LDAP_USE_SSL=true
LDAP_VERIFY_CERT=true
LDAP_CACHE_TTL_SECONDS=300
```

## AD Group Setup

1. Create `LLM-Sentinel-Admins` security group
2. Create `LLM-Sentinel-Operators` security group
3. Add users to appropriate groups
4. Users not in either group get `viewer` role

## Testing

```bash
# Test with ldp.exe (Windows) or ldapsearch (Linux)
ldapsearch -H ldap://dc.example.com -D "CN=svc-llmsentinel,..." -w "password" \
  -b "DC=example,DC=com" "(sAMAccountName=testuser)"
```

## Login

Use the LDAP login endpoint or Admin UI LDAP toggle:
```
POST /v1/auth/ldap-login
{"username": "jdoe", "password": "..."}
```
