"""
These tests have been taken from
https://github.com/boto/botocore/blob/develop/tests/unit/test_credentials.py
and adapted to work with asyncio and pytest
"""
import datetime
import mock

import pytest
import botocore.exceptions
from dateutil.tz import tzlocal

from aiobotocore import credentials


# From class TestCredentials(BaseEnvVar):
@pytest.mark.parametrize("access,secret", [
    ('foo\xe2\x80\x99', 'bar\xe2\x80\x99'), (u'foo', u'bar')])
def test_credentials_normalization(access, secret):
    c = credentials.AioCredentials(access, secret)
    assert isinstance(c.access_key, type(u'u'))
    assert isinstance(c.secret_key, type(u'u'))


# From class TestRefreshableCredentials(TestCredentials):
@pytest.fixture
def refreshable_creds():
    def _f(mock_time_return_value=None, refresher_return_value='METADATA'):
        refresher = mock.AsyncMock()
        future_time = datetime.datetime.now(tzlocal()) + datetime.timedelta(hours=24)
        expiry_time = datetime.datetime.now(tzlocal()) - datetime.timedelta(minutes=30)
        metadata = {
            'access_key': 'NEW-ACCESS',
            'secret_key': 'NEW-SECRET',
            'token': 'NEW-TOKEN',
            'expiry_time': future_time.isoformat(),
            'role_name': 'rolename',
        }
        refresher.return_value = metadata if refresher_return_value == 'METADATA' \
            else refresher_return_value
        mock_time = mock.Mock()
        mock_time.return_value = mock_time_return_value
        creds = credentials.AioRefreshableCredentials(
            'ORIGINAL-ACCESS', 'ORIGINAL-SECRET', 'ORIGINAL-TOKEN',
            expiry_time, refresher, 'iam-role', time_fetcher=mock_time
        )
        return creds
    return _f


@pytest.mark.asyncio
async def test_refreshablecredentials_get_credentials_set(refreshable_creds):
    creds = refreshable_creds(
        mock_time_return_value=(datetime.datetime.now(tzlocal()) -
                                datetime.timedelta(minutes=60))
    )

    assert not creds.refresh_needed()

    credentials_set = await creds.get_frozen_credentials()
    assert isinstance(credentials_set, credentials.ReadOnlyCredentials)
    assert credentials_set.access_key == 'ORIGINAL-ACCESS'
    assert credentials_set.secret_key == 'ORIGINAL-SECRET'
    assert credentials_set.token == 'ORIGINAL-TOKEN'


@pytest.mark.asyncio
async def test_refreshablecredentials_refresh_returns_empty_dict(refreshable_creds):
    creds = refreshable_creds(
        mock_time_return_value=datetime.datetime.now(tzlocal()),
        refresher_return_value={}
    )

    assert creds.refresh_needed()

    with pytest.raises(botocore.exceptions.CredentialRetrievalError):
        await creds.get_frozen_credentials()


@pytest.mark.asyncio
async def test_refreshablecredentials_refresh_returns_none(refreshable_creds):
    creds = refreshable_creds(
        mock_time_return_value=datetime.datetime.now(tzlocal()),
        refresher_return_value=None
    )

    assert creds.refresh_needed()

    with pytest.raises(botocore.exceptions.CredentialRetrievalError):
        await creds.get_frozen_credentials()


@pytest.mark.asyncio
async def test_refreshablecredentials_refresh_returns_partial(refreshable_creds):
    creds = refreshable_creds(
        mock_time_return_value=datetime.datetime.now(tzlocal()),
        refresher_return_value={'access_key': 'akid'}
    )

    assert creds.refresh_needed()

    with pytest.raises(botocore.exceptions.CredentialRetrievalError):
        await creds.get_frozen_credentials()


# From class TestDeferredRefreshableCredentials(unittest.TestCase):
@pytest.fixture
def deferrable_creds():
    def _f(mock_time_return_value=None, refresher_return_value='METADATA'):
        refresher = mock.AsyncMock()
        future_time = datetime.datetime.now(tzlocal()) + datetime.timedelta(hours=24)
        metadata = {
            'access_key': 'NEW-ACCESS',
            'secret_key': 'NEW-SECRET',
            'token': 'NEW-TOKEN',
            'expiry_time': future_time.isoformat(),
            'role_name': 'rolename',
        }
        refresher.return_value = metadata if refresher_return_value == 'METADATA' \
            else refresher_return_value
        mock_time = mock.Mock()
        mock_time.return_value = (mock_time_return_value or
                                  datetime.datetime.now(tzlocal()))
        creds = credentials.AioDeferredRefreshableCredentials(
            refresher, 'iam-role', time_fetcher=mock_time
        )
        return creds
    return _f


@pytest.mark.asyncio
async def test_deferrablecredentials_get_credentials_set(deferrable_creds):
    creds = deferrable_creds()

    creds._refresh_using.assert_not_called()

    await creds.get_frozen_credentials()
    assert creds._refresh_using.call_count == 1


