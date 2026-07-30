---
name: api-doc-generator
description: >
  Automatically discover API endpoints in a codebase and generate OpenAPI 3.1
  specifications and human-readable Markdown documentation. Use this skill
  whenever the user asks to document APIs, generate OpenAPI or Swagger specs,
  create API reference pages, list endpoints, audit API surface area, or
  produce interface contracts. Also trigger when the user mentions "API docs",
  "endpoint documentation", "API specification", "Swagger", "OpenAPI",
  "generate docs for routes", "document my endpoints", "API reference",
  "interface contract", or wants to know what APIs exist in a project.
  Even if the user just says "document the API" or "what endpoints do I have",
  this skill should activate.
---

# API Doc Generator

Generate accurate, standards-compliant API documentation by statically analyzing
source code. The output is an OpenAPI 3.1 specification (YAML or JSON) and a
companion Markdown reference — both derived from the actual code, not from
guesswork.

## Why this matters

Hand-written API docs drift from the implementation. Developers forget to update
them, endpoints get added or renamed, schemas change silently. By extracting the
API surface directly from route definitions and their associated type models, the
documentation stays correct as long as the code does. This skill turns the
codebase itself into the single source of truth for the API contract.

## Workflow overview

```
1. Discover    → find all route/endpoint definitions
2. Extract     → pull schemas, parameters, responses from code
3. Assemble    → build the OpenAPI document structure
4. Render      → emit YAML/JSON spec + Markdown docs
5. Validate    → confirm the spec is well-formed
```

Each step is designed to work incrementally — if only one file changed, you can
re-scan that file and patch the existing spec rather than regenerating from
scratch.

---

## Step 1: Discover endpoints

Scan the codebase for route definitions. The patterns differ by framework.

### FastAPI (Python)

Look for `APIRouter` instances and their decorated methods:

```python
# Router declaration — note the prefix and tags
router = APIRouter(prefix="/v1/operations", tags=["operations"])

# Endpoint decorators — method, path, and optional metadata
@router.get("/health", response_model=HealthResponse)
@router.post("/requests", response_model=AssistantResponse)
@router.put("/{id}", status_code=204)
@router.delete("/{id}")
@router.patch("/{id}", deprecated=True)
```

Key extraction targets:
- **Router prefix** from `APIRouter(prefix=...)` — this prepends to every route
- **Tags** from `APIRouter(tags=[...])` — becomes the OpenAPI tag group
- **HTTP method** from the decorator name (`get`, `post`, etc.)
- **Path** from the decorator's first argument
- **Response model** from `response_model=...`
- **Status code** from `status_code=...` (default: 200 for most methods, 201 for POST)
- **Deprecation** from `deprecated=True`
- **Query parameters** from function signature: `param: type = Query(...)`
- **Path parameters** from URL template: `/{param_name}`
- **Request body** from Pydantic model parameters
- **Dependencies** from `Depends(...)` — may inject headers or auth requirements

To find all routers, search for `APIRouter(` instantiations, then trace where
each router is included via `app.include_router(router)` to determine the final
mount prefix.

### Flask (Python)

```python
@app.route("/api/users", methods=["GET", "POST"])
@blueprint.route("/<int:user_id>", methods=["PUT"])
```

Flask routes combine methods in a single decorator. Extract the `methods` list
and generate one OpenAPI operation per method.

### Express (TypeScript/JavaScript)

```typescript
app.get("/api/users", handler)
router.post("/items", middleware, handler)
app.use("/api/v2", subRouter)
```

Express uses method-chained routing. The `app.use(prefix, router)` pattern
nests a sub-router under a prefix — resolve the full path by concatenating.

### MCP Tools

```python
@mcp.tool()
async def scan_files(path: str, pattern: str) -> list[str]:
    """Scan files matching a glob pattern."""
```

MCP tools are not HTTP endpoints, but they are API surface. Document them in a
separate section using the tool's name, description, and input schema.

### Discovery procedure

1. Search for router/app declarations using the patterns above
2. For each router, record its prefix and tags
3. For each decorated function, record method + path + handler name
4. Resolve `include_router` / `app.use` chains to compute final paths
5. Build a flat list of `(method, full_path, handler, router_tags)` tuples

---

## Step 2: Extract schemas and parameters

For each endpoint, extract the full request/response contract.

### Path parameters

Parse the URL template for `{param_name}` segments. Look up each parameter's
type in the function signature:

```python
@router.get("/operations/{operation_id}")
async def get_operation(operation_id: str):
```

