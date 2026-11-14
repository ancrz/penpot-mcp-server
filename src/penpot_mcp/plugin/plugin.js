// Penpot AI Studio & MCP Interactive Bridge — Worker Sandbox (plugin.js)
// Runs inside Penpot's plugin worker context with access to `penpot.*` API.

const PLUGIN_ORIGIN =
  typeof self !== "undefined" &&
  self.location &&
  self.location.origin &&
  self.location.origin !== "null"
    ? self.location.origin
    : "http://localhost:8787";

penpot.ui.open("Penpot AI Studio (MCP Gateway)", PLUGIN_ORIGIN + "/plugin/ui.html", {
  width: 410,
  height: 600,
});

function serializeShape(shape) {
  if (!shape) return null;
  try {
    return {
      id: String(shape.id || ""),
      name: String(shape.name || "Shape"),
      type: String(shape.type || "shape"),
      x: typeof shape.x === "number" ? Math.round(shape.x * 100) / 100 : null,
      y: typeof shape.y === "number" ? Math.round(shape.y * 100) / 100 : null,
      width: typeof shape.width === "number" ? Math.round(shape.width * 100) / 100 : null,
      height: typeof shape.height === "number" ? Math.round(shape.height * 100) / 100 : null,
      fills: Array.isArray(shape.fills) ? shape.fills.slice(0, 4) : undefined,
    };
  } catch (_err) {
    return { id: String(shape.id || ""), name: "Shape", type: "shape" };
  }
}

function collectCanvasContext() {
  const selection = Array.isArray(penpot.selection) ? penpot.selection : [];
  const serializedSelection = selection.slice(0, 100).map(serializeShape).filter(Boolean);
  const ids = serializedSelection.map((s) => s.id);

  let fileId = null;
  let fileName = null;
  let pageId = null;
  let pageName = null;
  let viewport = {};

  try {
    if (penpot.currentFile) {
      fileId = penpot.currentFile.id ? String(penpot.currentFile.id) : null;
      fileName = penpot.currentFile.name ? String(penpot.currentFile.name) : null;
    }
  } catch (_e) {}

  try {
    if (penpot.currentPage) {
      pageId = penpot.currentPage.id ? String(penpot.currentPage.id) : null;
      pageName = penpot.currentPage.name ? String(penpot.currentPage.name) : null;
    }
  } catch (_e) {}

  try {
    if (penpot.viewport) {
      viewport = {
        zoom: penpot.viewport.zoom,
        center: penpot.viewport.center,
      };
    }
  } catch (_e) {}

  return {
    type: "context",
    ids: ids,
    selection: serializedSelection,
    file_id: fileId,
    file_name: fileName,
    page_id: pageId,
    page_name: pageName,
    viewport: viewport,
  };
}

function broadcastContext(commandId) {
  const ctx = collectCanvasContext();
  if (commandId) {
    penpot.ui.sendMessage({
      type: "result",
      command_id: commandId,
      status: "ok",
      result: ctx,
    });
  }
  penpot.ui.sendMessage(ctx);
}

// 1. Listen for canvas events and stream context to UI iframe
let _changeDebounce = null;
function scheduleBroadcast() {
  if (_changeDebounce) clearTimeout(_changeDebounce);
  _changeDebounce = setTimeout(() => {
    broadcastContext(null);
  }, 250);
}

try {
  penpot.on("selectionchange", () => {
    broadcastContext(null);
  });
} catch (_e) {}

try {
  penpot.on("pagechange", () => {
    broadcastContext(null);
  });
} catch (_e) {}

try {
  penpot.on("filechange", () => {
    scheduleBroadcast();
  });
} catch (_e) {}

try {
  penpot.on("shapechange", () => {
    scheduleBroadcast();
  });
} catch (_e) {}

// Initial context broadcast after open
setTimeout(() => {
  broadcastContext(null);
}, 400);

// 2. Listen for commands from ui.html (relayed from PenpotWSController or In-App AI Studio)
penpot.ui.onMessage(async (msg) => {
  if (!msg || typeof msg !== "object") return;

  if (msg.type === "get_context") {
    broadcastContext(msg.command_id || null);
    return;
  }

  if (msg.type === "focus_shapes") {
    try {
      const targetIds = new Set((msg.shape_ids || []).map(String));
      const page = penpot.currentPage;
      const allShapes = page && typeof page.findShapes === "function" ? page.findShapes({}) : [];
      const matched = allShapes.filter((s) => targetIds.has(String(s.id)));
      if (matched.length > 0) {
        penpot.selection = matched;
        if (penpot.viewport && typeof penpot.viewport.zoomIntoView === "function") {
          penpot.viewport.zoomIntoView(matched);
        }
      }
      broadcastContext(msg.command_id || null);
    } catch (err) {
      penpot.ui.sendMessage({
        type: "result",
        command_id: msg.command_id,
        status: "error",
        error: err ? err.toString() : "Focus failed",
      });
    }
    return;
  }

  if (msg.type === "execute") {
    try {
      const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
      const fn = new AsyncFunction("penpot", msg.script);
      const rawResult = await fn(penpot);
      let safeResult = null;
      if (rawResult !== undefined) {
        try {
          safeResult = JSON.parse(JSON.stringify(rawResult));
        } catch (_serErr) {
          safeResult = String(rawResult);
        }
      }
      penpot.ui.sendMessage({
        type: "result",
        command_id: msg.command_id,
        status: "ok",
        result: safeResult,
      });
      broadcastContext(null);
    } catch (err) {
      penpot.ui.sendMessage({
        type: "result",
        command_id: msg.command_id,
        status: "error",
        error: err ? err.toString() : "Unknown execution error",
      });
    }
  }
});
