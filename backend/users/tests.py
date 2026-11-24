from django.urls import reverse
from django.core.cache import cache
from rest_framework.test import APITestCase
from rest_framework import status

from .models import User


class AuthTests(APITestCase):
    def setUp(self):
        cache.clear()
        # create data for each role user
        self.citizen_data = {
            'username': 'citizen1',
            'email': 'citizen1@example.com',
            'password': 'StrongPass123!',
            'password_confirm': 'StrongPass123!',
            'role': 'citizen'
        }
        self.ngo_data = {
            'username': 'ngo1',
            'email': 'ngo1@example.com',
            'password': 'NGOPass123!',
            'password_confirm': 'NGOPass123!',
            'role': 'ngo'
        }
        self.gov_data = {
            'username': 'gov1',
            'email': 'gov1@example.com',
            'password': 'GovPass123!',
            'password_confirm': 'GovPass123!',
            'role': 'government'
        }

    def test_register_and_login_citizen(self):
        # register citizen
        response = self.client.post(reverse('register'), self.citizen_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(username='citizen1').exists())
        self.assertEqual(User.objects.get(username='citizen1').role, 'citizen')

        # login with citizen credentials
        login_resp = self.client.post(reverse('login'), {'username': 'citizen1', 'password': 'StrongPass123!'}, format='json')
        self.assertEqual(login_resp.status_code, status.HTTP_200_OK)
        self.assertIn('access', login_resp.data)
        self.assertEqual(login_resp.data['role'], 'citizen')

    def test_self_registration_limited_to_citizen(self):
        for data in (self.ngo_data, self.gov_data, {**self.citizen_data, 'role': 'admin'}):
            resp = self.client.post(reverse('register'), data, format='json')
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn('role', resp.data)
        self.assertFalse(User.objects.filter(username__in=['ngo1', 'gov1']).exists())

    def test_register_duplicate_username(self):
        self.client.post(reverse('register'), self.citizen_data, format='json')
        response2 = self.client.post(reverse('register'), self.citizen_data, format='json')
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('username', response2.data)

    def test_login_wrong_password(self):
        self.client.post(reverse('register'), self.citizen_data, format='json')
        resp = self.client.post(reverse('login'), {'username': 'citizen1', 'password': 'WrongPass'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_role_permissions(self):
        # register all roles
        self.client.post(reverse('register'), self.citizen_data, format='json')
        User.objects.create_user(username='ngo1', email='ngo1@example.com', password='NGOPass123!', role='ngo')
        User.objects.create_user(username='gov1', email='gov1@example.com', password='GovPass123!', role='government')

        # login each user and attempt to create incident (citizen only)
        for creds, can_create in [(
            {'username': 'citizen1', 'password': 'StrongPass123!'}, True),
            ({'username': 'ngo1', 'password': 'NGOPass123!'}, False),
            ({'username': 'gov1', 'password': 'GovPass123!'}, False),
        ]:
            token_resp = self.client.post(reverse('login'), creds, format='json')
            self.assertEqual(token_resp.status_code, status.HTTP_200_OK)
            token = token_resp.data['access']
            self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

            incident_data = {
                'disaster_type': 'flood',
                'severity': 5,
                'people_affected': 100,
                'latitude': 0.0,
                'longitude': 0.0,
                'description': 'Test incident'
            }
            create_resp = self.client.post('/api/incidents/create/', incident_data, format='json')
            if can_create:
                self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
            else:
                self.assertEqual(create_resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_nonexistent_user_login(self):
        resp = self.client.post(reverse('login'), {'username': 'noone', 'password': 'foo'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_inactive_user_cannot_login(self):
        user = User.objects.create_user(username='inactive', password='pass123', role='citizen', email='inactive@example.com')
        user.is_active = False
        user.save()
        resp = self.client.post(reverse('login'), {'username': 'inactive', 'password': 'pass123'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class SessionAndPreferencesTests(APITestCase):
    PW = 'StrongPass123!'

    def setUp(self):
        cache.clear()

    def register(self, email='a@example.com', role='citizen', name='Alex'):
        return self.client.post(reverse('register'), {
            'email': email, 'first_name': name, 'password': self.PW,
            'password_confirm': self.PW, 'role': role,
        }, format='json')

    def login(self, email='a@example.com', **extra):
        return self.client.post(reverse('login'), {'email': email, 'password': self.PW, **extra}, format='json')

    def auth(self, resp):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")

    def test_register_defaults_username_to_email_and_hashes_password(self):
        self.assertEqual(self.register().status_code, 201)
        user = User.objects.get(email='a@example.com')
        self.assertEqual(user.username, 'a@example.com')
        self.assertNotEqual(user.password, self.PW)
        self.assertTrue(user.password.startswith('pbkdf2_'))

    def test_email_is_case_insensitively_unique(self):
        self.register()
        self.assertEqual(self.register(email='A@Example.com').status_code, 400)

    def test_login_by_email_and_role_mismatch_rejected(self):
        self.register()
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.login('A@EXAMPLE.com').status_code, 200)
        self.assertEqual(self.login(role='citizen').status_code, 200)
        self.assertEqual(self.login(role='government').status_code, 401)

    def test_me_requires_auth_and_only_name_editable(self):
        self.register()
        self.assertEqual(self.client.get(reverse('current-user')).status_code, 401)
        self.auth(self.login())
        resp = self.client.patch(reverse('current-user'), {'first_name': 'Bo', 'role': 'government'}, format='json')
        self.assertEqual(resp.status_code, 200)
        user = User.objects.get(email='a@example.com')
        self.assertEqual((user.first_name, user.role), ('Bo', 'citizen'))

    def test_preferences_defaults_update_and_isolation(self):
        self.register()
        self.register(email='b@example.com')
        self.auth(self.login())
        resp = self.client.get(reverse('preferences'))
        self.assertEqual((resp.status_code, resp.data['theme']), (200, 'system'))
        patch = self.client.patch(reverse('preferences'), {'theme': 'dark', 'alert_radius_km': 120,
                                                            'home_latitude': 10, 'home_longitude': 20}, format='json')
        self.assertEqual(patch.status_code, 200)
        self.auth(self.login('b@example.com'))
        self.assertEqual(self.client.get(reverse('preferences')).data['theme'], 'system')

    def test_preferences_validation(self):
        self.register()
        self.auth(self.login())
        for bad in ({'theme': 'neon'}, {'alert_radius_km': 0}, {'home_latitude': 95, 'home_longitude': 0},
                    {'home_latitude': 10}):
            self.assertEqual(self.client.patch(reverse('preferences'), bad, format='json').status_code, 400, bad)

    def test_logout_blacklists_refresh_token(self):
        self.register()
        resp = self.login()
        self.auth(resp)
        self.assertEqual(self.client.post(reverse('logout'), {'refresh': resp.data['refresh']}, format='json').status_code, 205)
        refreshed = self.client.post(reverse('token-refresh'), {'refresh': resp.data['refresh']}, format='json')
        self.assertEqual(refreshed.status_code, 401)

    def test_logout_works_without_access_token_and_rejects_garbage(self):
        self.register()
        refresh = self.login().data['refresh']
        self.client.credentials()
        self.assertEqual(self.client.post(reverse('logout'), {'refresh': {'x': 1}}, format='json').status_code, 400)
        self.assertEqual(self.client.post(reverse('logout'), {'refresh': 'junk'}, format='json').status_code, 400)
        self.assertEqual(self.client.post(reverse('logout'), {'refresh': refresh}, format='json').status_code, 205)
        self.assertEqual(self.client.post(reverse('token-refresh'), {'refresh': refresh}, format='json').status_code, 401)

    def test_preferences_reject_non_finite_coordinates(self):
        self.register()
        self.auth(self.login())
        for lat in ('nan', 'inf'):
            resp = self.client.patch(reverse('preferences'), {'home_latitude': lat, 'home_longitude': '10'})
            self.assertEqual(resp.status_code, 400, lat)

    def test_refresh_rotates_and_old_token_dies(self):
        self.register()
        first = self.login().data['refresh']
        second = self.client.post(reverse('token-refresh'), {'refresh': first}, format='json')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.client.post(reverse('token-refresh'), {'refresh': first}, format='json').status_code, 401)
