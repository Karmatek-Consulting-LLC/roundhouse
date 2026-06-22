from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.config import templates_dir
from app.deps import current_user
from app.models import User
from app.services.template_engine import TemplateEngine

router = APIRouter(prefix="/api/templates", tags=["templates"], dependencies=[Depends(current_user)])


def _engine() -> TemplateEngine:
    return TemplateEngine(templates_dir())


@router.get("")
def index(_: User = Depends(current_user)):
    return _engine().list_templates()


@router.get("/{name}")
def show(name: str, _: User = Depends(current_user)):
    t = _engine().get_template(name)
    if t is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return t
