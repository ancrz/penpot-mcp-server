# Penpot MCP Server — Tool Reference

Complete reference for all **66 tools** provided by the server.

---

## Table of Contents

1. [Projects & Teams](#1-projects--teams) (4 tools)
2. [File Operations](#2-file-operations) (9 tools)
3. [Shape Reading](#3-shape-reading) (6 tools)
4. [Components & Design Tokens](#4-components--design-tokens) (4 tools)
5. [Comments & Collaboration](#5-comments--collaboration) (6 tools)
6. [Media & Fonts](#6-media--fonts) (3 tools)
7. [Database & Advanced](#7-database--advanced) (3 tools)
8. [Snapshots](#8-snapshots) (2 tools)
9. [Export](#9-export) (2 tools)
10. [Advanced Analysis](#10-advanced-analysis) (2 tools)
11. [Shape Creation](#11-shape-creation) (8 tools)
12. [Shape Modification](#12-shape-modification) (12 tools)
13. [Text Operations](#13-text-operations) (5 tools)

---

## 1. Projects & Teams

### `list_teams`
List all teams in the Penpot instance with member and project counts.

**Parameters:** None

---

### `list_projects`
List projects, optionally filtered by team ID.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `team_id` | string | No | Filter by team UUID. Omit to list all projects. |

---

### `list_files`
List all files in a project.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project_id` | string | Yes | The project UUID. |

---

### `search_files`
Search files by name across all projects.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `query` | string | Yes | Search term (case-insensitive partial match). |

---

## 2. File Operations

### `get_file_summary`
Get detailed metadata for a file (counts, team, project, versions).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |

---

### `get_file_pages`
Get all pages in a file with their object counts.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |

---

### `get_file_history`
Get revision history of a file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `limit` | integer | No | Max entries to return (default 20). |

---

### `get_file_libraries`
List shared libraries linked to a file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |

---

### `create_project`
Create a new project in a team.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `team_id` | string | Yes | The team UUID. |
| `name` | string | Yes | Project name. |

---

### `create_file`
Create a new design file in a project.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project_id` | string | Yes | The project UUID. |
| `name` | string | Yes | File name. |

---

### `rename_file`
Rename an existing file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `name` | string | Yes | New name. |

---

### `duplicate_file`
Duplicate an existing file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `name` | string | No | Optional name for the copy. |

---

### `delete_file`
Delete a file (moves to trash).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |

---

## 3. Shape Reading

### `get_page_objects`
List all objects on a page, optionally filtered by type.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_type` | string | No | Filter: rect, circle, frame, text, group, path, image, svg-raw, bool. |

---

### `get_shape_tree`
Get the hierarchical tree of shapes on a page.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `root_id` | string | No | Start from this shape ID (omit for page root). |
| `depth` | integer | No | Max tree depth (default 3). |

---

### `get_shape_details`
Get full details of a specific shape (fills, strokes, layout, text content).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The shape UUID. |

---

### `search_shapes`
Search shapes by name or text content on a page.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `query` | string | Yes | Search term (case-insensitive). |
| `search_type` | string | No | "name" or "text" (default "name"). |

---

### `get_shape_css`
Get CSS representation of a shape's visual properties. Converts Penpot fills, strokes, shadows, and layout to CSS.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The shape UUID. |

---

### `get_shape_svg`
Get SVG representation of a shape.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The shape UUID. |

---

## 4. Components & Design Tokens

### `get_component_instances`
List all components defined in a file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |

---

### `get_design_tokens`
Get consolidated design tokens (colors, typographies, component count) from a file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |

---

### `get_colors_library`
Get all colors defined in a file's library.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |

---

### `get_typography_library`
Get all typographies defined in a file's library.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |

---

## 5. Comments & Collaboration

### `get_comments`
Get all comments on a file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `resolved` | boolean | No | Filter: true=resolved, false=unresolved, omit=all. |

---

### `get_active_users`
Get users currently editing a file (real-time presence).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |

---

### `get_share_links`
List share links for a file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |

---

### `create_comment`
Create a new comment on a page at a specific position.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `content` | string | Yes | Comment text. |
| `x` | float | Yes | X position. |
| `y` | float | Yes | Y position. |
| `frame_id` | string | No | Optional frame to attach the comment to. |

---

### `reply_to_comment`
Reply to an existing comment thread.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `thread_id` | string | Yes | The comment thread UUID. |
| `content` | string | Yes | Reply text. |

---

### `resolve_comment`
Resolve or unresolve a comment thread.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `thread_id` | string | Yes | The comment thread UUID. |
| `resolved` | boolean | No | True to resolve, False to unresolve (default True). |

---

## 6. Media & Fonts

### `list_media_assets`
List all media assets (images) in a file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |

---

### `list_fonts`
List custom fonts uploaded to a team.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `team_id` | string | Yes | The team UUID. |

---

### `upload_media`
Upload an image to a file from a URL.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `name` | string | Yes | Name for the media asset. |
| `url` | string | Yes | Public URL of the image. |

---

## 7. Database & Advanced

### `query_database`
Execute a read-only SQL query against the Penpot database. Only SELECT statements allowed.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `sql` | string | Yes | SQL SELECT query. |

---

### `get_webhooks`
List webhooks configured for a team.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `team_id` | string | Yes | The team UUID. |

---

### `get_profile`
Get the authenticated user's profile information.

**Parameters:** None

---

## 8. Snapshots

### `create_snapshot`
Create a named snapshot (version) of a file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `label` | string | Yes | Label for the snapshot. |

---

### `get_snapshots`
List all snapshots of a file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |

---

## 9. Export

### `export_frame_png`
Export a frame or shape to PNG via Penpot's exporter (headless Chromium). Returns base64-encoded PNG data.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `object_id` | string | Yes | The shape/frame UUID to export. |
| `scale` | float | No | Scale factor: 1.0=normal, 2.0=retina (default 1.0). |

---

### `export_frame_svg`
Export a frame or shape to SVG. Uses Penpot's exporter for pixel-perfect output; falls back to local SVG generation if the exporter is unavailable.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `object_id` | string | Yes | The shape/frame UUID to export. |

---

## 10. Advanced Analysis

### `get_file_raw_data`
Get the decoded internal data structure of a file. Returns the file's pages, components, colors, and typographies after full Transit decoding.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | No | Returns only that page's data if provided. |

---

### `compare_revisions`
Compare two revisions of a file to see what changed. Shows change operations (add-obj, mod-obj, del-obj) between revision numbers.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `revn_from` | integer | Yes | Starting revision number. |
| `revn_to` | integer | No | Ending revision number (default: latest). |

---

## 11. Shape Creation

### `create_rectangle`
Create a rectangle shape on a page.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `file_id` | string | Yes | — | The file UUID. |
| `page_id` | string | Yes | — | The page UUID. |
| `x` | float | No | 0 | X position. |
| `y` | float | No | 0 | Y position. |
| `width` | float | No | 100 | Width in pixels. |
| `height` | float | No | 100 | Height in pixels. |
| `name` | string | No | "Rectangle" | Shape name. |
| `fill_color` | string | No | "#B1B2B5" | Fill color hex. |
| `fill_opacity` | float | No | 1.0 | Fill opacity 0-1. |
| `stroke_color` | string | No | — | Optional stroke color hex. |
| `stroke_width` | float | No | 1.0 | Stroke width pixels. |
| `opacity` | float | No | 1.0 | Overall opacity 0-1. |
| `border_radius` | float | No | 0 | Corner radius. |
| `parent_id` | string | No | — | Parent shape ID (root if omitted). |

---

### `create_frame`
Create a frame (artboard/container) that can hold child shapes.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `file_id` | string | Yes | — | The file UUID. |
| `page_id` | string | Yes | — | The page UUID. |
| `x` | float | No | 0 | X position. |
| `y` | float | No | 0 | Y position. |
| `width` | float | No | 300 | Width in pixels. |
| `height` | float | No | 300 | Height in pixels. |
| `name` | string | No | "Frame" | Frame name. |
| `fill_color` | string | No | "#FFFFFF" | Background color hex. |
| `fill_opacity` | float | No | 1.0 | Background opacity 0-1. |
| `stroke_color` | string | No | — | Optional border color hex. |
| `stroke_width` | float | No | 1.0 | Border width. |
| `opacity` | float | No | 1.0 | Overall opacity 0-1. |
| `border_radius` | float | No | 0 | Corner radius. |
| `clip_content` | boolean | No | true | Clip children at frame bounds. |
| `parent_id` | string | No | — | Parent frame ID (root if omitted). |

---

### `create_ellipse`
Create an ellipse (or circle if width == height) on a page.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `file_id` | string | Yes | — | The file UUID. |
| `page_id` | string | Yes | — | The page UUID. |
| `x` | float | No | 0 | X position. |
| `y` | float | No | 0 | Y position. |
| `width` | float | No | 100 | Width in pixels. |
| `height` | float | No | 100 | Height in pixels. |
| `name` | string | No | "Ellipse" | Shape name. |
| `fill_color` | string | No | "#B1B2B5" | Fill color hex. |
| `fill_opacity` | float | No | 1.0 | Fill opacity 0-1. |
| `stroke_color` | string | No | — | Optional stroke color hex. |
| `stroke_width` | float | No | 1.0 | Stroke width. |
| `opacity` | float | No | 1.0 | Overall opacity 0-1. |
| `parent_id` | string | No | — | Parent shape ID (root if omitted). |

---

### `create_text`
Create a text shape on a page.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `file_id` | string | Yes | — | The file UUID. |
| `page_id` | string | Yes | — | The page UUID. |
| `text` | string | No | "Text" | Text content. |
| `x` | float | No | 0 | X position. |
| `y` | float | No | 0 | Y position. |
| `width` | float | No | — | Text box width (auto if omitted). |
| `height` | float | No | — | Text box height (auto if omitted). |
| `name` | string | No | — | Shape name (defaults to text content). |
| `font_family` | string | No | "sourcesanspro" | Font family. |
| `font_size` | integer | No | 16 | Font size in pixels. |
| `font_weight` | string | No | "400" | Weight: "400"=normal, "700"=bold. |
| `font_style` | string | No | "normal" | Style: "normal" or "italic". |
| `fill_color` | string | No | "#000000" | Text color hex. |
| `fill_opacity` | float | No | 1.0 | Text opacity 0-1. |
| `text_align` | string | No | "left" | Alignment: left/center/right/justify. |
| `line_height` | float | No | 1.2 | Line height multiplier. |
| `letter_spacing` | float | No | 0 | Letter spacing pixels. |
| `text_decoration` | string | No | "none" | none/underline/line-through. |
| `opacity` | float | No | 1.0 | Overall shape opacity 0-1. |
| `parent_id` | string | No | — | Parent shape ID (root if omitted). |

---

### `create_path`
Create a vector path shape.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `file_id` | string | Yes | — | The file UUID. |
| `page_id` | string | Yes | — | The page UUID. |
| `segments` | list | Yes | — | Path segments: list of `{command, x, y}` dicts. Commands: M=move, L=line, C=curve (with c1x,c1y,c2x,c2y), Z=close. |
| `name` | string | No | "Path" | Shape name. |
| `fill_color` | string | No | — | Optional fill color hex. |
| `fill_opacity` | float | No | 1.0 | Fill opacity 0-1. |
| `stroke_color` | string | No | "#000000" | Stroke color hex. |
| `stroke_width` | float | No | 1.0 | Stroke width. |
| `opacity` | float | No | 1.0 | Overall opacity 0-1. |
| `parent_id` | string | No | — | Parent shape ID (root if omitted). |

---

### `create_group`
Group existing shapes together.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `file_id` | string | Yes | — | The file UUID. |
| `page_id` | string | Yes | — | The page UUID. |
| `shape_ids` | list | Yes | — | List of shape UUIDs to group. |
| `name` | string | No | "Group" | Group name. |
| `parent_id` | string | No | — | Parent shape ID (root if omitted). |

---

### `create_component`
Convert a shape/frame into a reusable component.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The shape UUID to convert. |
| `name` | string | No | Component name (keeps current name if omitted). |

---

### `create_page`
Add a new page to a file.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `file_id` | string | Yes | — | The file UUID. |
| `name` | string | No | "New Page" | Page name. |

---

## 12. Shape Modification

### `modify_shape`
Modify arbitrary attributes of a shape.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The shape UUID. |
| `attrs` | dict | Yes | Kebab-case attributes to set. E.g. `{"opacity": 0.5, "name": "New Name"}`. |

---

### `move_shape`
Move a shape to a new position.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The shape UUID. |
| `x` | float | Yes | New X position. |
| `y` | float | Yes | New Y position. |

---

### `resize_shape`
Resize a shape.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The shape UUID. |
| `width` | float | Yes | New width in pixels. |
| `height` | float | Yes | New height in pixels. |

---

### `delete_shape`
Delete a shape from a page.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The shape UUID. |

---

### `rename_shape`
Rename a shape.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The shape UUID. |
| `name` | string | Yes | New name. |

---

### `set_fill`
Set the fill color of a shape.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `file_id` | string | Yes | — | The file UUID. |
| `page_id` | string | Yes | — | The page UUID. |
| `shape_id` | string | Yes | — | The shape UUID. |
| `color` | string | No | "#B1B2B5" | Fill color hex. |
| `opacity` | float | No | 1.0 | Fill opacity 0-1. |

---

### `set_stroke`
Set the stroke (border) of a shape.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `file_id` | string | Yes | — | The file UUID. |
| `page_id` | string | Yes | — | The page UUID. |
| `shape_id` | string | Yes | — | The shape UUID. |
| `color` | string | No | "#000000" | Stroke color hex. |
| `width` | float | No | 1.0 | Stroke width pixels. |
| `opacity` | float | No | 1.0 | Stroke opacity 0-1. |
| `style` | string | No | "solid" | solid/dashed/dotted/mixed. |

---

### `set_opacity`
Set the overall opacity of a shape.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The shape UUID. |
| `opacity` | float | Yes | Opacity 0 (transparent) to 1 (opaque). |

---

### `set_layout`
Set flex/grid layout on a frame (auto-layout container).

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `file_id` | string | Yes | — | The file UUID. |
| `page_id` | string | Yes | — | The page UUID. |
| `frame_id` | string | Yes | — | The frame shape UUID. |
| `layout_type` | string | No | "flex" | "flex" or "grid". |
| `direction` | string | No | "row" | row/column/row-reverse/column-reverse. |
| `gap` | float | No | 0 | Gap between children (pixels). |
| `padding` | float | No | 0 | Padding on all sides (pixels). |
| `align_items` | string | No | — | Cross-axis: start/center/end/stretch. |
| `justify_content` | string | No | — | Main-axis: start/center/end/space-between/space-around/space-evenly. |
| `wrap` | string | No | "nowrap" | "nowrap" or "wrap". |

---

### `reorder_shapes`
Reorder shapes within a parent (z-order).

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `file_id` | string | Yes | — | The file UUID. |
| `page_id` | string | Yes | — | The page UUID. |
| `parent_id` | string | Yes | — | Parent shape/frame UUID. |
| `shape_ids` | list | Yes | — | Shape UUIDs to move. |
| `index` | integer | No | 0 | Target index (0=bottom). |

---

### `delete_page`
Delete a page from a file.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |

---

### `rename_page`
Rename a page.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `name` | string | Yes | New name. |

---

## 13. Text Operations

### `set_text_content`
Replace text content of a text shape. Optionally override font properties.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The text shape UUID. |
| `text` | string | Yes | New text content. |
| `font_family` | string | No | Optional font family override. |
| `font_size` | integer | No | Optional font size override. |
| `font_weight` | string | No | Optional font weight override. |
| `fill_color` | string | No | Optional text color override (hex). |
| `text_align` | string | No | Optional alignment override. |

---

### `set_font`
Change the font family of a text shape.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The text shape UUID. |
| `font_family` | string | Yes | Font family name (e.g., "sourcesanspro", "roboto"). |

---

### `set_font_size`
Change the font size of a text shape.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The text shape UUID. |
| `font_size` | integer | Yes | Font size in pixels. |

---

### `set_text_align`
Set text alignment.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The text shape UUID. |
| `align` | string | Yes | "left", "center", "right", or "justify". |

---

### `set_text_style`
Set text styling (bold, italic, underline).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `file_id` | string | Yes | The file UUID. |
| `page_id` | string | Yes | The page UUID. |
| `shape_id` | string | Yes | The text shape UUID. |
| `font_weight` | string | No | "400"=normal, "700"=bold, etc. |
| `font_style` | string | No | "normal" or "italic". |
| `text_decoration` | string | No | "none", "underline", or "line-through". |
