#!/usr/bin/env python3
"""Penpot MCP Server — E2E Integration Test.
Creates a complete login form UI component from scratch.
"""
import json
import sys
import httpx

BASE = "http://localhost:8787/mcp"
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
req_id = 0

def call(session_id, method, params=None):
    global req_id
    req_id += 1
    h = {**HEADERS}
    if session_id:
        h["Mcp-Session-Id"] = session_id
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params:
        body["params"] = params
    resp = httpx.post(BASE, json=body, headers=h, timeout=60.0)
    for line in resp.text.strip().split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    return json.loads(resp.text)

def tool(sid, name, args=None):
    r = call(sid, "tools/call", {"name": name, "arguments": args or {}})
    if "error" in r:
        return {"error": r["error"]}
    text = r["result"]["content"][0]["text"]
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {"_text": text} if text else {"_text": "(empty)"}

def ok(label, result):
    if isinstance(result, dict) and "error" in result:
        print(f"  FAIL {label}: {result['error']}")
        return False
    print(f"  OK   {label}")
    return True

print("=" * 60)
print("Penpot MCP Server — E2E Integration Test")
print("=" * 60)

# 1. Initialize session
r = call(None, "initialize", {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": {"name": "e2e-test", "version": "1.0"}
})
# Extract session ID from response headers isn't possible here,
# but the streamable HTTP transport includes it
# Let's get it from the response
sid_resp = httpx.post(BASE, json={
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "e2e-test", "version": "1.0"}}
}, headers=HEADERS, timeout=30.0)
sid = sid_resp.headers.get("mcp-session-id", "")
print(f"\n1. Session: {sid[:16]}...")
assert sid, "Failed to get session ID"

# 2. List projects
projects = tool(sid, "list_projects")
ok("list_projects", projects)
team_id = projects[0]["team_id"] if isinstance(projects, list) else None

# 3. Get files
files = tool(sid, "list_files", {"project_id": projects[0]["id"]})
ok("list_files", files)

# 4. Create a new file for the E2E test
new_file = tool(sid, "create_file", {
    "project_id": projects[0]["id"],
    "name": "E2E Test - Login Form"
})
ok("create_file", new_file)
file_id = new_file["id"]
print(f"     File: {file_id}")

# 5. Get pages
pages = tool(sid, "get_file_pages", {"file_id": file_id})
ok("get_file_pages", pages)
page_id = pages[0]["id"]

# 6. Create main frame (Login Card)
frame = tool(sid, "create_frame", {
    "file_id": file_id, "page_id": page_id,
    "name": "Login Card", "x": 100, "y": 100,
    "width": 400, "height": 500
})
ok("create_frame (Login Card)", frame)
frame_id = frame["id"]

# 7. Create header background
header_bg = tool(sid, "create_rectangle", {
    "file_id": file_id, "page_id": page_id,
    "name": "Header BG", "x": 100, "y": 100,
    "width": 400, "height": 120,
    "fill_color": "#4A90D9",
    "frame_id": frame_id
})
ok("create_rectangle (Header BG)", header_bg)

# 8. Create title text
title = tool(sid, "create_text", {
    "file_id": file_id, "page_id": page_id,
    "name": "Title", "x": 150, "y": 140,
    "width": 300, "height": 40,
    "content": "Welcome Back",
    "font_size": 28,
    "fill_color": "#FFFFFF",
    "frame_id": frame_id
})
ok("create_text (Title)", title)

# 9. Create subtitle
subtitle = tool(sid, "create_text", {
    "file_id": file_id, "page_id": page_id,
    "name": "Subtitle", "x": 150, "y": 180,
    "width": 300, "height": 24,
    "content": "Sign in to continue",
    "font_size": 14,
    "fill_color": "#E0E0FF",
    "frame_id": frame_id
})
ok("create_text (Subtitle)", subtitle)

# 10. Email input field
email_bg = tool(sid, "create_rectangle", {
    "file_id": file_id, "page_id": page_id,
    "name": "Email Field", "x": 132, "y": 260,
    "width": 336, "height": 44,
    "fill_color": "#F5F5F5",
    "frame_id": frame_id
})
ok("create_rectangle (Email Field)", email_bg)

email_label = tool(sid, "create_text", {
    "file_id": file_id, "page_id": page_id,
    "name": "Email Label", "x": 132, "y": 238,
    "width": 200, "height": 20,
    "content": "Email",
    "font_size": 12,
    "fill_color": "#666666",
    "frame_id": frame_id
})
ok("create_text (Email Label)", email_label)

