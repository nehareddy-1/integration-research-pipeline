"""Output schemas. Each LLM agent is forced to return JSON matching one of these."""
from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

# Fixed choices make the 100 rows countable (patterns) and comparable (accuracy).
Auth = Literal["OAuth2", "API key", "Basic", "Bearer token", "JWT", "Other", "unknown"]
Access = Literal[
    "Self-serve free",      # free account / free dev account can create keys or an OAuth app
    "Free trial",           # keys available only during a time-limited trial
    "Paid plan",            # API needs a paid tier
    "Admin approval",       # app review / marketplace approval / workspace admin must approve
    "Partner gated",        # partner program, contact sales, or business verification gate
    "No public API",
    "unknown",
]
ApiType = Literal["REST", "GraphQL", "SOAP", "SDK only", "CLI only", "Webhooks only", "None", "unknown"]
Breadth = Literal["Narrow", "Medium", "Broad", "unknown"]
McpStatus = Literal["Official MCP", "Community MCP", "None found", "unknown"]
Verdict = Literal[
    "Buildable now",
    "Buildable with paid account",
    "Needs partnership or approval",
    "Not buildable",
    "unknown",
]
Confidence = Literal["low", "med", "high"]


class Evidence(BaseModel):
    field: str = Field(description="Which field this supports, e.g. auth_methods, access, api_type, mcp_status, verdict")
    url: str = Field(description="Exact URL of a page you were given")
    quote: str = Field(description="Short exact quote (max ~30 words) copied from that page")


class AppRecord(BaseModel):
    """Research agent output: one row of the final table."""
    app: str
    category: str
    one_liner: str = Field(description="What the app does, one sentence")
    auth_methods: List[Auth]
    auth_notes: str = Field(description="e.g. 'OAuth2 for public apps, personal API key for internal use'")
    access: Access
    access_notes: str = Field(description="How a developer gets credentials, and any gate")
    api_type: List[ApiType]
    api_breadth: Breadth = Field(description="Narrow: few endpoints; Medium: core objects; Broad: most of the product")
    docs_url: str
    mcp_status: McpStatus
    mcp_url: str = Field(description="URL of the MCP server if any, else empty string")
    verdict: Verdict
    blocker: str = Field(description="Main blocker if not 'Buildable now', else 'none'")
    evidence: List[Evidence]
    confidence: Confidence


class UrlPick(BaseModel):
    """Research agent step 1: which search results are worth reading."""
    urls: List[str] = Field(description="Up to 4 URLs to read, most useful first")
    reason: str


class ClaimCheck(BaseModel):
    field: str
    claim: str
    verdict: Literal["SUPPORTED", "PARTIAL", "NOT_SUPPORTED"]
    reason: str = Field(description="One line")


class VerifyBatch(BaseModel):
    """Verifier agent output: one judgement per claimed field."""
    checks: List[ClaimCheck]