@pytest.mark.asyncio
async def test_deferrablecredentials_refresh_only_called_once(deferrable_creds):
    creds = deferrable_creds()

    creds._refresh_using.assert_not_called()

    for _ in range(5):
        await creds.get_frozen_credentials()

    assert creds._refresh_using.call_count == 1


# From class TestAssumeRoleCredentialFetcher(BaseEnvVar):
def assume_role_client_creator(with_response):
    class _Client(object):
        def __init__(self, resp):
            self._resp = resp

            self._called = []
            self._call_count = 0

        async def assume_role(self, *args, **kwargs):
            self._call_count += 1
            self._called.append((args, kwargs))

            if isinstance(self._resp, list):
                return self._resp.pop(0)
            return self._resp

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    return mock.Mock(return_value=_Client(with_response))


def some_future_time():
    timeobj = datetime.datetime.now(tzlocal())
    return timeobj + datetime.timedelta(hours=24)


def get_expected_creds_from_response(response):
    expiration = response['Credentials']['Expiration']
    if isinstance(expiration, datetime.datetime):
        expiration = expiration.isoformat()
    return {
        'access_key': response['Credentials']['AccessKeyId'],
        'secret_key': response['Credentials']['SecretAccessKey'],
        'token': response['Credentials']['SessionToken'],
        'expiry_time': expiration
    }


@pytest.mark.asyncio
async def test_assumerolefetcher_no_cache():
    response = {
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': some_future_time().isoformat()
        },
    }
    refresher = credentials.AioAssumeRoleCredentialFetcher(
        assume_role_client_creator(response),
        credentials.AioCredentials('a', 'b', 'c'),
        'myrole'
    )

    expected_response = get_expected_creds_from_response(response)
    response = await refresher.fetch_credentials()

    assert response == expected_response


@pytest.mark.asyncio
async def test_assumerolefetcher_cache_key_with_role_session_name():
    response = {
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': some_future_time().isoformat()
        },
    }
    cache = {}
    client_creator = assume_role_client_creator(response)
    role_session_name = 'my_session_name'

    refresher = credentials.AioAssumeRoleCredentialFetcher(
        client_creator,
        credentials.AioCredentials('a', 'b', 'c'),
        'myrole',
        cache=cache,
        extra_args={'RoleSessionName': role_session_name}
    )
    await refresher.fetch_credentials()

    # This is the sha256 hex digest of the expected assume role args.
    cache_key = (
        '2964201f5648c8be5b9460a9cf842d73a266daf2'
    )
    assert cache_key in cache
    assert cache[cache_key] == response


@pytest.mark.asyncio
async def test_assumerolefetcher_cache_in_cache_but_expired():
    response = {
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': some_future_time().isoformat(),
        },
    }
    client_creator = assume_role_client_creator(response)
    cache = {
        'development--myrole': {
            'Credentials': {
                'AccessKeyId': 'foo-cached',
                'SecretAccessKey': 'bar-cached',
                'SessionToken': 'baz-cached',
                'Expiration': datetime.datetime.now(tzlocal()),
            }
        }
    }

    refresher = credentials.AioAssumeRoleCredentialFetcher(
        client_creator,
        credentials.AioCredentials('a', 'b', 'c'),
        'myrole',
        cache=cache
    )
    expected = get_expected_creds_from_response(response)
    response = await refresher.fetch_credentials()

    assert response == expected


# From class TestAssumeRoleWithWebIdentityCredentialFetcher(BaseEnvVar):
def assume_role_web_identity_client_creator(with_response):
    class _Client(object):
        def __init__(self, resp):
            self._resp = resp

            self._called = []
            self._call_count = 0

        async def assume_role_with_web_identity(self, *args, **kwargs):
            self._call_count += 1
            self._called.append((args, kwargs))

            if isinstance(self._resp, list):
                return self._resp.pop(0)
            return self._resp

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    return mock.Mock(return_value=_Client(with_response))


@pytest.mark.asyncio
async def test_webidentfetcher_no_cache():
    response = {
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': some_future_time().isoformat()
        },
    }
    refresher = credentials.AioAssumeRoleWithWebIdentityCredentialFetcher(
        assume_role_web_identity_client_creator(response),
        lambda: 'totally.a.token',
        'myrole'
    )

    expected_response = get_expected_creds_from_response(response)
    response = await refresher.fetch_credentials()

    assert response == expected_response


# From class TestInstanceMetadataProvider(BaseEnvVar):
@pytest.mark.asyncio
async def test_instancemetadata_load():
    timeobj = datetime.datetime.now(tzlocal())
    timestamp = (timeobj + datetime.timedelta(hours=24)).isoformat()

    fetcher = mock.AsyncMock()
    fetcher.retrieve_iam_role_credentials.return_value = {
        'access_key': 'a',
        'secret_key': 'b',
        'token': 'c',
        'expiry_time': timestamp,
        'role_name': 'myrole',
    }

    provider = credentials.AioInstanceMetadataProvider(
        iam_role_fetcher=fetcher
    )
    creds = await provider.load()
    assert creds is not None
    assert creds.access_key == 'a'
    assert creds.secret_key == 'b'
    assert creds.token == 'c'
    assert creds.method == 'iam-role'


