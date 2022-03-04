import base64
import json
import typing
from collections import namedtuple

import coreapi
from boto3.dynamodb.conditions import Key
from django.conf import settings
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.schemas import AutoSchema

from app.pagination import CustomPagination, EdgeIdentityPagination
from edge_api.identities.edge_request_forwarder import forward_identity_request
from environments.identities.models import Identity
from environments.identities.serializers import (
    EdgeIdentitySerializer,
    IdentitySerializer,
)
from environments.identities.traits.serializers import TraitSerializerBasic
from environments.models import Environment
from environments.permissions.permissions import NestedEnvironmentPermissions
from environments.sdk.serializers import (
    IdentifyWithTraitsSerializer,
    IdentitySerializerWithTraitsAndSegments,
)
from features.serializers import FeatureStateSerializerFull
from integrations.integration import (
    IDENTITY_INTEGRATIONS,
    identify_integrations,
)
from projects.exceptions import DynamoNotEnabledError
from util.views import SDKAPIView


class EdgeIdentityViewSet(viewsets.ModelViewSet):
    serializer_class = EdgeIdentitySerializer
    permission_classes = [IsAuthenticated, NestedEnvironmentPermissions]
    pagination_class = EdgeIdentityPagination
    lookup_field = "identity_uuid"
    dynamo_identifier_search_functions = {
        "EQUAL": lambda identifier: Key("identifier").eq(identifier),
        "BEGINS_WITH": lambda identifier: Key("identifier").begins_with(identifier),
    }

    def initial(self, request, *args, **kwargs):
        environment = self.get_environment_from_request()
        if not environment.project.enable_dynamo_db:
            raise DynamoNotEnabledError()

        super().initial(request, *args, **kwargs)

    def _get_search_function_and_value(
        self,
        search_query: str,
    ) -> typing.Tuple[typing.Callable, str]:
        if search_query.startswith('"') and search_query.endswith('"'):
            return self.dynamo_identifier_search_functions[
                "EQUAL"
            ], search_query.replace('"', "")
        return self.dynamo_identifier_search_functions["BEGINS_WITH"], search_query

    def get_object(self):
        return Identity.dynamo_wrapper.get_item_from_uuid(
            self.kwargs["environment_api_key"], self.kwargs["identity_uuid"]
        )

    def get_queryset(self):
        page_size = self.pagination_class().get_page_size(self.request)
        previous_last_evaluated_key = self.request.GET.get("last_evaluated_key")
        search_query = self.request.query_params.get("q")
        start_key = None
        if previous_last_evaluated_key:
            start_key = json.loads(base64.b64decode(previous_last_evaluated_key))

        if not search_query:
            return Identity.dynamo_wrapper.get_all_items(
                self.kwargs["environment_api_key"], page_size, start_key
            )
        search_func, search_identifier = self._get_search_function_and_value(
            search_query
        )
        identity_documents = Identity.dynamo_wrapper.search_items_with_identifier(
            self.kwargs["environment_api_key"],
            search_identifier,
            search_func,
            page_size,
            start_key,
        )
        return identity_documents

    def get_environment_from_request(self):
        """
        Get environment object from URL parameters in request.
        """
        return Environment.objects.get(api_key=self.kwargs["environment_api_key"])

    def perform_destroy(self, instance):
        Identity.dynamo_wrapper.delete_item(instance["composite_key"])


class IdentityViewSet(viewsets.ModelViewSet):
    serializer_class = IdentitySerializer
    permission_classes = [IsAuthenticated, NestedEnvironmentPermissions]
    pagination_class = CustomPagination

    def get_queryset(self):
        environment = self.get_environment_from_request()
        queryset = Identity.objects.filter(environment=environment)

        search_query = self.request.query_params.get("q")
        if search_query:
            if search_query.startswith('"') and search_query.endswith('"'):
                # Quoted searches should do an exact match just like Google
                queryset = queryset.filter(
                    identifier__exact=search_query.replace('"', "")
                )
            else:
                # Otherwise do a fuzzy search
                queryset = queryset.filter(identifier__icontains=search_query)

        # change the default order by to avoid performance issues with pagination
        # when environments have small number (<page_size) of records
        queryset = queryset.order_by("created_date")

        return queryset

    def get_environment_from_request(self):
        """
        Get environment object from URL parameters in request.
        """
        return Environment.objects.get(api_key=self.kwargs["environment_api_key"])

    def perform_create(self, serializer):
        environment = self.get_environment_from_request()
        serializer.save(environment=environment)

    def perform_update(self, serializer):
        environment = self.get_environment_from_request()
        serializer.save(environment=environment)


