# Production Security Considerations

This document outlines security considerations for deploying ClauseLens in production.

## Rate Limiting

For production deployment, implement rate limiting to prevent abuse:

### Recommended Limits
- **File uploads**: 10 requests per minute per IP
- **Analysis requests**: 30 requests per minute per IP
- **API calls**: 100 requests per minute per IP

### Implementation Options
1. **Streamlit Cloud**: Use built-in rate limiting
2. **Self-hosted**: Implement with nginx/traefik
3. **Application-level**: Use `slowapi` or similar library

### Example with slowapi
```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/analyze")
@limiter.limit("10/minute")
def analyze_endpoint():
    # Analysis logic
    pass
```

## Error Handling

### Information Disclosure Prevention
The application already:
- Does not expose API keys in logs
- Does not expose file paths in error messages
- Uses generic error messages for file upload failures
- Does not include stack traces in user-facing errors

### Recommended Enhancements
1. **Sanitize error messages**: Ensure no internal paths or configuration details leak
2. **Log security events**: Log failed authentication, rate limit violations, suspicious patterns
3. **Structured logging**: Use JSON logging for easy parsing and analysis

## Prompt Injection Protection

### Current Implementation
- Pattern-based detection in `core/prompt_safety.py`
- Quarantines suspicious passages from rule matching
- Surfaces warnings to human reviewers
- Uses `finditer()` to detect multiple injections in a single segment

### Detected Patterns
- "Ignore previous instructions" variants
- "System prompt" references
- Role-playing attempts ("You are now...")
- Compliance declarations ("This contract is fully compliant")
- Suppression commands ("Do not report...")
- Override attempts ("Override the policy...")

### Limitations
- Pattern-based (can be bypassed with obfuscation)
- Does not detect context-dependent injections
- No LLM-based detection (offline-first design)

### Production Recommendations
1. **Add LLM-based detection**: If using an LLM, add a classifier for injection attempts
2. **User feedback**: Allow users to flag missed injections
3. **Pattern updates**: Regularly update patterns based on attack trends
4. **Sandboxing**: Run LLM calls in isolated environments

## File Upload Security

### Current Implementation
- File size limits (10 MB default)
- PDF/DOCX signature validation
- Extension whitelist
- Path traversal protection
- Empty file rejection

### Production Recommendations
1. **Virus scanning**: Integrate ClamAV or similar for uploaded files
2. **Sandboxed extraction**: Extract files in isolated containers
3. **Temporary storage**: Use secure temp directories with proper permissions
4. **Cleanup**: Implement automatic cleanup of uploaded files

## Input Validation

### Current Implementation
- Pydantic schema validation for all inputs
- Range constraints (0-365 for days)
- Type validation (int, bool, list)
- Non-empty list validation (jurisdictions)

### Production Recommendations
1. **Additional validation**: Add business logic validation
2. **Input sanitization**: Sanitize free-text fields before storage
3. **Length limits**: Add length limits to text fields

## Authentication and Authorization

### Current State
- No authentication (local-first design)
- No multi-tenancy
- No user management

### Production Recommendations
1. **Add authentication**: Integrate OAuth2 or similar
2. **User isolation**: Ensure users can only access their own data
3. **Audit logging**: Log all user actions for compliance
4. **Session management**: Implement secure session handling

## Secrets Management

### Current State
- Environment variables for API keys
- `.env` file excluded from Git
- No secrets in code

### Production Recommendations
1. **Secret manager**: Use AWS Secrets Manager, HashiCorp Vault, or similar
2. **Rotation**: Implement automatic secret rotation
3. **Encryption**: Encrypt secrets at rest
4. **Audit**: Log secret access

## Network Security

### Current State
- Streamlit handles network layer
- No custom network code

### Production Recommendations
1. **HTTPS only**: Enforce TLS 1.2+
2. **CORS**: Configure CORS policies
3. **WAF**: Use a Web Application Firewall
4. **DDoS protection**: Implement DDoS mitigation

## Logging and Monitoring

### Current State
- Application logs to stdout
- No structured logging
- No security event logging

### Production Recommendations
1. **Structured logging**: Use JSON format
2. **Centralized logging**: Send logs to ELK, Splunk, or similar
3. **Metrics**: Add Prometheus metrics for monitoring
4. **Alerting**: Set up alerts for security events

## Dependency Management

### Current State
- `requirements.txt` with pinned versions
- Regular dependency updates not automated

### Production Recommendations
1. **Automated scanning**: Use Dependabot or similar
2. **Regular updates**: Schedule monthly dependency updates
3. **Vulnerability scanning**: Use `safety` or `pip-audit`
4. **SBOM**: Generate Software Bill of Materials

## Compliance Considerations

### Data Privacy
- Contracts may contain sensitive information
- Consider data retention policies
- Implement data deletion on request

### Legal Disclaimer
- The app includes a disclaimer that it is not legal advice
- Ensure this is prominent in the UI
- Consider additional jurisdiction-specific disclaimers

## Deployment Checklist

- [ ] Add rate limiting
- [ ] Implement authentication
- [ ] Set up HTTPS
- [ ] Configure WAF
- [ ] Set up centralized logging
- [ ] Implement secret management
- [ ] Add monitoring and alerting
- [ ] Configure backup strategy
- [ ] Set up CI/CD with security scanning
- [ ] Document incident response procedure
- [ ] Conduct security audit
- [ ] Perform penetration testing
