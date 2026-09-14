import hashlib
import json
import os
import unittest
from unittest.mock import patch

from ir_graphrag.auth import (AuthService, InvalidCredentials, LoginBusy, LoginRateLimited,
                             SESSION_SECONDS, hash_password, load_users, validate_users)


TEST_PASSWORDS = {'analyst': 'auth-test-password', 'reviewer': 'auth-review-password'}
TEST_USERS = {username: hash_password(password) for username, password in TEST_PASSWORDS.items()}


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.auth = AuthService(TEST_USERS)

    def test_configured_accounts_authenticate_and_passwords_are_hashed(self):
        for username, password in TEST_PASSWORDS.items():
            with self.subTest(username=username):
                self.assertRegex(TEST_USERS[username], r'^pbkdf2_sha256\$600000\$[0-9a-f]{32}\$[0-9a-f]{64}$')
                self.assertNotIn(password, TEST_USERS[username])
                token = self.auth.login(username, password, 'client')
                self.assertEqual(self.auth.current_user(token), username)
                self.assertNotIn(token, self.auth._sessions)
                self.assertIn(hashlib.sha256(token.encode()).hexdigest(), self.auth._sessions)

    def test_invalid_and_unknown_credentials_never_create_sessions(self):
        for username, password in [('analyst', 'wrong'), ('reviewer', ''),
                                   ('missing', TEST_PASSWORDS['reviewer']), ('', TEST_PASSWORDS['reviewer']),
                                   ('reviewer', None), ('reviewer', '\ud800')]:
            with self.subTest(username=username, password_type=type(password).__name__):
                with self.assertRaises(InvalidCredentials):
                    self.auth.login(username, password, 'client')
        self.assertEqual(self.auth._sessions, {})

    def test_expiry_logout_and_token_tampering_revoke_access(self):
        with patch('ir_graphrag.auth.time.monotonic', return_value=100) as clock:
            token = self.auth.login('reviewer', TEST_PASSWORDS['reviewer'], 'client')
            changed = ('a' if token[0] != 'a' else 'b') + token[1:]
            for invalid in (None, '', changed, token + 'x', 'é' * 43):
                self.assertIsNone(self.auth.current_user(invalid))
            clock.return_value = 100 + SESSION_SECONDS - 1
            self.assertEqual(self.auth.current_user(token), 'reviewer')
            clock.return_value += 1
            self.assertIsNone(self.auth.current_user(token))
            token = self.auth.login('analyst', TEST_PASSWORDS['analyst'], 'client')
            self.auth.logout(token)
            self.auth.logout(token)
            self.auth.logout(None)
            self.assertIsNone(self.auth.current_user(token))

    def test_login_attempts_are_limited_per_ip_before_password_hashing(self):
        with patch('ir_graphrag.auth.time.monotonic', return_value=1000) as clock:
            with patch('ir_graphrag.auth._verify_password', return_value=False) as verify:
                for _ in range(10):
                    with self.assertRaises(InvalidCredentials):
                        self.auth.login('reviewer', 'wrong', 'client')
                with self.assertRaises(LoginRateLimited):
                    self.auth.login('reviewer', TEST_PASSWORDS['reviewer'], 'client')
                self.assertEqual(verify.call_count, 10)
                with self.assertRaises(InvalidCredentials):
                    self.auth.login('reviewer', 'wrong', 'another-client')
                clock.return_value = 1060
                with self.assertRaises(InvalidCredentials):
                    self.auth.login('reviewer', 'wrong', 'client')

    def test_busy_hash_workers_and_full_client_table_reject_admission(self):
        self.auth._hash_slots.acquire()
        self.auth._hash_slots.acquire()
        try:
            with patch('ir_graphrag.auth._verify_password') as verify:
                with self.assertRaises(LoginBusy):
                    self.auth.login('reviewer', TEST_PASSWORDS['reviewer'], 'client')
                verify.assert_not_called()
        finally:
            self.auth._hash_slots.release()
            self.auth._hash_slots.release()
        with patch('ir_graphrag.auth.MAX_LOGIN_CLIENTS', 1):
            with self.assertRaises(LoginBusy):
                self.auth.login('reviewer', TEST_PASSWORDS['reviewer'], 'another-client')

    def test_password_hashes_are_salted_and_invalid_configuration_is_rejected(self):
        self.assertNotEqual(hash_password(TEST_PASSWORDS['reviewer']), TEST_USERS['reviewer'])
        self.assertIn('new-user', validate_users({'new-user': hash_password('test-only-password')}))
        for password in ('', 'x' * 1025, None, '\ud800'):
            with self.assertRaises(ValueError):
                hash_password(password)
        for users in ({}, None, {'reviewer': 'plaintext'}, {' reviewer': TEST_USERS['reviewer']}):
            with self.assertRaises(ValueError):
                validate_users(users)

    def test_environment_configuration_loads_only_explicit_accounts(self):
        with patch.dict(os.environ, {'GRAPHRAG_AUTH_USERS_JSON': json.dumps(TEST_USERS)}):
            configured = load_users()
        self.assertEqual(configured, TEST_USERS)
        configured['new-user'] = TEST_USERS['reviewer']
        self.assertNotIn('new-user', TEST_USERS)

    def test_missing_or_invalid_environment_configuration_fails_without_echoing_it(self):
        with patch.dict(os.environ, clear=True):
            with self.assertRaisesRegex(ValueError, 'Configure GRAPHRAG_AUTH_USERS_JSON'):
                load_users()
        for value in ('', 'private-config-not-json', '{}', '[]', 'null',
                      json.dumps({'reviewer': 'private-config-plaintext'})):
            with self.subTest(kind=value[:1]):
                with patch.dict(os.environ, {'GRAPHRAG_AUTH_USERS_JSON': value}):
                    with self.assertRaises(ValueError) as error:
                        load_users()
                self.assertEqual(str(error.exception),
                                 'Configure GRAPHRAG_AUTH_USERS_JSON with a valid username/password-hash mapping.')
                self.assertNotIn('private-config', str(error.exception))


if __name__ == '__main__':
    unittest.main()
