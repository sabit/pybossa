"""Password-based API key retrieval for provisioned local accounts."""

from flask import current_app, g, jsonify, request

from pybossa.core import anonymizer, user_repo
from pybossa.ratelimit import RateLimit


def login_error(status, code, message, fields=None):
    error = dict(code=code, message=message)
    if fields is not None:
        error['fields'] = fields
    return jsonify(error=error), status


def login():
    # Use the same endpoint/IP bucket and headers as the existing API limiter,
    # while preserving this endpoint's JSON error contract.
    scope = anonymizer.ip(request.remote_addr or '127.0.0.1')
    limit = RateLimit('rate-limit/%s/%s/' % (request.endpoint, scope),
                      current_app.config['LIMIT'], current_app.config['PER'],
                      True)
    g._view_rate_limit = limit
    if limit.over_limit:
        return login_error(429, 'rate_limited', 'Too many login attempts.')

    if not request.is_json:
        return login_error(415, 'unsupported_media_type',
                           'Content-Type must be application/json.')
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return login_error(400, 'invalid_json',
                           'The request body must be a valid JSON object.')

    errors = {}
    for field in ('email_addr', 'password'):
        if not isinstance(data.get(field), str) or not data[field]:
            errors[field] = ['A nonempty string is required.']
    if errors:
        return login_error(400, 'validation_failed',
                           'Please correct the supplied fields.', errors)

    if (current_app.config.get('LDAP_HOST') or
            current_app.config.get('ENABLE_TWO_FACTOR_AUTH')):
        return login_error(403, 'unsupported_auth_mode',
                           'This endpoint requires local password login '
                           'without two-factor authentication.')

    user = user_repo.get_by(email_addr=data['email_addr'])
    if user is None or not user.check_password(data['password']):
        return login_error(401, 'invalid_credentials',
                           'Invalid email or password.')

    return jsonify(user=dict(id=user.id, name=user.name,
                             fullname=user.fullname,
                             email_addr=user.email_addr),
                   api_key=user.api_key)


def prevent_login_caching(response):
    if request.endpoint == 'api.api_login':
        response.headers['Cache-Control'] = 'no-store'
    return response
