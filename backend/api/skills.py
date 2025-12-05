"""Skills API endpoints"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from storage.skills_store import skills_store

router = APIRouter()


class SkillCreate(BaseModel):
    """Request model for creating a skill"""
    name: str
    description: Optional[str] = None
    system_prompt: str


class Skill(BaseModel):
    """Skill model - matches frontend Skill interface"""
    skill_id: str
    name: str
    description: Optional[str] = None
    system_prompt: str
    is_builtin: bool = False


@router.get("/")
async def list_skills():
    """List all skills"""
    skills = await skills_store.list()
    return skills


@router.post("/", response_model=Skill)
async def create_skill(skill: SkillCreate):
    """Create a new skill"""
    result = await skills_store.create(
        name=skill.name,
        system_prompt=skill.system_prompt,
        description=skill.description
    )
    return result


@router.get("/{skill_id}", response_model=Skill)
async def get_skill(skill_id: str):
    """Get a specific skill"""
    skill = await skills_store.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill
