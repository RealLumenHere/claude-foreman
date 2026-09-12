"""Generated from uhp-2026-08-11.openapi.yaml by despag/tools/gen_uhp_models.py.
DO NOT EDIT BY HAND — re-run the generator (see its module docstring for the exact
invocation) to reproduce this file after a schema change."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict

class DiscoveryImplementation(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: Optional[str] = None
    version: Optional[str] = None

class ModelCatalogBackendsItem(BaseModel):
    default: str
    models: List[Model]

class CreateResponseRequestMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    harness_id: Optional[str] = None

class ResponseMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    session_id: Optional[str] = None
    requested_model: Optional[str] = None
    model_fallback: Optional[bool] = None
    model_fallback_reason: Optional[str] = None
    ignored_fields: Optional[List[str]] = None

class Discovery(BaseModel):
    model_config = ConfigDict(extra="allow")
    object: Literal['uhp.discovery'] = 'uhp.discovery'
    protocol: Literal['uhp'] = 'uhp'
    versions: List[str]
    default_version: str
    conformance_class: Literal['core', 'extended', 'full']
    capabilities: Capabilities
    implementation: Optional[DiscoveryImplementation] = None

class Capabilities(BaseModel):
    streaming: Optional[bool] = None
    sessions: Optional[bool] = None
    cancellation: Optional[bool] = None
    files_input: Optional[bool] = None
    files_output: Optional[bool] = None
    session_listing: Optional[bool] = None
    harness_management: Optional[bool] = None
    session_sharing: Optional[bool] = None
    idempotency: Optional[bool] = None

class Harness(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    object: Literal['harness'] = 'harness'
    name: str
    base: str
    baseLabel: Optional[str] = None
    defaultModel: Optional[str] = None
    systemPrompt: Optional[str] = None
    mcpServers: Optional[List[McpServer]] = None
    skills: Optional[List[Skill]] = None
    disabledTools: Optional[List[str]] = None
    maxStep: Optional[int] = None
    timeoutSeconds: Optional[int] = None
    createdAt: Optional[int] = None

class McpServer(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    url: str
    transport: Literal['http', 'sse'] = 'http'
    enabled: bool = True
    headers: Optional[Dict[str, str]] = None
    auth: Optional[str] = None

class Skill(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    enabled: bool = True
    files: Optional[List[SkillFile]] = None
    content: Optional[str] = None
    blob: Optional[str] = None

class SkillFile(BaseModel):
    model_config = ConfigDict(extra="allow")
    path: str
    content: Optional[str] = None
    content_b64: Optional[str] = None

class HarnessCreate(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: Optional[str] = None
    base: str
    default_model: Optional[str] = None
    system_prompt: Optional[str] = None
    mcp_servers: Optional[List[McpServer]] = None
    skills: Optional[List[Skill]] = None
    disabled_tools: Optional[List[str]] = None
    max_step: Optional[int] = None
    timeout_seconds: Optional[int] = None

class ModelCatalog(BaseModel):
    backends: Dict[str, ModelCatalogBackendsItem]

class HarnessModels(BaseModel):
    harness_id: Optional[str] = None
    backend: Optional[str] = None
    default: Optional[str] = None
    fallback: Optional[str] = None
    models: List[Model]

class Model(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    label: Optional[str] = None
    backend: Optional[str] = None
    available: bool
    default: Optional[bool] = None

class CreateResponseRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    input: Union[str, List[Dict[str, Any]]]
    model: Optional[str] = None
    metadata: Optional[CreateResponseRequestMetadata] = None
    stream: bool = False
    previous_response_id: Optional[str] = None
    instructions: Optional[str] = None
    store: bool = True
    max_output_tokens: Optional[int] = None
    max_step: Optional[int] = None
    timeout_seconds: Optional[int] = None
    tools: Optional[List[Dict[str, Any]]] = None
    include: Optional[List[str]] = None
    background: bool = False

class Response(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    object: Literal['response'] = 'response'
    created_at: int
    status: ResponseStatus
    error: Optional[Error] = None
    incomplete_details: Optional[Dict[str, Any]] = None
    previous_response_id: Optional[str] = None
    model: str
    output: List[OutputItem]
    store: Optional[bool] = None
    usage: Optional[Usage] = None
    metadata: Optional[ResponseMetadata] = None

class OutputItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: Optional[str] = None
    type: str
    status: Optional[str] = None
    role: Optional[str] = None
    content: Optional[List[ContentPart]] = None
    summary: Optional[List[Dict[str, Any]]] = None
    call_id: Optional[str] = None
    name: Optional[str] = None
    arguments: Optional[str] = None
    output: Optional[str] = None

class ContentPart(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    text: Optional[str] = None
    annotations: Optional[List[Annotation]] = None

class Annotation(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal['container_file_citation'] = 'container_file_citation'
    container_id: Optional[str] = None
    file_id: Optional[str] = None
    filename: Optional[str] = None
    download_url: Optional[str] = None
    start_index: Optional[int] = None
    end_index: Optional[int] = None

class Usage(BaseModel):
    model_config = ConfigDict(extra="allow")
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cache_read_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None

class SessionShare(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    url: str
    object: Optional[str] = None
    enabled: Optional[bool] = None

class TurnItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    status: str
    user: Optional[str] = None
    assistant: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    files: Optional[List[Dict[str, Any]]] = None

class Session(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    object: Literal['session'] = 'session'
    harness_id: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[int] = None
    updated_at: Optional[int] = None

class SessionList(BaseModel):
    model_config = ConfigDict(extra="allow")
    sessions: List[Session]
    next_cursor: Optional[str] = None

class File(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    object: Literal['file'] = 'file'
    container_id: Optional[str] = None
    filename: str
    bytes: Optional[int] = None
    created_at: Optional[int] = None

class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="allow")
    error: Error
    detail: Optional[str] = None

class Error(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal['invalid_request_error', 'authentication_error', 'permission_error', 'rate_limit_error', 'harness_error', 'server_error']
    code: str
    message: str
    param: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None

class Event(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    sequence_number: int
    response: Optional[Response] = None
    item: Optional[OutputItem] = None
    part: Optional[ContentPart] = None
    annotation: Optional[Annotation] = None
    delta: Optional[str] = None
    text: Optional[str] = None
    arguments: Optional[str] = None
    item_id: Optional[str] = None
    output_index: Optional[int] = None
    content_index: Optional[int] = None
    summary_index: Optional[int] = None
    annotation_index: Optional[int] = None
    code: Optional[str] = None
    message: Optional[str] = None
    param: Optional[str] = None

ResponseStatus = Literal['in_progress', 'completed', 'failed', 'incomplete', 'cancelled']


# Resolve forward references created by inline nested schemas above.
DiscoveryImplementation.model_rebuild()
Discovery.model_rebuild()
Capabilities.model_rebuild()
Harness.model_rebuild()
McpServer.model_rebuild()
Skill.model_rebuild()
SkillFile.model_rebuild()
HarnessCreate.model_rebuild()
ModelCatalogBackendsItem.model_rebuild()
ModelCatalog.model_rebuild()
HarnessModels.model_rebuild()
Model.model_rebuild()
CreateResponseRequestMetadata.model_rebuild()
CreateResponseRequest.model_rebuild()
ResponseMetadata.model_rebuild()
Response.model_rebuild()
OutputItem.model_rebuild()
ContentPart.model_rebuild()
Annotation.model_rebuild()
Usage.model_rebuild()
SessionShare.model_rebuild()
TurnItem.model_rebuild()
Session.model_rebuild()
SessionList.model_rebuild()
File.model_rebuild()
ErrorEnvelope.model_rebuild()
Error.model_rebuild()
Event.model_rebuild()