class SDKIdentitiesDeprecated(SDKAPIView):
    """
    THIS ENDPOINT IS DEPRECATED. Please use `/identities/?identifier=<identifier>` instead.
    """

    # API to handle /api/v1/identities/ endpoint to return Flags and Traits for user Identity
    # if Identity does not exist it will create one, otherwise will fetch existing

    serializer_class = IdentifyWithTraitsSerializer

    schema = AutoSchema(
        manual_fields=[
            coreapi.Field(
                "X-Environment-Key",
                location="header",
                description="API Key for an Environment",
            ),
            coreapi.Field(
                "identifier",
                location="path",
                required=True,
                description="Identity user identifier",
            ),
        ]
    )

    # identifier is in a path parameter
    def get(self, request, identifier, *args, **kwargs):
        # if we have identifier fetch, or create if does not exist
        if identifier:
            identity, _ = Identity.objects.get_or_create(
                identifier=identifier,
                environment=request.environment,
            )

        else:
            return Response(
                {"detail": "Missing identifier"}, status=status.HTTP_400_BAD_REQUEST
            )

        if identity:
            traits_data = identity.get_all_user_traits()
            # traits_data = self.get_serializer(identity.get_all_user_traits(), many=True)
            # return Response(traits.data, status=status.HTTP_200_OK)
        else:
            return Response(
                {"detail": "Given identifier not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # We need object type to pass into our IdentitySerializerTraitFlags
        IdentityFlagsWithTraitsAndSegments = namedtuple(
            "IdentityTraitFlagsSegments", ("flags", "traits", "segments")
        )
        identity_flags_traits_segments = IdentityFlagsWithTraitsAndSegments(
            flags=identity.get_all_feature_states(),
            traits=traits_data,
            segments=identity.get_segments(),
        )

        serializer = IdentitySerializerWithTraitsAndSegments(
            identity_flags_traits_segments
        )

        return Response(serializer.data, status=status.HTTP_200_OK)


class SDKIdentities(SDKAPIView):
    serializer_class = IdentifyWithTraitsSerializer
    pagination_class = None  # set here to ensure documentation is correct

    def get(self, request):
        identifier = request.query_params.get("identifier")
        if not identifier:
            return Response(
                {"detail": "Missing identifier"}
            )  # TODO: add 400 status - will this break the clients?

        identity, _ = (
            Identity.objects.select_related(
                "environment",
                "environment__project",
                *[
                    f"environment__{integration['relation_name']}"
                    for integration in IDENTITY_INTEGRATIONS
                ],
            )
            .prefetch_related("identity_traits")
            .get_or_create(identifier=identifier, environment=request.environment)
        )
        if settings.EDGE_API_URL:
            forward_identity_request(request, request.environment.project.id)

        feature_name = request.query_params.get("feature")
        if feature_name:
            return self._get_single_feature_state_response(identity, feature_name)
        else:
            return self._get_all_feature_states_for_user_response(identity)

    def get_serializer_context(self):
        context = super(SDKIdentities, self).get_serializer_context()
        if hasattr(self.request, "environment"):
            # only set it if the request has the attribute to ensure that the
            # documentation works correctly still
            context["environment"] = self.request.environment
        return context

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()

        if settings.EDGE_API_URL:
            forward_identity_request(request, request.environment.project.id)

        # we need to serialize the response again to ensure that the
        # trait values are serialized correctly
        response_serializer = IdentifyWithTraitsSerializer(
            instance=instance,
            context={"identity": instance.get("identity")},  # todo: improve this
        )
        return Response(response_serializer.data)

    def _get_single_feature_state_response(self, identity, feature_name):
        for feature_state in identity.get_all_feature_states():
            if feature_state.feature.name == feature_name:
                serializer = FeatureStateSerializerFull(
                    feature_state, context={"identity": identity}
                )
                return Response(data=serializer.data, status=status.HTTP_200_OK)

        return Response(
            {"detail": "Given feature not found"}, status=status.HTTP_404_NOT_FOUND
        )

    def _get_all_feature_states_for_user_response(self, identity, trait_models=None):
        """
        Get all feature states for an identity

        :param identity: Identity model to return feature states for
        :param trait_models: optional list of trait_models to pass in for organisations that don't persist them
        :return: Response containing lists of both serialized flags and traits
        """
        all_feature_states = identity.get_all_feature_states()
        serialized_flags = FeatureStateSerializerFull(
            all_feature_states, many=True, context={"identity": identity}
        )
        serialized_traits = TraitSerializerBasic(
            identity.identity_traits.all(), many=True
        )

        identify_integrations(identity, all_feature_states)

        response = {"flags": serialized_flags.data, "traits": serialized_traits.data}

        return Response(data=response, status=status.HTTP_200_OK)