# 11. Password input field
pass_bg = tool(sid, "create_rectangle", {
    "file_id": file_id, "page_id": page_id,
    "name": "Password Field", "x": 132, "y": 340,
    "width": 336, "height": 44,
    "fill_color": "#F5F5F5",
    "frame_id": frame_id
})
ok("create_rectangle (Password Field)", pass_bg)

pass_label = tool(sid, "create_text", {
    "file_id": file_id, "page_id": page_id,
    "name": "Password Label", "x": 132, "y": 318,
    "width": 200, "height": 20,
    "content": "Password",
    "font_size": 12,
    "fill_color": "#666666",
    "frame_id": frame_id
})
ok("create_text (Password Label)", pass_label)

# 12. Login button
btn = tool(sid, "create_rectangle", {
    "file_id": file_id, "page_id": page_id,
    "name": "Login Button", "x": 132, "y": 420,
    "width": 336, "height": 48,
    "fill_color": "#4A90D9",
    "frame_id": frame_id
})
ok("create_rectangle (Login Button)", btn)

btn_text = tool(sid, "create_text", {
    "file_id": file_id, "page_id": page_id,
    "name": "Button Text", "x": 230, "y": 432,
    "width": 140, "height": 24,
    "content": "Sign In",
    "font_size": 16,
    "fill_color": "#FFFFFF",
    "frame_id": frame_id
})
ok("create_text (Button Text)", btn_text)

# 13. Add stroke to input fields
for shape_id, name in [(email_bg["id"], "Email"), (pass_bg["id"], "Password")]:
    r = tool(sid, "set_stroke", {
        "file_id": file_id, "page_id": page_id,
        "shape_id": shape_id,
        "color": "#CCCCCC", "width": 1
    })
    ok(f"set_stroke ({name} Field)", r)

# 14. Add corner radius to button
r = tool(sid, "modify_shape", {
    "file_id": file_id, "page_id": page_id,
    "shape_id": btn["id"],
    "properties": {"r1": 6, "r2": 6, "r3": 6, "r4": 6}
})
ok("modify_shape (Button radius)", r)

# 15. Create a forgotten password link text
forgot = tool(sid, "create_text", {
    "file_id": file_id, "page_id": page_id,
    "name": "Forgot Password", "x": 132, "y": 476,
    "width": 336, "height": 20,
    "content": "Forgot your password?",
    "font_size": 12,
    "fill_color": "#4A90D9",
    "frame_id": frame_id
})
ok("create_text (Forgot Password)", forgot)

# 16. Create a snapshot
snap = tool(sid, "create_snapshot", {
    "file_id": file_id,
    "label": "E2E Test - Login Form Complete"
})
ok("create_snapshot", snap)

# 17. Export to SVG
svg = tool(sid, "export_frame_svg", {
    "file_id": file_id, "page_id": page_id,
    "object_id": frame_id
})
has_svg = isinstance(svg, dict) and svg.get("type") == "svg" and len(svg.get("content", "")) > 0
print(f"  {'OK  ' if has_svg else 'FAIL'} export_frame_svg ({len(svg.get('content', ''))} chars)")

# 18. Export to PNG
png = tool(sid, "export_frame_png", {
    "file_id": file_id, "page_id": page_id,
    "object_id": frame_id, "scale": 2.0
})
has_png = isinstance(png, dict) and (png.get("content_base64") or png.get("error"))
if png.get("content_base64"):
    print(f"  OK   export_frame_png ({png.get('size_bytes', '?')} bytes @ 2x)")
else:
    print(f"  WARN export_frame_png (fallback: {png.get('error', 'unknown')[:60]})")

# 19. Read back the shape tree
tree = tool(sid, "get_shape_tree", {
    "file_id": file_id, "page_id": page_id,
    "root_id": frame_id, "depth": 2
})
child_count = len(tree.get("children", [])) if isinstance(tree, dict) else 0
ok(f"get_shape_tree ({child_count} children)", tree)

# 20. Get CSS for the button
css = tool(sid, "get_shape_css", {
    "file_id": file_id, "page_id": page_id,
    "shape_id": btn["id"]
})
ok("get_shape_css (Login Button)", css)

# 21. File summary
summary = tool(sid, "get_file_summary", {"file_id": file_id})
ok("get_file_summary", summary)

# 22. Compare revisions
compare = tool(sid, "compare_revisions", {
    "file_id": file_id, "revn_from": 0
})
ok(f"compare_revisions ({compare.get('change_count', '?')} changes)", compare)

print("\n" + "=" * 60)
print("E2E Test Complete!")
print(f"File: {file_id}")
print(f"Frame: {frame_id}")
print(f"Children: {child_count}")
print("=" * 60)
