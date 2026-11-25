import uuid
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, status

from app.core.containers import Container
from app.schemas.common_schema import DeletedResponse
from app.routers.deps import CurrentUser
from app.schemas.conversation_schema import (
    ConversationCreate,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationRename,
    ConversationResponse,
    MessageCreate,
    SendMessageResponse,
)
from app.services.conversation_service import ConversationService

router = APIRouter(prefix="/conversations", tags=["conversations"])

ConversationServiceDep = Annotated[ConversationService, Depends(Provide[Container.conversation_service_provider])]


@router.get("", response_model=ConversationListResponse)
@inject
def list_conversations(user: CurrentUser, service: ConversationServiceDep):
    return service.list_conversations(user.id)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ConversationResponse)
@inject
def create_conversation(req: ConversationCreate, user: CurrentUser, service: ConversationServiceDep):
    return service.create_conversation(user.id, req.title)


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
@inject
def get_conversation(conversation_id: uuid.UUID, user: CurrentUser, service: ConversationServiceDep):
    return service.get_conversation(conversation_id, user.id)


@router.patch("/{conversation_id}", response_model=ConversationResponse)
@inject
def rename_conversation(
    conversation_id: uuid.UUID, req: ConversationRename, user: CurrentUser, service: ConversationServiceDep
):
    return service.rename_conversation(conversation_id, user.id, req.title)


@router.delete("/{conversation_id}", response_model=DeletedResponse)
@inject
def delete_conversation(conversation_id: uuid.UUID, user: CurrentUser, service: ConversationServiceDep):
    service.delete_conversation(conversation_id, user.id)
    return DeletedResponse(deleted=str(conversation_id))


@router.post("/{conversation_id}/messages", response_model=SendMessageResponse)
@inject
def send_message(conversation_id: uuid.UUID, req: MessageCreate, user: CurrentUser, service: ConversationServiceDep):
    return service.send_message(conversation_id, user.id, req)