→ Path parameter `operation_id` of type `string`.

### Query parameters

Function parameters with defaults or explicit `Query()` annotations:

```python
async def list_operations(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    state: Optional[str] = Query(None),
):
```

→ Query params: `skip` (integer, default 0, min 0), `limit` (integer, default 20, min 1, max 100), `state` (string, optional).

### Request body

Pydantic models used as function parameters:

```python
class CreateOperationRequest(BaseModel):
    task_description: str = Field(..., min_length=1)
    context: dict[str, Any] = Field(default_factory=dict)
    approved: bool = False
```

Convert Pydantic fields to JSON Schema:
- `str` → `type: string`
- `int` → `type: integer`
- `float` → `type: number`
- `bool` → `type: boolean`
- `list[T]` → `type: array`, items from T
- `dict[K, V]` → `type: object`
- `Optional[T]` → not required, nullable
- `Field(...)` constraints → `minimum`, `maximum`, `minLength`, `maxLength`, `pattern`
- `Field(default_factory=...)` → not required
- `Field(...)` (no default) → required

### Response schemas

From `response_model=...` or return type annotations. Convert the Pydantic
model to a JSON Schema the same way as request bodies. If the response model
wraps data (e.g., `{"data": ..., "status": "ok"}`), document the envelope.

### Headers and authentication

Look for `Header(...)` parameters and `Depends(...)` calls that inject auth:

```python
async def protected_route(
    x_api_key: str = Header(...),
    current_user: User = Depends(get_current_user),
):
```

→ Required header `X-API-Key`. If `get_current_user` implies Bearer token auth,
note `securitySchemes` with `bearerAuth`.

### Error responses

Look for `HTTPException` raises in the handler body:

```python
if not found:
    raise HTTPException(status_code=404, detail="Operation not found")
```

→ Response `404` with `detail` string in the body.

---

## Step 3: Assemble the OpenAPI document

Build a valid OpenAPI 3.1.0 document. The structure:

```yaml
openapi: 3.1.0
info:
  title: <from FastAPI app title or project name>
  version: <from app version or "0.1.0">
  description: <from docstring or "Auto-generated API specification">

servers:
  - url: http://localhost:8000
    description: Local development

tags:
  - name: <tag>
    description: <inferred from router module docstring>

paths:
  /prefix/path:
    get:
      tags: [<router tags>]
      summary: <from handler docstring first line>
      description: <from handler docstring remainder>
      operationId: <handler function name>
      parameters: [...]
      requestBody: {...}
      responses:
        "200":
          description: Successful response
          content:
            application/json:
              schema: <response model ref>
        "404":
          description: Not found
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/HTTPError"
      deprecated: <true/false>

components:
  schemas:
    <ModelName>:
      type: object
      properties: {...}
      required: [...]
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
```

### Assembly rules

- **operationId**: use the handler function name — it's already unique per path
- **summary**: first line of the handler's docstring
- **description**: full docstring if multi-line, otherwise omit
- **$ref**: put all Pydantic models under `components/schemas` and reference them
  rather than inlining — this keeps the spec readable and avoids duplication
- **tags**: inherit from the router's `tags=` list
- **servers**: default to `localhost:8000`; if the user specifies a base URL, use it
- **Naming**: if models share names across modules, prefix with module name to
  avoid collisions (e.g., `operations_CreateRequest` vs `assistant_CreateRequest`)

### Pydantic → JSON Schema conversion

For each Pydantic model encountered:

1. Walk its fields
2. Map Python types to JSON Schema types
3. Apply `Field()` constraints
4. Resolve nested models recursively (add them to `components/schemas` too)
5. Handle `Optional` fields by excluding them from `required`
6. Handle `Literal` types as `enum`
7. Handle `Union` types as `oneOf`
8. Handle generic models (e.g., `PaginatedResponse[T]`) by resolving the type parameter

---

## Step 4: Render output

### OpenAPI YAML (primary output)

Generate clean, well-indented YAML. Use `$ref` for shared schemas. Sort paths
alphabetically. Group operations by tag in comments.

Save to `docs/openapi.yaml` (or user-specified path).

### OpenAPI JSON (optional)

Same content, JSON format. Useful for tooling that requires JSON input.
Save to `docs/openapi.json` if requested.

### Markdown documentation

Generate a human-readable API reference. Structure:

```markdown
# API Reference

> Auto-generated from source code. Do not edit manually.

## Overview

- **Base URL**: `http://localhost:8000`
- **Total endpoints**: 12
- **Tags**: operations, assistant, aflow

