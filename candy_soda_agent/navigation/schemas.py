from pydantic import BaseModel, Field

class ScreenNode(BaseModel):
    id: str
    screen_type: str
    summary: str = ""
    representative: str = ""
    visits: int = 0

class NavigationEdge(BaseModel):
    source: str
    target: str
    action: str = ""

class NavigationGraph(BaseModel):
    nodes: dict[str, ScreenNode] = Field(default_factory=dict)
    edges: list[NavigationEdge] = Field(default_factory=list)