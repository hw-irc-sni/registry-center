# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0

"""TMF639 Resource Inventory Management v5 facade over agent cards.

Exposes each registered AgentCard as a TMF639 LogicalResource under
/tmf-api/resourceInventoryManagement/v5 (see mapping.py for the field
mapping). Disabled unless `tmf639.enabled=true` (REGISTRY_TMF639_ENABLED).

Endpoints:
- GET    /resource       list resources (fields, offset, limit, attribute filters)
- GET    /resource/{id}  retrieve one resource
- POST   /resource       register a signed AgentCard
- PATCH  /resource/{id}  replace the signed AgentCard (merge patch)
- DELETE /resource/{id}  deregister
- POST   /hub            subscribe to inventory change events (see events.py)
- GET    /hub/{id}       read a subscription
- DELETE /hub/{id}       unsubscribe

The facade adds no business rules of its own: registration and update run
the main port's shared flows (content safety, signature verification,
registry signing, approval status), deletes and updates use the main port's
owner check, and reads apply the discovery visibility rule. Unpublished
cards are visible only to their verified owner and to administrators (token
mode, nms_oss role); to anyone else they do not exist (404), on every method.

The main-port token middleware only covers /rest/v1/registry-center, so this
router authenticates in its own guard. Errors use the TMF630 Error body.
"""

import contextlib
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from loguru import logger

from agent_registry.errors import RegistryUnavailableError
from agent_registry.identity import TOKEN, identity_mode
from agent_registry.model.agent_layer import normalize_registration_item
from agent_registry.registry_instance import get_registry
from agent_registry.status import DISCOVERABLE_STATUS, normalize_status
from agent_registry.broadcast.callback_policy import validate_callback_destination
from agent_registry.broadcast.subscriptions import Subscription
from agent_registry.tmf639 import events, mapping
from common.custom.custom_handle import HandlerRegistry
from common.custom.interface_type import InterfaceType
from common.log.audit_logger import OperationName
from common.util.app_config import get_conf
from common.util.authenticate_util import CallerRole, Principal

_REASONS = {
    400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden', 404: 'Not Found',
    409: 'Conflict', 413: 'Payload Too Large', 415: 'Unsupported Media Type',
    422: 'Unprocessable Content', 429: 'Too Many Requests', 500: 'Internal Server Error',
    501: 'Not Implemented', 503: 'Service Unavailable',
}
_UNSUPPORTED_QUERY = ('filter', 'sort', 'before', 'after')
_PATCH_MEDIA_TYPES = ('application/merge-patch+json', 'application/json')
_WRITE_ROLES = (CallerRole.NMS_OSS, CallerRole.VENDOR_AGENT)


class TMFError(HTTPException):
    """An HTTP error rendered as a TMF630 Error body (see tmf_error_handler)."""

    def __init__(self, status_code: int, message: str = '', headers: Optional[dict] = None):
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.reason = _REASONS.get(status_code, 'Error')


async def tmf_error_handler(request: Request, exc: TMFError) -> JSONResponse:
    """Registered on the app for TMFError; more specific than the HTTPException handler."""
    return JSONResponse(status_code=exc.status_code, headers=exc.headers,
                        content=mapping.tmf_error(exc.status_code, exc.reason, str(exc.detail or '')))


@contextlib.contextmanager
def _tmf_errors():
    """Translate errors raised by the shared main-port helpers into TMFError."""
    try:
        yield
    except TMFError:
        raise
    except HTTPException as exc:
        raise TMFError(exc.status_code, str(exc.detail), getattr(exc, 'headers', None)) from exc
    except RegistryUnavailableError as exc:
        raise TMFError(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


def _server():
    # Imported lazily: agent_registry.server mounts this router, so a module-level
    # import would be circular when this module is imported first.
    from agent_registry import server
    return server


_rate_limiters: Dict[str, Any] = {}


async def _rate_limit(request: Request, interface_name: str) -> None:
    limiter = _rate_limiters.get(interface_name)
    if limiter is None:
        limiter = _rate_limiters[interface_name] = _server().RateLimiter(interface_name)
    await limiter(request)


@dataclass(frozen=True)
class TmfCaller:
    client_ip: str
    is_admin: bool = False
    owner: str = ''          # verified owner identity, '' when unverified

    def owns(self, record) -> bool:
        return bool(self.owner) and self.owner == getattr(record, 'owner', None)


def _is_hub_route(request: Request) -> bool:
    # The matched route, not the raw URL path, so a mount prefix cannot change the answer.
    route_path = getattr(request.scope.get('route'), 'path', '') or ''
    return route_path.startswith(events.HUB_PATH)


async def tmf_guard(request: Request) -> TmfCaller:
    """Feature flag, then authentication and the TMF639 role policy.

    Token mode reuses the main-port credential provider and stores the
    principal where resolve_caller_identity() expects it, so owner checks work
    exactly as on the main port. Reads and hub subscriptions are open to every
    authenticated role; resource writes need nms_oss or vendor_agent (vendors
    are further limited to their own cards by the owner check). Other identity modes keep the main port's
    behaviour: the AUTHENTICATE slot plus certificate/proxy owner identity.
    """
    config = get_conf()
    enabled = config.get('tmf639.enabled', config.get('tmf639_enabled', 'false'))
    if str(enabled).lower() != 'true':
        raise TMFError(status.HTTP_404_NOT_FOUND, 'TMF639 API is disabled')
    client_ip = request.client.host if request.client else ''
    is_admin = False
    with _tmf_errors():
        if identity_mode(_server().config) == TOKEN:
            from agent_registry.integration.app import authenticate_request
            from agent_registry.main_auth import main_token_policy
            try:
                main_token_policy.validate_configuration()
            except ValueError:
                raise TMFError(status.HTTP_503_SERVICE_UNAVAILABLE,
                               'Main authentication provider unavailable') from None
            principal = await authenticate_request(request, main_token_policy.handler)
            if not isinstance(principal, Principal) or not principal.identity or principal.role is None:
                raise TMFError(status.HTTP_503_SERVICE_UNAVAILABLE,
                               'Authentication provider returned no verified identity')
            if (request.method not in ('GET', 'HEAD') and principal.role not in _WRITE_ROLES
                    and not _is_hub_route(request)):
                raise TMFError(status.HTTP_403_FORBIDDEN,
                               'Role is not permitted to modify agent resources')
            request.scope['registry_principal'] = principal
            is_admin = principal.role == CallerRole.NMS_OSS
        authenticate_handle = HandlerRegistry.get_handler(InterfaceType.AUTHENTICATE)
        await authenticate_handle.handle(client_ip, request)
        identity = _server()._caller_identity(request)
    return TmfCaller(client_ip=client_ip, is_admin=is_admin,
                     owner=identity.owner if identity.verified and identity.owner else '')


# Routes also take the guard as a parameter (FastAPI runs it once per request);
# the router-level dependency keeps any future route from skipping it.
tmf639_router = APIRouter(prefix=mapping.API_BASE_PATH, tags=['TMF639 Resource Inventory'],
                          dependencies=[Depends(tmf_guard)])


# ---------- helpers ----------

def _public_base(request: Request) -> str:
    base = str(get_conf().get('registry.public.base_url', '') or '').strip().rstrip('/')
    return base or str(request.base_url).rstrip('/')


def _href_base(request: Request) -> str:
    return _public_base(request) + mapping.RESOURCE_PATH


def _health_of(name: str, organization: str):
    try:
        from agent_registry.health import get_health_service
        service = get_health_service()
        return service.get(name, organization) if service.enabled else None
    except Exception as exc:  # health is decorative here; never fail a read on it
        logger.debug(f"TMF639 health lookup failed for {organization}/{name}: {exc}")
        return None


def _resource(request: Request, record, caller: TmfCaller) -> Dict[str, Any]:
    card = record.agent_card
    return mapping.to_resource(record, _href_base(request),
                               health=_health_of(card.name, card.provider.organization),
                               include_owner=caller.is_admin)


def _visible(record, caller: TmfCaller) -> bool:
    """Discovery rule, widened to the card's own verified owner and administrators."""
    if record is None:
        return False
    if caller.is_admin or caller.owns(record):
        return True
    card = record.agent_card
    return (normalize_status(record.status) == DISCOVERABLE_STATUS
            and not _server()._is_hidden_unhealthy(card.name, card.provider.organization))


def _respond(request: Request, record, caller: TmfCaller, status_code: int = status.HTTP_200_OK,
             headers: Optional[dict] = None) -> JSONResponse:
    resource = _resource(request, record, caller)
    return JSONResponse(status_code=status_code, headers=headers,
                        content=mapping.select_fields(resource, request.query_params.get('fields')))


async def _authorize_write(request: Request, registry, caller: TmfCaller,
                           name: str, organization: str) -> Optional[str]:
    """Main-port owner check, without revealing cards the caller cannot see.

    The native 403 names the owner and so confirms that an unpublished card
    exists; here a card the caller cannot see is 404, and a visible card owned
    by someone else is a 403 that does not name the owner.
    """
    server = _server()
    record = registry.get_by_key_with_owner(name, organization)
    if not _visible(record, caller):
        raise TMFError(status.HTTP_404_NOT_FOUND, 'Resource not found')
    if not server.OWNER_ISOLATION_ENABLED:
        return None
    try:
        return await server._verify_owner_permission(request, name, organization, registry)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            raise TMFError(status.HTTP_403_FORBIDDEN, 'Not the owner of this resource') from exc
        raise


def _key_from_id(resource_id: str) -> Tuple[str, str]:
    key = mapping.decode_resource_id(resource_id)
    if key is None:
        raise TMFError(status.HTTP_404_NOT_FOUND, 'Resource not found')
    return key


def _non_negative_int(request: Request, name: str) -> Optional[int]:
    raw = request.query_params.get(name)
    if raw is None or raw == '':
        return None
    try:
        value = int(raw)
    except ValueError:
        raise TMFError(status.HTTP_400_BAD_REQUEST, f"'{name}' must be a non-negative integer") from None
    if value < 0:
        raise TMFError(status.HTTP_400_BAD_REQUEST, f"'{name}' must be a non-negative integer")
    return value


async def _json_object(request: Request) -> Dict[str, Any]:
    with _tmf_errors():
        return await _server()._parse_json_object(request)


def _registration_item(body: Dict[str, Any], *, partial: bool):
    """Extract the signed card (and optional layer) from a Resource body."""
    try:
        characteristics = mapping.characteristic_map(body.get('resourceCharacteristic'))
    except ValueError as exc:
        raise TMFError(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    card = characteristics.get(mapping.CHAR_AGENT_CARD)
    if not isinstance(card, dict):
        raise TMFError(status.HTTP_400_BAD_REQUEST,
                       "resourceCharacteristic 'agentCard' must carry the signed AgentCard object")
    if partial:
        unexpected = set(characteristics) - mapping.WRITABLE_CHARACTERISTICS
        if unexpected:
            raise TMFError(status.HTTP_400_BAD_REQUEST,
                           f"Only 'agentCard' and 'layer' can be patched, not: {', '.join(sorted(unexpected))}")
    item = {'agentCard': card}
    if mapping.CHAR_LAYER in characteristics:
        item['layer'] = characteristics[mapping.CHAR_LAYER]
    try:
        return normalize_registration_item(item)
    except ValueError as exc:
        raise TMFError(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None


def _check_create_body(body: Dict[str, Any], card: Dict[str, Any]) -> None:
    """Derived attributes may be echoed back, but must not contradict the card."""
    resource_type = body.get('@type')
    if resource_type is not None and resource_type not in ('LogicalResource', 'Resource'):
        raise TMFError(status.HTTP_400_BAD_REQUEST, "'@type' must be LogicalResource")
    if body.get('category') not in (None, mapping.CATEGORY):
        raise TMFError(status.HTTP_400_BAD_REQUEST, f"'category' must be {mapping.CATEGORY}")
    if body.get('name') is not None and body.get('name') != card.get('name'):
        raise TMFError(status.HTTP_400_BAD_REQUEST, "'name' must match the AgentCard name")


def _require_store(registry) -> None:
    registry.require_authoritative_store('TMF639 resource inventory')


# ---------- routes ----------

@tmf639_router.get('/resource', summary='List resources (agent cards)')
async def list_resources(request: Request, caller: TmfCaller = Depends(tmf_guard),
                         registry=Depends(get_registry)):
    unsupported = [name for name in _UNSUPPORTED_QUERY if name in request.query_params]
    if unsupported:
        raise TMFError(status.HTTP_501_NOT_IMPLEMENTED,
                       f"Query parameter(s) not supported: {', '.join(unsupported)}")
    offset = _non_negative_int(request, 'offset') or 0
    limit = _non_negative_int(request, 'limit')
    filters = {name: request.query_params[name] for name in mapping.FILTERABLE_ATTRIBUTES
               if name in request.query_params}
    organization = request.query_params.get('relatedParty.name') or None

    server = _server()
    with _tmf_errors():
        await _rate_limit(request, 'query')
        async with server.semaphore_guard(server.query_semaphore):
            _require_store(registry)
            records = [record for record in registry.find_records(organization=organization)
                       if _visible(record, caller)]
            resources = [resource for resource in (_resource(request, record, caller) for record in records)
                         if mapping.matches_filters(resource, filters)]
            page = mapping.paginate(resources, offset, limit)
            await server._maybe_audit_read(
                OperationName.QUERY_AGENT,
                {'interface': 'tmf639', 'organization': organization or '', 'count': len(page)},
                caller.client_ip, server._audit_caller(server._caller_identity(request)))
    fields = request.query_params.get('fields')
    body = [mapping.select_fields(resource, fields) for resource in page]
    return JSONResponse(status_code=status.HTTP_200_OK, content=body,
                        headers={'X-Total-Count': str(len(resources)), 'X-Result-Count': str(len(page))})


@tmf639_router.get('/resource/{resource_id}', summary='Retrieve a resource (agent card)')
async def retrieve_resource(resource_id: str, request: Request,
                            caller: TmfCaller = Depends(tmf_guard), registry=Depends(get_registry)):
    organization, name = _key_from_id(resource_id)
    server = _server()
    with _tmf_errors():
        await _rate_limit(request, 'get')
        async with server.semaphore_guard(server.get_semaphore):
            _require_store(registry)
            record = registry.get_by_key_with_owner(name, organization)
            visible = _visible(record, caller)
            await server._maybe_audit_read(
                OperationName.GET_AGENT,
                {'interface': 'tmf639', 'name': name, 'organization': organization, 'found': visible},
                caller.client_ip, server._audit_caller(server._caller_identity(request)))
    # Missing and not-visible are indistinguishable, so unpublished cards do not leak.
    if not visible:
        raise TMFError(status.HTTP_404_NOT_FOUND, 'Resource not found')
    return _respond(request, record, caller)


@tmf639_router.post('/resource', status_code=status.HTTP_201_CREATED,
                    summary='Register a signed AgentCard as a resource')
async def create_resource(request: Request, caller: TmfCaller = Depends(tmf_guard),
                          registry=Depends(get_registry)):
    server = _server()
    body = await _json_object(request)
    item = _registration_item(body, partial=False)
    _check_create_body(body, item.agent_card)
    with _tmf_errors():
        await _rate_limit(request, 'register')
        _require_store(registry)
        identity = server._caller_identity(request)
        if server.OWNER_ISOLATION_ENABLED and server.OWNER_VALIDATION_MODE == 'strict':
            server._require_verified_identity(identity, 'register agent')
        await server._process_register_cards(
            [item], caller.client_ip, identity.owner, registry,
            server.get_signature_validator(), server.get_registry_signer(),
            caller=server._audit_caller(identity))
        provider = item.agent_card.get('provider') or {}
        record = registry.get_by_key_with_owner(item.agent_card.get('name'), provider.get('organization'))
    if record is None:  # registered but no longer readable (raced with a delete)
        raise TMFError(status.HTTP_409_CONFLICT, 'Resource was removed while it was being created')
    href = f"{_href_base(request)}/{mapping.encode_resource_id(record.agent_card.provider.organization, record.agent_card.name)}"
    return _respond(request, record, caller, status.HTTP_201_CREATED, headers={'Location': href})


@tmf639_router.patch('/resource/{resource_id}', summary='Replace the signed AgentCard of a resource')
async def patch_resource(resource_id: str, request: Request,
                         caller: TmfCaller = Depends(tmf_guard), registry=Depends(get_registry)):
    media_type = request.headers.get('content-type', '').split(';')[0].strip().lower()
    if media_type not in _PATCH_MEDIA_TYPES:
        raise TMFError(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                       'Use application/merge-patch+json (JSON Patch is not supported)')
    organization, name = _key_from_id(resource_id)
    server = _server()
    body = await _json_object(request)
    unexpected = set(body) - {'resourceCharacteristic', '@type'}
    if unexpected:
        raise TMFError(status.HTTP_400_BAD_REQUEST,
                       'Only resourceCharacteristic (agentCard, layer) can be patched; '
                       f"not: {', '.join(sorted(unexpected))}. Review state changes use the native API.")
    item = _registration_item(body, partial=True)
    with _tmf_errors():
        await _rate_limit(request, 'update')
        _require_store(registry)
        owner = await _authorize_write(request, registry, caller, name, organization)
        await server._process_update_cards(
            [item], caller.client_ip, name, organization, owner,
            server.get_signature_validator(), server.get_registry_signer(),
            caller=server._audit_caller(server._caller_identity(request)))
        record = registry.get_by_key_with_owner(name, organization)
    if record is None:
        raise TMFError(status.HTTP_404_NOT_FOUND, 'Resource not found')
    return _respond(request, record, caller)


@tmf639_router.delete('/resource/{resource_id}', status_code=status.HTTP_204_NO_CONTENT,
                      summary='Deregister a resource (agent card)')
async def delete_resource(resource_id: str, request: Request,
                          caller: TmfCaller = Depends(tmf_guard), registry=Depends(get_registry)):
    organization, name = _key_from_id(resource_id)
    server = _server()
    details = {'interface': 'tmf639', 'agentName': name, 'organization': organization}
    with _tmf_errors():
        await _rate_limit(request, 'deregister')
        _require_store(registry)
        owner = await _authorize_write(request, registry, caller, name, organization)
        async with server.semaphore_guard(server.deregister_semaphore):
            deregister_handle = HandlerRegistry.get_handler(InterfaceType.DEREGISTER)
            success = await deregister_handle.handle(name, organization, owner=owner)
            await server._audit_result(OperationName.DEREGISTER_AGENT, success, details, caller.client_ip,
                                       caller=server._audit_caller(server._caller_identity(request)))
    if not success:
        raise TMFError(status.HTTP_404_NOT_FOUND, 'Resource not found')
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- hub (event subscriptions) ----------

def _broadcast_service():
    from agent_registry.broadcast import get_broadcast_service
    service = get_broadcast_service()
    if not service.broadcast_enabled:
        raise TMFError(status.HTTP_503_SERVICE_UNAVAILABLE, 'Change broadcast is disabled')
    return service


def _hub_subscription(service, hub_id: str) -> Subscription:
    subscription = service.subscription_store.get(hub_id)
    # Native subscriptions share the store but are not hubs.
    if subscription is None or subscription.format != events.SUBSCRIPTION_FORMAT:
        raise TMFError(status.HTTP_404_NOT_FOUND, 'Hub not found')
    return subscription


@tmf639_router.post('/hub', status_code=status.HTTP_201_CREATED,
                    summary='Subscribe to resource inventory events')
async def create_hub(request: Request, caller: TmfCaller = Depends(tmf_guard)):
    body = await _json_object(request)
    callback, query, secret = body.get('callback'), body.get('query'), body.get('secret')
    if not isinstance(callback, str) or not callback:
        raise TMFError(status.HTTP_400_BAD_REQUEST, "'callback' is required")
    if query is not None and not isinstance(query, str):
        raise TMFError(status.HTTP_400_BAD_REQUEST, "'query' must be a string")
    if secret is not None and (not isinstance(secret, str) or not secret):
        raise TMFError(status.HTTP_400_BAD_REQUEST, "'secret' must be a non-empty string")
    try:
        event_types = events.native_event_types(query)
        validate_callback_destination(callback)
    except ValueError as exc:
        raise TMFError(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    server = _server()
    with _tmf_errors():
        await _rate_limit(request, 'subscription')
        service = _broadcast_service()
        async with server.semaphore_guard(server.subscription_semaphore):
            created = service.subscription_store.create(Subscription(
                subscription_id='', callback_url=callback, event_types=event_types,
                secret=secret, format=events.SUBSCRIPTION_FORMAT, query=query))
            if service.dispatcher is not None:
                service.dispatcher.add_subscription(created)
    logger.info('TMF639 hub created: {} (client {})', created.subscription_id, caller.client_ip)
    location = f"{_public_base(request)}{events.HUB_PATH}/{created.subscription_id}"
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=events.to_hub(created),
                        headers={'Location': location})


@tmf639_router.get('/hub/{hub_id}', summary='Retrieve an event subscription')
async def retrieve_hub(hub_id: str, request: Request, caller: TmfCaller = Depends(tmf_guard)):
    with _tmf_errors():
        await _rate_limit(request, 'subscription')
        subscription = _hub_subscription(_broadcast_service(), hub_id)
    return JSONResponse(status_code=status.HTTP_200_OK, content=events.to_hub(subscription))


@tmf639_router.delete('/hub/{hub_id}', status_code=status.HTTP_204_NO_CONTENT,
                      summary='Remove an event subscription')
async def delete_hub(hub_id: str, request: Request, caller: TmfCaller = Depends(tmf_guard)):
    with _tmf_errors():
        await _rate_limit(request, 'subscription')
        service = _broadcast_service()
        _hub_subscription(service, hub_id)
        if not service.subscription_store.delete(hub_id):
            raise TMFError(status.HTTP_404_NOT_FOUND, 'Hub not found')
        if service.dispatcher is not None:
            service.dispatcher.remove_subscription(hub_id)
    logger.info('TMF639 hub deleted: {} (client {})', hub_id, caller.client_ip)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