---

## Operations

### `POST /v1/operations/requests`

Submit a new assistant request.

**Request body** (`application/json`):

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| task_description | string | Yes | The task to perform |
| context | object | No | Additional context |

**Response** (`200`):

| Field | Type | Description |
|-------|------|-------------|
| result | string | The assistant's response |
| status | string | Operation status |

**Example**:

```json
// Request
{"task_description": "Fix the bug", "context": {"file": "main.py"}}

// Response
{"result": "Fixed", "status": "completed"}
```

---

(repeat for each endpoint, grouped by tag)
```

### Markdown generation rules

- One section per endpoint, grouped by tag
- Each section: method badge, path, summary, parameters table, request body
  table, response table, example if inferrable
- Path parameters and query parameters in a single "Parameters" table
- Required fields marked explicitly
- Default values shown in the description column
- Enum values listed: `one of: active, archived, completed`
- Deprecated endpoints marked with a ⚠ prefix

---

## Step 5: Validate

After generating the spec, validate it:

1. **Structural validity**: every path has at least one operation, every `$ref`
   resolves to a defined schema, required fields are present
2. **Type consistency**: response models referenced in `responses` exist in
   `components/schemas`
3. **Path parameter coverage**: every `{param}` in a path has a corresponding
   parameter definition
4. **Operation ID uniqueness**: no two operations share the same `operationId`

If validation fails, report the specific issue and fix it before emitting the
final output.

---

## Incremental updates

When the user asks to update existing docs (not generate from scratch):

1. Read the existing `openapi.yaml` to get the current spec
2. Re-scan only the files that changed (use `git diff --name-only` or the user's
   indication of what changed)
3. For each changed file:
   - If it defines routes: re-extract those endpoints and update the paths
   - If it defines models: re-extract schemas and update `components/schemas`
   - If a route was removed: remove the corresponding path
   - If a model was removed and no longer referenced: remove from schemas
4. Re-validate the patched spec
5. Re-render the Markdown from the updated spec

This avoids full regeneration and preserves any manual annotations the user may
have added to the spec (like extended descriptions or examples).

---

## Handling edge cases

### Dynamic routes

Some frameworks build routes at runtime (e.g., `router.add_api_route()` in a
loop). These cannot be statically analyzed. When detected, note them in a
"Dynamic routes" section and ask the user for the runtime values.

### Generic response wrappers

Many projects wrap responses in a standard envelope:

```python
class APIResponse(BaseModel, Generic[T]):
    data: T
    status: str = "ok"
    errors: list[str] = []
```

Detect this pattern and document the envelope once, then reference it with the
inner type resolved per endpoint.

### Authentication

Common patterns to detect:
- `Depends(get_current_user)` → Bearer token
- `Depends(api_key_auth)` → API key header
- `@login_required` decorator → session-based auth
- FastAPI's `Security()` → OAuth2 or custom scheme

Map these to OpenAPI `securitySchemes` and per-operation `security` arrays.

### File uploads

```python
@router.post("/upload")
async def upload(file: UploadFile = File(...)):
```

→ `requestBody` with `content: multipart/form-data` and a `file` property of
`type: string, format: binary`.

### WebSocket endpoints

```python
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
```

OpenAPI 3.1 does not natively document WebSockets. Note these in a separate
"WebSocket Endpoints" section with the path, expected message format, and any
auth requirements.

---

## Output file locations

Default paths (override if the user specifies otherwise):

| Output | Path |
|--------|------|
| OpenAPI YAML | `docs/openapi.yaml` |
| OpenAPI JSON | `docs/openapi.json` |
| Markdown reference | `docs/api-reference.md` |

If a `docs/` directory doesn't exist, create it. If files already exist at
those paths, ask the user before overwriting.

---

## Quick reference: type mappings

| Python type | JSON Schema |
|-------------|-------------|
| `str` | `type: string` |
| `int` | `type: integer` |
| `float` | `type: number` |
| `bool` | `type: boolean` |
| `list[str]` | `type: array, items: {type: string}` |
| `dict[str, Any]` | `type: object` |
| `Optional[str]` | `type: string, nullable: true` (not required) |
| `datetime` | `type: string, format: date-time` |
| `UUID` | `type: string, format: uuid` |
| `Enum` | `type: string, enum: [...]` |
| `Literal["a", "b"]` | `type: string, enum: ["a", "b"]` |
| `UploadFile` | `type: string, format: binary` |
