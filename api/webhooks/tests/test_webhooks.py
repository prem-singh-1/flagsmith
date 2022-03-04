import hashlib
import hmac
import json
from unittest import TestCase, mock

import pytest
from core.constants import FLAGSMITH_SIGNATURE_HEADER

from environments.models import Environment, Webhook
from organisations.models import Organisation, OrganisationWebhook
from projects.models import Project
from webhooks.sample_webhook_data import (
    environment_webhook_data,
    organisation_webhook_data,
)
from webhooks.webhooks import (
    WebhookEventType,
    WebhookType,
    call_environment_webhooks,
    trigger_sample_webhook,
)


@pytest.mark.django_db
class WebhooksTestCase(TestCase):
    def setUp(self) -> None:
        organisation = Organisation.objects.create(name="Test organisation")
        project = Project.objects.create(name="Test project", organisation=organisation)
        self.environment = Environment.objects.create(
            name="Test environment", project=project
        )

    @mock.patch("webhooks.webhooks.requests")
    def test_requests_made_to_all_urls_for_environment(self, mock_requests):
        # Given
        webhook_1 = Webhook.objects.create(
            url="http://url.1.com", enabled=True, environment=self.environment
        )
        webhook_2 = Webhook.objects.create(
            url="http://url.2.com", enabled=True, environment=self.environment
        )

        # When
        call_environment_webhooks(
            environment=self.environment,
            data={},
            event_type=WebhookEventType.FLAG_UPDATED,
        )

        # Then
        assert len(mock_requests.post.call_args_list) == 2

        # and
        call_1_args, _ = mock_requests.post.call_args_list[0]
        call_2_args, _ = mock_requests.post.call_args_list[1]
        all_call_args = call_1_args + call_2_args
        assert all(
            str(webhook.url) in all_call_args for webhook in (webhook_1, webhook_2)
        )

    @mock.patch("webhooks.webhooks.requests")
    def test_request_not_made_to_disabled_webhook(self, mock_requests):
        # Given
        Webhook.objects.create(
            url="http://url.1.com", enabled=False, environment=self.environment
        )

        # When
        call_environment_webhooks(
            environment=self.environment,
            data={},
            event_type=WebhookEventType.FLAG_UPDATED,
        )

        # Then
        mock_requests.post.assert_not_called()

    @mock.patch("webhooks.webhooks.requests")
    def test_trigger_sample_webhook_makes_correct_post_request_for_environment(
        self, mock_request
    ):
        url = "http://test.test"
        webhook = Webhook(url=url)
        trigger_sample_webhook(webhook, WebhookType.ENVIRONMENT)
        args, kwargs = mock_request.post.call_args
        assert json.loads(kwargs["data"]) == environment_webhook_data
        assert args[0] == url

    @mock.patch("webhooks.webhooks.requests")
    def test_trigger_sample_webhook_makes_correct_post_request_for_organisation(
        self, mock_request
    ):
        url = "http://test.test"
        webhook = OrganisationWebhook(url=url)

        trigger_sample_webhook(webhook, WebhookType.ORGANISATION)
        args, kwargs = mock_request.post.call_args
        assert json.loads(kwargs["data"]) == organisation_webhook_data
        assert args[0] == url

    @mock.patch("webhooks.webhooks.WebhookSerializer")
    @mock.patch("webhooks.webhooks.requests")
    def test_request_made_with_correct_signature(
        self, mock_requests, webhook_serializer
    ):
        # Given
        payload = {"key": "value"}
        webhook_serializer.return_value.data = payload
        secret = "random_key"
        Webhook.objects.create(
            url="http://url.1.com",
            enabled=True,
            environment=self.environment,
            secret=secret,
        )

        expected_signature = hmac.new(
            key=secret.encode(),
            msg=json.dumps(payload).encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()

        call_environment_webhooks(
            environment=self.environment,
            data={},
            event_type=WebhookEventType.FLAG_UPDATED,
        )
        # When
        _, kwargs = mock_requests.post.call_args_list[0]
        # Then
        received_signature = kwargs["headers"][FLAGSMITH_SIGNATURE_HEADER]
        assert hmac.compare_digest(expected_signature, received_signature) is True

    @mock.patch("webhooks.webhooks.requests")
    def test_request_does_not_have_signature_header_if_secret_is_not_set(
        self, mock_requests
    ):
        # Given
        Webhook.objects.create(
            url="http://url.1.com", enabled=True, environment=self.environment
        )
        # When
        call_environment_webhooks(
            environment=self.environment,
            data={},
            event_type=WebhookEventType.FLAG_UPDATED,
        )

        # Then
        _, kwargs = mock_requests.post.call_args_list[0]
        assert FLAGSMITH_SIGNATURE_HEADER not in kwargs["headers"]