# From class CredentialResolverTest(BaseEnvVar):
@pytest.fixture
def credential_provider():
    def _f(method, canonical_name, creds='None'):
        # 'None' so that we can differentiate from None
        provider = mock.AsyncMock()
        provider.METHOD = method
        provider.CANONICAL_NAME = canonical_name
        if creds != 'None':
            provider.load.return_value = creds
        return provider
    return _f


@pytest.mark.asyncio
async def test_credresolver_load_credentials_single_provider(credential_provider):
    provider1 = credential_provider('provider1', 'CustomProvider1',
                                    credentials.AioCredentials('a', 'b', 'c'))
    resolver = credentials.AioCredentialResolver(providers=[provider1])

    creds = await resolver.load_credentials()
    assert creds.access_key == 'a'
    assert creds.secret_key == 'b'
    assert creds.token == 'c'


# From class TestCanonicalNameSourceProvider(BaseEnvVar):
@pytest.mark.asyncio
async def test_canonicalsourceprovider_source_creds(credential_provider):
    creds = credentials.AioCredentials('a', 'b', 'c')
    provider1 = credential_provider('provider1', 'CustomProvider1', creds)
    provider2 = credential_provider('provider2', 'CustomProvider2')
    provider = credentials.AioCanonicalNameCredentialSourcer(
        providers=[provider1, provider2])

    result = await provider.source_credentials('CustomProvider1')
    assert result is creds


@pytest.mark.asyncio
async def test_canonicalsourceprovider_source_creds_case_insensitive(
        credential_provider):
    creds = credentials.AioCredentials('a', 'b', 'c')
    provider1 = credential_provider('provider1', 'CustomProvider1', creds)
    provider2 = credential_provider('provider2', 'CustomProvider2')
    provider = credentials.AioCanonicalNameCredentialSourcer(
        providers=[provider1, provider2])

    result = await provider.source_credentials('cUsToMpRoViDeR1')
    assert result is creds


# From class TestAssumeRoleCredentialProvider(unittest.TestCase):
@pytest.fixture
def assumerolecredprovider_config_loader():
    fake_config = {
        'profiles': {
            'development': {
                'role_arn': 'myrole',
                'source_profile': 'longterm',
            },
            'longterm': {
                'aws_access_key_id': 'akid',
                'aws_secret_access_key': 'skid',
            },
            'non-static': {
                'role_arn': 'myrole',
                'credential_source': 'Environment'
            },
            'chained': {
                'role_arn': 'chained-role',
                'source_profile': 'development'
            }
        }
    }

    def _f(config=None):
        return lambda: (config or fake_config)

    return _f


@pytest.mark.asyncio
async def test_assumerolecredprovider_assume_role_no_cache(
        credential_provider,
        assumerolecredprovider_config_loader):
    creds = credentials.AioCredentials('a', 'b', 'c')
    provider1 = credential_provider('provider1', 'CustomProvider1', creds)
    provider2 = credential_provider('provider2', 'CustomProvider2')
    provider = credentials.AioCanonicalNameCredentialSourcer(
        providers=[provider1, provider2])

    result = await provider.source_credentials('cUsToMpRoViDeR1')
    assert result is creds

    response = {
        'Credentials': {
            'AccessKeyId': 'foo',
            'SecretAccessKey': 'bar',
            'SessionToken': 'baz',
            'Expiration': some_future_time().isoformat()
        },
    }
    client_creator = assume_role_client_creator(response)
    provider = credentials.AioAssumeRoleProvider(
        assumerolecredprovider_config_loader(),
        client_creator, cache={}, profile_name='development')

    creds = await provider.load()

    # So calling .access_key would cause deferred credentials to be loaded,
    # according to the source, you're supposed to call get_frozen_credentials
    # so will do that.
    creds = await creds.get_frozen_credentials()
    assert creds.access_key == 'foo'
    assert creds.secret_key == 'bar'
    assert creds.token == 'baz'


# From class TestContainerProvider(BaseEnvVar):
def full_url(url):
    return 'http://%s%s' % (credentials.AioContainerMetadataFetcher.IP_ADDRESS, url)


@pytest.mark.asyncio
async def test_containerprovider_assume_role_no_cache():
    environ = {
        'AWS_CONTAINER_CREDENTIALS_RELATIVE_URI': '/latest/credentials?id=foo'
    }
    fetcher = mock.AsyncMock()
    fetcher.full_url = full_url

    timeobj = datetime.datetime.now(tzlocal())
    timestamp = (timeobj + datetime.timedelta(hours=24)).isoformat()
    fetcher.retrieve_full_uri.return_value = {
        "AccessKeyId": "access_key",
        "SecretAccessKey": "secret_key",
        "Token": "token",
        "Expiration": timestamp,
    }
    provider = credentials.AioContainerProvider(environ, fetcher)
    # Will return refreshable credentials
    creds = await provider.load()

    url = full_url('/latest/credentials?id=foo')
    fetcher.retrieve_full_uri.assert_called_with(url, headers=None)

    assert creds.access_key == 'access_key'
    assert creds.secret_key == 'secret_key'
    assert creds.token == 'token'
    assert creds.method == 'container-role'
