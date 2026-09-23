"""HTTP contract tests using the real app and password hashing, without a DB."""

import json
from unittest import TestCase
from unittest.mock import patch

from default import flask_app
from pybossa.model.user import User


class TestLoginAPI(TestCase):
    def setUp(self):
        self.config = patch.dict(flask_app.config, {
            'WTF_CSRF_ENABLED': True,
            'LDAP_HOST': False,
            'ENABLE_TWO_FACTOR_AUTH': False,
            'ACCOUNT_CONFIRMATION_DISABLED': False,
        })
        self.config.start()
        self.addCleanup(self.config.stop)
        self.client = flask_app.test_client()
        self.user = User(id=7, name='contributor', fullname='Example Contributor',
                         email_addr='contributor@example.org',
                         api_key='existing-contributor-key')
        with flask_app.app_context():
            self.user.set_password('correct-password')
        self.repo_patch = patch('pybossa.api.login.user_repo')
        self.repo = self.repo_patch.start()
        self.addCleanup(self.repo_patch.stop)
        self.repo.get_by.return_value = self.user
        # Exercise the real RateLimit implementation with an isolated pipeline.
        self.redis_patch = patch('pybossa.ratelimit.sentinel')
        self.redis = self.redis_patch.start()
        self.addCleanup(self.redis_patch.stop)
        self.pipeline = self.redis.master.pipeline.return_value
        self.pipeline.execute.return_value = [1, True]

    def post(self, payload=None, **kwargs):
        if payload is None:
            payload = dict(email_addr=self.user.email_addr,
                           password='correct-password')
        return self.client.post('/api/auth/login', data=json.dumps(payload),
                                content_type='application/json', **kwargs)

    def assert_private(self, response):
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers.getlist('Set-Cookie'), [])
        self.assertNotIn('Location', response.headers)
        self.assertNotIn('correct-password', response.get_data(as_text=True))
        self.assertNotIn(self.user.passwd_hash, response.get_data(as_text=True))

    def test_login_returns_only_identity_and_existing_key_without_session(self):
        for _ in range(2):
            response = self.post()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), {
                'user': dict(id=7, name='contributor',
                             fullname='Example Contributor',
                             email_addr='contributor@example.org'),
                'api_key': 'existing-contributor-key',
            })
            self.assert_private(response)
        self.repo.get_by.assert_called_with(email_addr=self.user.email_addr)
        self.repo.update.assert_not_called()
        self.repo.save.assert_not_called()

    def test_invalid_credentials_have_the_same_error(self):
        responses = [self.post(dict(email_addr=self.user.email_addr,
                                    password='wrong-password'))]
        self.repo.get_by.return_value = None
        responses.append(self.post())
        self.user.passwd_hash = None
        self.repo.get_by.return_value = self.user
        responses.append(self.post())
        for response in responses:
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.get_json(), {
                'error': {'code': 'invalid_credentials',
                          'message': 'Invalid email or password.'}})
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertNotIn('api_key', response.get_json())

    def test_ambient_credentials_do_not_bypass_password_or_select_identity(self):
        with self.client.session_transaction() as session:
            session['_user_id'] = 'different-user'
            session['_fresh'] = True
        with patch('pybossa.core.user_repo') as ambient_repo:
            response = self.post(dict(email_addr=self.user.email_addr,
                                      password='wrong-password'),
                                 headers={'Authorization': 'some-valid-api-key'},
                                 query_string={'api_key': 'another-key'})
            self.assertEqual(response.status_code, 401)
            response = self.post(headers={'Authorization': 'some-valid-api-key'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['user']['id'], 7)
            ambient_repo.get_by.assert_not_called()

    def test_invalid_fields(self):
        for value in (None, '', 0, True, [], {}):
            for field in ('email_addr', 'password'):
                payload = dict(email_addr=self.user.email_addr,
                               password='correct-password')
                payload[field] = value
                response = self.post(payload)
                self.assertEqual(response.status_code, 400)
                error = response.get_json()['error']
                self.assertEqual(error['code'], 'validation_failed')
                self.assertIn(field, error['fields'])
                self.assert_private(response)
        response = self.post({})
        self.assertEqual(set(response.get_json()['error']['fields']),
                         {'email_addr', 'password'})
        self.repo.get_by.assert_not_called()

    def test_malformed_and_non_object_json(self):
        for data in ('', '{', 'null', '[]', '"text"', 'false', '1'):
            response = self.client.post('/api/auth/login', data=data,
                                        content_type='application/json')
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.get_json()['error']['code'], 'invalid_json')
            self.assert_private(response)
        self.repo.get_by.assert_not_called()

    def test_media_type_and_charset(self):
        for content_type in ('text/plain', 'application/x-www-form-urlencoded'):
            response = self.client.post('/api/auth/login', data='{}',
                                        content_type=content_type)
            self.assertEqual(response.status_code, 415)
            self.assertEqual(response.get_json()['error']['code'],
                             'unsupported_media_type')
            self.assert_private(response)
        response = self.client.post('/api/auth/login',
                                    data=json.dumps(dict(email_addr=self.user.email_addr,
                                                         password='correct-password')),
                                    content_type='application/json; charset=utf-8')
        self.assertEqual(response.status_code, 200)
        self.assert_private(response)

    def test_unsupported_modes_do_not_check_password_or_return_key(self):
        for mode, value in (('LDAP_HOST', 'ldap.example.org'),
                            ('ENABLE_TWO_FACTOR_AUTH', True)):
            with patch.dict(flask_app.config, {mode: value}):
                response = self.post()
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.get_json()['error']['code'],
                             'unsupported_auth_mode')
            self.assert_private(response)
        self.repo.get_by.assert_not_called()

    def test_rate_limit_uses_existing_headers_and_new_error_envelope(self):
        self.pipeline.execute.return_value = [flask_app.config['LIMIT'], True]
        response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()['error']['code'], 'rate_limited')
        self.assertEqual(response.headers['X-RateLimit-Limit'],
                         str(flask_app.config['LIMIT']))
        self.assertEqual(response.headers['X-RateLimit-Remaining'], '0')
        self.assertGreater(int(response.headers['X-RateLimit-Reset']), 0)
        self.assert_private(response)
        self.repo.get_by.assert_not_called()

    def test_key_authentication_still_works_on_existing_api(self):
        key = self.post().get_json()['api_key']
        from flask_login import current_user
        with patch('pybossa.core.user_repo') as repo:
            repo.get_by.return_value = self.user
            with flask_app.test_request_context('/api/user/7',
                                                 headers={'Authorization': key}):
                flask_app.preprocess_request()
                self.assertEqual(current_user.id, 7)
                repo.get_by.assert_called_once_with(api_key=key)

    def test_existing_web_login_still_sets_session_and_checks_csrf(self):
        with patch('pybossa.view.account.user_repo') as repo:
            repo.get_by.return_value = self.user
            response = self.client.post('/account/signin',
                                        data=json.dumps(dict(email=self.user.email_addr,
                                                             password='correct-password')),
                                        content_type='application/json')
            self.assertEqual(response.status_code, 400)
            form = self.client.get('/account/signin',
                                   content_type='application/json').get_json()['form']
            response = self.client.post('/account/signin',
                                        data=json.dumps(dict(email=self.user.email_addr,
                                                             password='correct-password')),
                                        content_type='application/json',
                                        headers={'X-CSRFToken': form['csrf']})
            self.assertEqual(response.status_code, 200)
            self.assertIn('next', response.get_json())
            with self.client.session_transaction() as session:
                self.assertEqual(session['_user_id'], self.user.name)
